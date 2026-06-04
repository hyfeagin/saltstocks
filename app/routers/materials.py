from __future__ import annotations

import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..constants import DEFAULT_CHANNEL, DEFAULT_STATUS
from ..deps import render, get_db
from ..utils import clean_str, normalize_code, get_next_sku, upsert_resale_listing

router = APIRouter()

MATERIAL_UNITS = ["oz", "lb", "g", "kg", "ml", "L", "each", "yd", "ft", "sqft"]


@router.get("/materials", response_class=HTMLResponse)
def materials_list(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT id, sku, name, qty_on_hand, unit, unit_cost, location, notes
        FROM items
        WHERE item_type = 'material'
        ORDER BY name ASC
        """
    ).fetchall()

    recent_runs = conn.execute(
        """
        SELECT
            pr.id,
            pr.qty_produced,
            pr.cost_per_unit,
            pr.notes,
            pr.produced_at,
            i.name AS finished_name,
            i.sku  AS finished_sku
        FROM production_runs pr
        JOIN items i ON i.id = pr.finished_item_id
        ORDER BY pr.produced_at DESC
        LIMIT 15
        """
    ).fetchall()

    return render("materials_list.html", request, rows=rows, recent_runs=recent_runs)


# =========================
# ANCHOR: MATERIALS_NEW_BEGIN
# =========================
@router.get("/materials/new", response_class=HTMLResponse)
def materials_new_form(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    codes = conn.execute(
        "SELECT code, label FROM codes WHERE is_active=1 AND kind='material' ORDER BY code ASC"
    ).fetchall()
    return render("materials_form.html", request, mode="new", item=None, codes=codes, units=MATERIAL_UNITS)


@router.post("/materials/new")
def materials_create(
    company: str = Form("UM"),
    brand_code: str = Form(...),
    name: str = Form(...),
    unit: str = Form("oz"),
    qty_on_hand: str = Form("0"),
    unit_cost: str = Form("0"),
    location: str = Form(""),
    notes: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    company_n = normalize_code(company, "UM")
    code = normalize_code(brand_code, "MISC")
    try:
        qty = float(qty_on_hand) if qty_on_hand.strip() else 0.0
    except ValueError:
        qty = 0.0
    try:
        cost = float(unit_cost) if unit_cost.strip() else 0.0
    except ValueError:
        cost = 0.0

    with conn:
        sku_val = get_next_sku(conn, company_n, code)
        conn.execute(
            """
            INSERT INTO items
              (item_type, company, brand_code, sku, name, unit, qty_on_hand, unit_cost, location, notes)
            VALUES ('material', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company_n, code, sku_val, name.strip(), unit, qty, cost,
             clean_str(location), clean_str(notes)),
        )

    return RedirectResponse(url="/materials", status_code=303)
# =========================
# ANCHOR: MATERIALS_NEW_END
# =========================


