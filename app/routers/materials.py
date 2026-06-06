from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..deps import render, get_db
from ..accounting.posting import JournalLineInput, PostEntryRequest, post_entries

router = APIRouter(prefix="/materials", tags=["materials"])

UNITS = ["oz", "lbs", "g", "kg", "ml", "L", "each", "fl oz", "tsp", "tbsp", "cup"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _safe_decimal(val: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(val)).quantize(Decimal("0.0001"))
    except (InvalidOperation, TypeError):
        return Decimal(default)


def _fetch_materials(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, name, unit, qty_on_hand, unit_cost, reorder_point, notes
        FROM items
        WHERE item_type='material'
        ORDER BY name
        """
    ).fetchall()


def _fetch_material(conn: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, name, unit, qty_on_hand, unit_cost, reorder_point, notes
        FROM items WHERE id=? AND item_type='material'
        """,
        (item_id,),
    ).fetchone()


def _fetch_recipes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.id, r.name, r.yield_qty, r.yield_unit, r.description, r.is_active,
               COUNT(rl.id) AS line_count
        FROM recipes r
        LEFT JOIN recipe_lines rl ON rl.recipe_id = r.id
        GROUP BY r.id
        ORDER BY r.name
        """
    ).fetchall()


def _fetch_recipe(conn: sqlite3.Connection, recipe_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM recipes WHERE id=?", (recipe_id,)
    ).fetchone()


def _fetch_recipe_lines(conn: sqlite3.Connection, recipe_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT rl.id, rl.material_id, rl.qty_per_batch, rl.notes,
               i.name AS material_name, i.unit, i.qty_on_hand, i.unit_cost
        FROM recipe_lines rl
        JOIN items i ON i.id = rl.material_id
        WHERE rl.recipe_id = ?
        ORDER BY i.name
        """,
        (recipe_id,),
    ).fetchall()