# =========================
# ANCHOR: PRODUCE_BEGIN
# (Must be registered before /{item_id}/ routes)
# =========================
@router.get("/materials/produce", response_class=HTMLResponse)
def produce_form(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    materials = conn.execute(
        """
        SELECT id, name, sku, qty_on_hand, unit, unit_cost
        FROM items
        WHERE item_type = 'material'
        ORDER BY name ASC
        """
    ).fetchall()

    resale_items = conn.execute(
        """
        SELECT i.id, i.name, i.sku, i.qty_on_hand
        FROM items i
        WHERE i.item_type = 'resale'
        ORDER BY i.name ASC
        """
    ).fetchall()

    return render("produce_form.html", request, materials=materials, resale_items=resale_items)


@router.post("/materials/produce")
def produce_submit(
    finished_item_id: str = Form(""),
    new_item_name: str = Form(""),
    qty_produced: float = Form(...),
    notes: str = Form(""),
    material_id: List[str] = Form([]),
    qty_used: List[str] = Form([]),
    conn: sqlite3.Connection = Depends(get_db),
):
    # Parse valid material consumption lines
    mat_lines: list[tuple[int, float]] = []
    for mid, qu in zip(material_id, qty_used):
        mid, qu = mid.strip(), qu.strip()
        if mid and qu:
            try:
                mat_lines.append((int(mid), float(qu)))
            except ValueError:
                pass

    # Calculate total material cost and derived cost per unit
    total_cost = 0.0
    for mat_item_id, qty in mat_lines:
        row = conn.execute(
            "SELECT unit_cost FROM items WHERE id=? AND item_type='material'",
            (mat_item_id,),
        ).fetchone()
        if row:
            total_cost += (row["unit_cost"] or 0.0) * qty

    cost_per_unit: Optional[float] = None
    if qty_produced > 0 and total_cost > 0:
        cost_per_unit = round(total_cost / qty_produced, 4)

    with conn:
        fid = finished_item_id.strip()
        if fid and fid.isdigit():
            # Update existing resale item
            item_id = int(fid)
            conn.execute(
                "UPDATE items SET qty_on_hand = qty_on_hand + ? WHERE id=? AND item_type='resale'",
                (qty_produced, item_id),
            )
            if cost_per_unit is not None:
                conn.execute(
                    "UPDATE items SET unit_cost=? WHERE id=? AND item_type='resale'",
                    (cost_per_unit, item_id),
                )
        else:
            # Create new resale item
            name = new_item_name.strip()
            if not name:
                return RedirectResponse(url="/materials/produce", status_code=303)
            sku_val = get_next_sku(conn, "UM", "MISC")
            cur = conn.execute(
                """
                INSERT INTO items
                  (item_type, company, brand_code, sku, name, unit, qty_on_hand, unit_cost, category)
                VALUES ('resale', 'UM', 'MISC', ?, ?, 'each', ?, ?, 'Soap/Bath & Beauty')
                """,
                (sku_val, name, qty_produced, cost_per_unit or 0.0),
            )
            item_id = cur.lastrowid
            upsert_resale_listing(conn, item_id, status=DEFAULT_STATUS, channel=DEFAULT_CHANNEL)

        # Deduct from material stocks
        for mat_item_id, qty in mat_lines:
            conn.execute(
                "UPDATE items SET qty_on_hand = qty_on_hand - ? WHERE id=? AND item_type='material'",
                (qty, mat_item_id),
            )

        # Log the production run
        cur = conn.execute(
            "INSERT INTO production_runs (finished_item_id, qty_produced, cost_per_unit, notes)"
            " VALUES (?, ?, ?, ?)",
            (item_id, qty_produced, cost_per_unit, notes.strip() or None),
        )
        run_id = cur.lastrowid

        for mat_item_id, qty in mat_lines:
            conn.execute(
                "INSERT INTO production_run_materials (run_id, material_item_id, qty_used)"
                " VALUES (?, ?, ?)",
                (run_id, mat_item_id, qty),
            )

    return RedirectResponse(url="/materials", status_code=303)
# =========================
# ANCHOR: PRODUCE_END
# =========================


# =========================
# ANCHOR: MATERIALS_EDIT_BEGIN
# =========================
@router.get("/materials/{item_id}/edit", response_class=HTMLResponse)
def materials_edit_form(request: Request, item_id: int, conn: sqlite3.Connection = Depends(get_db)):
    item = conn.execute(
        "SELECT * FROM items WHERE id=? AND item_type='material'", (item_id,)
    ).fetchone()
    if not item:
        return RedirectResponse(url="/materials", status_code=303)

    codes = conn.execute(
        "SELECT code, label FROM codes WHERE is_active=1 AND kind='material' ORDER BY code ASC"
    ).fetchall()
    return render("materials_form.html", request, mode="edit", item=item, codes=codes, units=MATERIAL_UNITS)


@router.post("/materials/{item_id}/edit")
def materials_update(
    item_id: int,
    company: str = Form("UM"),
    brand_code: str = Form(""),
    name: str = Form(...),
    unit: str = Form("oz"),
    qty_on_hand: str = Form("0"),
    unit_cost: str = Form("0"),
    location: str = Form(""),
    notes: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        qty = float(qty_on_hand) if qty_on_hand.strip() else 0.0
    except ValueError:
        qty = 0.0
    try:
        cost = float(unit_cost) if unit_cost.strip() else 0.0
    except ValueError:
        cost = 0.0

    with conn:
        conn.execute(
            """
            UPDATE items
            SET company=?, brand_code=?, name=?, unit=?, qty_on_hand=?,
                unit_cost=?, location=?, notes=?
            WHERE id=? AND item_type='material'
            """,
            (
                normalize_code(company, "UM"),
                normalize_code(brand_code, "MISC"),
                name.strip(), unit, qty, cost,
                clean_str(location), clean_str(notes),
                item_id,
            ),
        )
    return RedirectResponse(url="/materials", status_code=303)
# =========================
# ANCHOR: MATERIALS_EDIT_END
# =========================


@router.post("/materials/{item_id}/adjust")
def materials_adjust_qty(
    item_id: int,
    delta: float = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    with conn:
        conn.execute(
            "UPDATE items SET qty_on_hand = qty_on_hand + ? WHERE id=? AND item_type='material'",
            (delta, item_id),
        )
    return RedirectResponse(url="/materials", status_code=303)