def _fetch_production_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, recipe_name, batches, finished_item_name, finished_qty,
               total_cogs, run_date, created_at
        FROM production_runs
        ORDER BY run_date DESC, created_at DESC
        LIMIT 100
        """
    ).fetchall()


# ── Materials list ────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def materials_list(request: Request, _json: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db)):
    rows = _fetch_materials(conn)
    if _json:
        return JSONResponse([
            {"id": r["id"], "name": r["name"], "unit": r["unit"],
             "unit_cost": float(r["unit_cost"]), "qty_on_hand": float(r["qty_on_hand"])}
            for r in rows
        ])
    runs = _fetch_production_runs(conn)
    return render(
        "materials/list.html", request,
        materials=rows,
        runs=runs,
    )


# ── Add material ──────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def material_new_form(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return render("materials/form.html", request, item=None, units=UNITS, error=None)


@router.post("/new")
def material_new_submit(
    request: Request,
    name: str = Form(...),
    unit: str = Form(...),
    qty_on_hand: str = Form("0"),
    unit_cost: str = Form("0"),
    reorder_point: str = Form(""),
    notes: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    name = name.strip()
    if not name:
        return render("materials/form.html", request, item=None, units=UNITS, error="Name is required.")
    qty = _safe_decimal(qty_on_hand)
    cost = _safe_decimal(unit_cost)
    rp = _safe_decimal(reorder_point) if reorder_point.strip() else None
    with conn:
        conn.execute(
            """
            INSERT INTO items (item_type, name, unit, qty_on_hand, unit_cost, reorder_point, notes)
            VALUES ('material', ?, ?, ?, ?, ?, ?)
            """,
            (name, unit, float(qty), float(cost), float(rp) if rp is not None else None, notes.strip() or None),
        )
    return RedirectResponse("/materials", status_code=303)


# ── Edit material ─────────────────────────────────────────────────────────────

@router.get("/{item_id}/edit", response_class=HTMLResponse)
def material_edit_form(item_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    item = _fetch_material(conn, item_id)
    if item is None:
        return RedirectResponse("/materials", status_code=303)
    return render("materials/form.html", request, item=item, units=UNITS, error=None)


@router.post("/{item_id}/edit")
def material_edit_submit(
    item_id: int,
    request: Request,
    name: str = Form(...),
    unit: str = Form(...),
    unit_cost: str = Form("0"),
    reorder_point: str = Form(""),
    notes: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    name = name.strip()
    if not name:
        item = _fetch_material(conn, item_id)
        return render("materials/form.html", request, item=item, units=UNITS, error="Name is required.")
    cost = _safe_decimal(unit_cost)
    rp = _safe_decimal(reorder_point) if reorder_point.strip() else None
    with conn:
        conn.execute(
            """
            UPDATE items SET name=?, unit=?, unit_cost=?, reorder_point=?, notes=?
            WHERE id=? AND item_type='material'
            """,
            (name, unit, float(cost), float(rp) if rp is not None else None, notes.strip() or None, item_id),
        )
    return RedirectResponse("/materials", status_code=303)


# ── Qty adjust ────────────────────────────────────────────────────────────────

@router.post("/{item_id}/adjust")
def material_adjust(
    item_id: int,
    delta: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        d = float(delta)
    except (ValueError, TypeError):
        return RedirectResponse("/materials", status_code=303)
    with conn:
        conn.execute(
            """
            UPDATE items
            SET qty_on_hand = MAX(0, qty_on_hand + ?)
            WHERE id=? AND item_type='material'
            """,
            (d, item_id),
        )
    return RedirectResponse("/materials", status_code=303)


# ── Recipes list ──────────────────────────────────────────────────────────────

@router.get("/recipes", response_class=HTMLResponse)
def recipes_list(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    recipes = _fetch_recipes(conn)
    return render("materials/recipes_list.html", request, recipes=recipes)


# ── New recipe ────────────────────────────────────────────────────────────────

@router.get("/recipes/new", response_class=HTMLResponse)
def recipe_new_form(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    materials = _fetch_materials(conn)
    return render("materials/recipe_form.html", request,
                  recipe=None, lines=[], materials=materials, units=UNITS, error=None)


@router.post("/recipes/new")
def recipe_new_submit(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    yield_qty: str = Form("1"),
    yield_unit: str = Form("each"),
    notes: str = Form(""),
    material_ids: list[str] = Form(default=[]),
    qty_per_batches: list[str] = Form(default=[]),
    conn: sqlite3.Connection = Depends(get_db),
):
    name = name.strip()
    if not name:
        materials = _fetch_materials(conn)
        return render("materials/recipe_form.html", request,
                      recipe=None, lines=[], materials=materials, units=UNITS, error="Recipe name is required.")
    yq = max(float(_safe_decimal(yield_qty)), 0.0001)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO recipes (name, description, yield_qty, yield_unit, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, description.strip() or None, yq, yield_unit.strip() or "each", notes.strip() or None),
        )
        recipe_id = cur.lastrowid
        _save_recipe_lines(conn, recipe_id, material_ids, qty_per_batches)
    return RedirectResponse(f"/materials/recipes/{recipe_id}", status_code=303)


# ── View/edit recipe ──────────────────────────────────────────────────────────

@router.get("/recipes/{recipe_id}", response_class=HTMLResponse)
def recipe_detail(recipe_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    recipe = _fetch_recipe(conn, recipe_id)
    if recipe is None:
        return RedirectResponse("/materials/recipes", status_code=303)
    lines = _fetch_recipe_lines(conn, recipe_id)
    materials = _fetch_materials(conn)
    return render("materials/recipe_form.html", request,
                  recipe=recipe, lines=lines, materials=materials, units=UNITS, error=None)


@router.post("/recipes/{recipe_id}")
def recipe_update(
    recipe_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    yield_qty: str = Form("1"),
    yield_unit: str = Form("each"),
    notes: str = Form(""),
    material_ids: list[str] = Form(default=[]),
    qty_per_batches: list[str] = Form(default=[]),
    conn: sqlite3.Connection = Depends(get_db),
):
    name = name.strip()
    if not name:
        recipe = _fetch_recipe(conn, recipe_id)
        lines = _fetch_recipe_lines(conn, recipe_id)
        materials = _fetch_materials(conn)
        return render("materials/recipe_form.html", request,
                      recipe=recipe, lines=lines, materials=materials, units=UNITS,
                      error="Recipe name is required.")
    yq = max(float(_safe_decimal(yield_qty)), 0.0001)
    with conn:
        conn.execute(
            """
            UPDATE recipes SET name=?, description=?, yield_qty=?, yield_unit=?, notes=?,
                               updated_at=datetime('now')
            WHERE id=?
            """,
            (name, description.strip() or None, yq, yield_unit.strip() or "each",
             notes.strip() or None, recipe_id),
        )
        conn.execute("DELETE FROM recipe_lines WHERE recipe_id=?", (recipe_id,))
        _save_recipe_lines(conn, recipe_id, material_ids, qty_per_batches)
    return RedirectResponse(f"/materials/recipes/{recipe_id}", status_code=303)


def _save_recipe_lines(
    conn: sqlite3.Connection,
    recipe_id: int,
    material_ids: list[str],
    qty_per_batches: list[str],
) -> None:
    for mid, qty_str in zip(material_ids, qty_per_batches):
        try:
            mid_int = int(mid)
            qty = float(_safe_decimal(qty_str))
            if qty <= 0:
                continue
        except (ValueError, TypeError):
            continue
        conn.execute(
            "INSERT INTO recipe_lines (recipe_id, material_id, qty_per_batch) VALUES (?,?,?)",
            (recipe_id, mid_int, qty),
        )


# ── Production run ────────────────────────────────────────────────────────────

@router.get("/produce", response_class=HTMLResponse)
def produce_form(
    request: Request,
    recipe_id: Optional[int] = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    recipes = _fetch_recipes(conn)
    selected_recipe = None
    recipe_lines = []
    if recipe_id:
        selected_recipe = _fetch_recipe(conn, recipe_id)
        if selected_recipe:
            recipe_lines = _fetch_recipe_lines(conn, recipe_id)

    finished_items = conn.execute(
        "SELECT id, name, sku, qty_on_hand FROM items WHERE item_type='resale' ORDER BY name"
    ).fetchall()

    return render("materials/produce.html", request,
                  recipes=recipes,
                  selected_recipe=selected_recipe,
                  recipe_lines=recipe_lines,
                  finished_items=finished_items,
                  today=date.today().isoformat(),
                  error=None)


@router.post("/produce")
def produce_submit(
    request: Request,
    recipe_id: str = Form(""),
    recipe_name_override: str = Form(""),
    batches: str = Form("1"),
    finished_item_id: str = Form(""),
    finished_item_name_new: str = Form(""),
    finished_qty: str = Form(""),
    run_date: str = Form(""),
    notes: str = Form(""),
    material_ids: list[str] = Form(default=[]),
    qty_useds: list[str] = Form(default=[]),
    unit_costs: list[str] = Form(default=[]),
    units: list[str] = Form(default=[]),
    conn: sqlite3.Connection = Depends(get_db),
):
    # ── Resolve recipe name ───────────────────────────────────────────────────
    r_id: Optional[int] = int(recipe_id) if recipe_id.strip().isdigit() else None
    if r_id:
        recipe_row = _fetch_recipe(conn, r_id)
        r_name = recipe_row["name"] if recipe_row else (recipe_name_override.strip() or "Ad-hoc run")
    else:
        r_name = recipe_name_override.strip() or "Ad-hoc run"

    n_batches = max(float(_safe_decimal(batches)), 0.0001)
    fqty = max(float(_safe_decimal(finished_qty)), 0) if finished_qty.strip() else 0
    run_date_str = run_date.strip() or date.today().isoformat()

    # ── Build material consumption lines ─────────────────────────────────────
    mat_lines: list[dict] = []
    total_cogs = Decimal("0")
    for mid, qty_str, ucost_str, unit in zip(material_ids, qty_useds, unit_costs, units):
        try:
            mid_int = int(mid)
            qty = float(_safe_decimal(qty_str))
            ucost = _safe_decimal(ucost_str)
            if qty <= 0:
                continue
        except (ValueError, TypeError):
            continue
        line_total = (Decimal(str(qty)) * ucost).quantize(Decimal("0.01"))
        total_cogs += line_total
        mat = conn.execute("SELECT name FROM items WHERE id=?", (mid_int,)).fetchone()
        mat_lines.append({
            "material_id": mid_int,
            "material_name": mat["name"] if mat else f"Material {mid_int}",
            "qty_used": qty,
            "unit": unit or "each",
            "unit_cost": str(ucost),
            "total_cost": str(line_total),
        })

    if not mat_lines:
        recipes = _fetch_recipes(conn)
        finished_items = conn.execute(
            "SELECT id, name, sku, qty_on_hand FROM items WHERE item_type='resale' ORDER BY name"
        ).fetchall()
        selected_recipe = _fetch_recipe(conn, r_id) if r_id else None
        recipe_lines_db = _fetch_recipe_lines(conn, r_id) if r_id else []
        return render("materials/produce.html", request,
                      recipes=recipes, selected_recipe=selected_recipe,
                      recipe_lines=recipe_lines_db, finished_items=finished_items,
                      today=run_date_str, error="Add at least one material used.")

    cost_per_unit = (total_cogs / Decimal(str(fqty))).quantize(Decimal("0.0001")) if fqty > 0 else Decimal("0")

    # ── Look up Inventory account (1200) for journal entry ────────────────────
    inv_acct = conn.execute("SELECT id FROM accounts WHERE code='1200'").fetchone()
    inv_account_id: Optional[int] = inv_acct["id"] if inv_acct else None

    # ── Build journal lines (reclassification within Inventory 1200) ──────────
    # DR 1200: finished goods added to stock
    # CR 1200 ×N: raw materials consumed (one line per ingredient)
    journal_lines: list[JournalLineInput] = []
    if inv_account_id is not None and total_cogs > Decimal("0"):
        finished_label = finished_item_name_new.strip() or (
            conn.execute("SELECT name FROM items WHERE id=?", (int(finished_item_id),)).fetchone()["name"]
            if finished_item_id.strip().isdigit() else "finished goods"
        )
        journal_lines.append(JournalLineInput(
            account_id=inv_account_id,
            debit=total_cogs,
            memo=f"Production: {r_name} → {int(fqty) if fqty == int(fqty) else fqty} {finished_label}",
        ))
        # Credit one line per material so the ledger shows what was consumed
        # Last line absorbs any rounding so debits == credits exactly
        running = Decimal("0")
        for i, ln in enumerate(mat_lines):
            if i < len(mat_lines) - 1:
                amt = Decimal(ln["total_cost"])
                running += amt
            else:
                amt = total_cogs - running  # absorb rounding remainder
            journal_lines.append(JournalLineInput(
                account_id=inv_account_id,
                credit=amt,
                memo=f"Consumed: {ln['material_name']} {ln['qty_used']} {ln['unit']}",
            ))

    # ── Mutable state shared with the after_insert callback ──────────────────
    state: dict = {"f_item_id": int(finished_item_id) if finished_item_id.strip().isdigit() else None,
                   "f_item_name": None}

    def _after_insert(tx_conn: sqlite3.Connection, _entry_ids: list[int]) -> None:
        f_id = state["f_item_id"]

        # Resolve or create finished item
        if f_id:
            row = tx_conn.execute("SELECT name FROM items WHERE id=?", (f_id,)).fetchone()
            if row:
                state["f_item_name"] = row["name"]
                if fqty > 0:
                    tx_conn.execute(
                        "UPDATE items SET qty_on_hand = qty_on_hand + ?, unit_cost = ? WHERE id=?",
                        (fqty, float(cost_per_unit), f_id),
                    )
        elif finished_item_name_new.strip() and fqty > 0:
            state["f_item_name"] = finished_item_name_new.strip()
            cur = tx_conn.execute(
                "INSERT INTO items (item_type, name, qty_on_hand, unit_cost, unit) VALUES ('resale',?,?,?,'each')",
                (state["f_item_name"], fqty, float(cost_per_unit)),
            )
            state["f_item_id"] = cur.lastrowid

        # Deduct material quantities
        for ln in mat_lines:
            tx_conn.execute(
                "UPDATE items SET qty_on_hand = MAX(0, qty_on_hand - ?) WHERE id=?",
                (ln["qty_used"], ln["material_id"]),
            )

        # Record the production run
        cur = tx_conn.execute(
            """
            INSERT INTO production_runs
              (recipe_id, recipe_name, batches, finished_item_id, finished_item_name,
               finished_qty, total_cogs, notes, run_date)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (r_id, r_name, n_batches, state["f_item_id"], state["f_item_name"],
             fqty, str(total_cogs), notes.strip() or None, run_date_str),
        )
        run_id = cur.lastrowid

        tx_conn.executemany(
            """
            INSERT INTO production_run_materials
              (run_id, material_id, material_name, qty_used, unit, unit_cost, total_cost)
            VALUES (?,?,?,?,?,?,?)
            """,
            [(run_id, ln["material_id"], ln["material_name"],
              ln["qty_used"], ln["unit"], ln["unit_cost"], ln["total_cost"])
             for ln in mat_lines],
        )

    # ── Post everything in one atomic transaction ─────────────────────────────
    if journal_lines:
        req = PostEntryRequest(
            entry_date=date.fromisoformat(run_date_str),
            description=f"Production run: {r_name}",
            template_id="PRODUCTION_RUN",
            total_amount=total_cogs,
            lines=journal_lines,
            created_by_method="system_auto",
            notes=notes.strip() or None,
        )
        post_entries(conn, [req], after_insert=_after_insert)
    else:
        # No accounting system set up — still record the run
        with conn:
            _after_insert(conn, [])

    return RedirectResponse("/materials", status_code=303)
