from __future__ import annotations

import csv
import io
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from ..constants import DEFAULT_CHANNEL, DEFAULT_STATUS, RESALE_STATUSES
from ..deps import render, get_db
from ..utils import clean_str, safe_parse_float, normalize_code, get_next_sku, fetch_resale_rows, fetch_resale_item_by_id, upsert_resale_listing

router = APIRouter()


# =========================
# ANCHOR: RESALE_LIST_VIEW_BEGIN
# (List and search resale inventory)
# =========================
@router.get("/resale", response_class=HTMLResponse)
def resale_list(request: Request, q: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db)):
    rows = fetch_resale_rows(conn, q=q)
    return render("resale_list.html", request, rows=rows, q=q or "", active_resale_tab="inventory")
# =========================
# ANCHOR: RESALE_LIST_VIEW_END
# =========================


# =========================
# ANCHOR: RESALE_EXPORT_CSV_BEGIN
# (Export resale inventory to CSV)
# Must be registered before /{item_id}/edit to avoid path conflict.
# =========================
@router.get("/resale/export")
def resale_export_csv(conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT
          i.id,
          i.company,
          i.category,
          i.brand_code,
          i.ip,
          i.sku,
          i.name,
          i.unit,
          i.qty_on_hand,
          i.unit_cost,
          i.location,
          i.condition,
          i.tags,
          i.notes,
          COALESCE(rl.status,'unlisted') AS status,
          COALESCE(rl.channel,'unassigned') AS channel,
          rl.list_price,
          rl.url
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.item_type='resale'
        ORDER BY i.updated_at DESC
        """
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "company", "category", "brand_code", "ip", "sku", "name", "unit",
        "qty_on_hand", "unit_cost", "location", "condition", "tags", "notes",
        "status", "channel", "list_price", "url",
    ])
    for r in rows:
        writer.writerow([
            r["id"],
            r["company"] or "",
            r["category"] or "",
            r["brand_code"] or "",
            r["ip"] or "",
            r["sku"] or "",
            r["name"] or "",
            r["unit"] or "",
            r["qty_on_hand"],
            r["unit_cost"],
            r["location"] or "",
            r["condition"] or "",
            r["tags"] or "",
            r["notes"] or "",
            r["status"] or DEFAULT_STATUS,
            r["channel"] or DEFAULT_CHANNEL,
            r["list_price"] if r["list_price"] is not None else "",
            r["url"] or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="saltstocks_resale_export.csv"'},
    )
# =========================
# ANCHOR: RESALE_EXPORT_CSV_END
# =========================


# =========================
# ANCHOR: RESALE_BULK_UPDATE_BEGIN
# (Bulk update resale items and listings)
# Must be registered before /{item_id}/edit to avoid path conflict.
# =========================
@router.post("/resale/bulk-update")
def resale_bulk_update(
    item_id: List[int] = Form([]),
    status: str = Form(""),
    channel: str = Form(""),
    location: str = Form(""),
    append_tags: str = Form(""),
    unit_cost: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not item_id:
        return RedirectResponse(url="/resale", status_code=303)

    status = status.strip()
    channel = channel.strip()
    location = location.strip()
    append_tags = append_tags.strip()

    try:
        unit_cost_value: Optional[float] = safe_parse_float(unit_cost)
    except ValueError:
        unit_cost_value = None

    with conn:
        if location:
            conn.executemany(
                "UPDATE items SET location=? WHERE id=? AND item_type='resale'",
                [(location, i) for i in item_id],
            )

        if unit_cost_value is not None:
            conn.executemany(
                "UPDATE items SET unit_cost=? WHERE id=? AND item_type='resale'",
                [(unit_cost_value, i) for i in item_id],
            )

        if append_tags:
            conn.executemany(
                """
                UPDATE items
                SET tags =
                  CASE
                    WHEN tags IS NULL OR TRIM(tags) = '' THEN ?
                    ELSE tags || ', ' || ?
                  END
                WHERE id=? AND item_type='resale'
                """,
                [(append_tags, append_tags, i) for i in item_id],
            )

        if status or channel:
            kwargs = {}
            if status:
                kwargs["status"] = status
            if channel:
                kwargs["channel"] = channel
            for i in item_id:
                upsert_resale_listing(conn, i, **kwargs)

    return RedirectResponse(url="/resale", status_code=303)
# =========================
# ANCHOR: RESALE_BULK_UPDATE_END
# =========================


# =========================
# ANCHOR: RESALE_IMPORT_CSV_BEGIN
# (Import resale inventory updates from CSV)
# Must be registered before /{item_id}/edit to avoid path conflict.
# =========================
@router.post("/resale/import")
async def resale_import_csv(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("update"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    CSV import rules:
    - If id matches an existing item, update that item.
    - If id is blank/NULL, create a new item.
    - If id is provided but not found, skip with error.
    - Unknown columns are ignored, missing columns are allowed.
    - qty_on_hand/unit_cost/list_price parse as floats; invalid values skip the row.
    - Blank qty_on_hand/unit_cost default to 0; blank list_price becomes NULL.
    - SKU rules:
        - If SKU blank for new item, auto-generate from company+brand_code.
        - If SKU provided, must be unique (create) or not conflict (update).
    - Tags behavior:
        - mode="update" replaces tags with CSV value (blank clears).
        - mode="append_tags" appends CSV tags (blank does nothing).
    """
    content = await file.read()
    text = content.decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(text))
    header_set = {h.strip() for h in (reader.fieldnames or []) if h}

    known_item_fields = {
        "company", "category", "brand", "brand_code", "ip", "sku", "name",
        "unit", "qty_on_hand", "unit_cost", "location", "condition", "tags", "notes",
    }
    listing_fields = {"status", "channel", "list_price", "url"}
    known_fields = {"id"} | known_item_fields | listing_fields

    def normalize_empty(val: Optional[str]) -> Optional[str]:
        if val is None:
            return None
        cleaned = str(val).strip()
        if cleaned == "" or cleaned.lower() == "null":
            return None
        return cleaned

    errors: List[str] = []
    processed = 0
    created = 0
    updated = 0
    skipped = 0

    if not reader.fieldnames:
        rows = fetch_resale_rows(conn)
        return render("resale_list.html", request, rows=rows, q="", import_summary={
            "processed": 0, "created": 0, "updated": 0, "skipped": 0,
            "errors": ["No headers found in CSV."],
        }, active_resale_tab="import")

    with conn:
        for row_num, row in enumerate(reader, start=2):
            if not any(normalize_empty(value) for key, value in row.items() if key in known_fields):
                continue
            processed += 1
            row_errors: List[str] = []

            raw_id = normalize_empty(row.get("id"))
            item_id: Optional[int] = None
            is_create = raw_id is None
            if raw_id is not None:
                if not raw_id.isdigit():
                    row_errors.append("id must be a number or blank")
                else:
                    item_id = int(raw_id)
                    existing_item = conn.execute(
                        "SELECT id FROM items WHERE id=? AND item_type='resale'", (item_id,)
                    ).fetchone()
                    if existing_item is None:
                        row_errors.append(f"id {item_id} not found")

            def get_value(field: str) -> Optional[str]:
                if field in header_set:
                    return normalize_empty(row.get(field))
                return None

            name_val = get_value("name")
            if is_create and not name_val:
                row_errors.append("name is required for new items")
            if not is_create and "name" in header_set and not name_val:
                row_errors.append("name cannot be blank when updating")

            qty_raw = get_value("qty_on_hand")
            unit_cost_raw = get_value("unit_cost")
            list_price_raw = get_value("list_price")

            qty_val = None
            unit_cost_val = None
            list_price_val = None

            try:
                if "qty_on_hand" in header_set:
                    qty_val = safe_parse_float(qty_raw)
                if "unit_cost" in header_set:
                    unit_cost_val = safe_parse_float(unit_cost_raw)
                if "list_price" in header_set:
                    list_price_val = safe_parse_float(list_price_raw)
            except ValueError:
                row_errors.append("invalid numeric value")

            sku_val = get_value("sku")
            if not is_create and "sku" in header_set and sku_val:
                conflict = conn.execute(
                    "SELECT id FROM items WHERE sku=? AND id<>?", (sku_val, item_id)
                ).fetchone()
                if conflict:
                    row_errors.append(f"sku {sku_val} is already in use")

            if row_errors:
                skipped += 1
                errors.append(f"Row {row_num}: {', '.join(row_errors)}")
                continue

            try:
                conn.execute("SAVEPOINT resale_import_row")
                if is_create:
                    company_val = normalize_code(get_value("company"), "GV")
                    brand_code_val = normalize_code(get_value("brand_code"), "MISC")
                    brand_val = get_value("brand")
                    category_val = get_value("category")
                    ip_val = get_value("ip")
                    location_val = get_value("location")
                    condition_val = get_value("condition")
                    tags_val = get_value("tags")
                    notes_val = get_value("notes")
                    unit_val = get_value("unit") or "each"

                    if sku_val is None or sku_val == "":
                        sku_val = get_next_sku(conn, company_val, brand_code_val)
                    else:
                        conflict = conn.execute(
                            "SELECT id FROM items WHERE sku=?", (sku_val,)
                        ).fetchone()
                        if conflict:
                            raise ValueError(f"sku {sku_val} is already in use")

                    qty_final = qty_val if qty_val is not None else 0.0
                    unit_cost_final = unit_cost_val if unit_cost_val is not None else 0.0

                    cur = conn.execute(
                        """
                        INSERT INTO items (
                          item_type, company, category, brand, brand_code, ip,
                          sku, name, unit, qty_on_hand, unit_cost,
                          location, condition, tags, notes
                        )
                        VALUES (
                          'resale', ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?,
                          ?, ?, ?, ?
                        )
                        """,
                        (
                            company_val, category_val, brand_val, brand_code_val, ip_val,
                            sku_val, name_val, unit_val, qty_final, unit_cost_final,
                            location_val, condition_val, tags_val, notes_val,
                        ),
                    )
                    new_id = cur.lastrowid
                    status_val = get_value("status") or DEFAULT_STATUS
                    channel_val = get_value("channel") or DEFAULT_CHANNEL
                    url_val = get_value("url")
                    upsert_resale_listing(conn, new_id, status=status_val, channel=channel_val, list_price=list_price_val, url=url_val)
                    created += 1
                else:
                    item_sets = []
                    item_vals: List[Optional[object]] = []

                    field_map = {
                        "company": normalize_code,
                        "category": lambda x: x,
                        "brand": lambda x: x,
                        "brand_code": normalize_code,
                        "ip": lambda x: x,
                        "sku": lambda x: x,
                        "name": lambda x: x,
                        "unit": lambda x: x,
                        "location": lambda x: x,
                        "condition": lambda x: x,
                        "notes": lambda x: x,
                    }

                    for field, transform in field_map.items():
                        if field in header_set:
                            value = get_value(field)
                            item_sets.append(f"{field}=?")
                            item_vals.append(transform(value) if value is not None else None)

                    if "qty_on_hand" in header_set:
                        item_sets.append("qty_on_hand=?")
                        item_vals.append(qty_val if qty_val is not None else 0.0)
                    if "unit_cost" in header_set:
                        item_sets.append("unit_cost=?")
                        item_vals.append(unit_cost_val if unit_cost_val is not None else 0.0)

                    tags_val = get_value("tags") if "tags" in header_set else None
                    if "tags" in header_set and mode == "append_tags":
                        if tags_val:
                            conn.execute(
                                """
                                UPDATE items
                                SET tags =
                                  CASE
                                    WHEN tags IS NULL OR TRIM(tags) = '' THEN ?
                                    ELSE tags || ', ' || ?
                                  END
                                WHERE id=? AND item_type='resale'
                                """,
                                (tags_val, tags_val, item_id),
                            )
                    elif "tags" in header_set:
                        item_sets.append("tags=?")
                        item_vals.append(tags_val)

                    if item_sets:
                        item_vals.append(item_id)
                        conn.execute(
                            f"UPDATE items SET {', '.join(item_sets)} WHERE id=? AND item_type='resale'",
                            tuple(item_vals),
                        )

                    listing_present = any(field in header_set for field in listing_fields)
                    if listing_present:
                        listing_row = conn.execute(
                            "SELECT channel, status, list_price, url FROM resale_listings WHERE item_id=?",
                            (item_id,),
                        ).fetchone()

                        status_val = get_value("status")
                        channel_val = get_value("channel")
                        url_val = get_value("url") if "url" in header_set else None
                        list_price_provided = "list_price" in header_set
                        url_provided = "url" in header_set

                        if listing_row:
                            new_channel = channel_val or listing_row["channel"]
                            new_status = status_val or listing_row["status"]
                            new_list_price = list_price_val if list_price_provided else listing_row["list_price"]
                            new_url = url_val if url_provided else listing_row["url"]
                        else:
                            new_channel = channel_val or DEFAULT_CHANNEL
                            new_status = status_val or DEFAULT_STATUS
                            new_list_price = list_price_val
                            new_url = url_val

                        upsert_resale_listing(conn, item_id, status=new_status, channel=new_channel, list_price=new_list_price, url=new_url)
                    updated += 1

                conn.execute("RELEASE resale_import_row")
            except (sqlite3.IntegrityError, ValueError) as exc:
                conn.execute("ROLLBACK TO resale_import_row")
                conn.execute("RELEASE resale_import_row")
                skipped += 1
                errors.append(f"Row {row_num}: {exc}")

    rows = fetch_resale_rows(conn)
    return render("resale_list.html", request, rows=rows, q="", import_summary={
        "processed": processed,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }, active_resale_tab="import")
# =========================
# ANCHOR: RESALE_IMPORT_CSV_END
# =========================


# =========================
# ANCHOR: RESALE_NEW_FORM_BEGIN
# (Render the resale item creation form)
# =========================
@router.get("/resale/new", response_class=HTMLResponse)
def resale_new_form(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    codes = conn.execute(
        "SELECT code, label FROM codes WHERE is_active=1 AND kind IN ('item','material') ORDER BY kind ASC, code ASC"
    ).fetchall()
    categories = conn.execute(
        "SELECT name, is_active FROM categories WHERE is_active=1 ORDER BY name ASC"
    ).fetchall()
    return render("resale_form.html", request, mode="new", item=None, codes=codes, categories=categories)
# =========================
# ANCHOR: RESALE_NEW_FORM_END
# =========================


# =========================
# ANCHOR: RESALE_CREATE_DB_WRITE_BEGIN
# (Create resale item and listing rows)
# =========================
@router.post("/resale/new")
def resale_create(
    company: str = Form("GV"),
    category: str = Form("Collectibles"),
    brand_code: str = Form(...),
    ip: str = Form(""),
    name: str = Form(...),
    qty_on_hand: float = Form(1),
    unit_cost: float = Form(0),
    location: str = Form(""),
    condition: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    status: str = Form(DEFAULT_STATUS),
    channel: str = Form(DEFAULT_CHANNEL),
    list_price: Optional[float] = Form(None),
    url: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Creates a resale item with auto-generated SKU:
      SKU = {Company}-{BrandCode}-{Sequence}
    SKU is not editable (A+A rule).
    """
    company_n = normalize_code(company, "GV")
    code = normalize_code(brand_code, "MISC")

    with conn:
        sku_final = get_next_sku(conn, company_n, code)
        cur = conn.execute(
            """
            INSERT INTO items (
              item_type, company, category, brand_code, ip,
              sku, name, unit, qty_on_hand, unit_cost,
              location, condition, tags, notes
            )
            VALUES (
              'resale', ?, ?, ?, ?,
              ?, ?, 'each', ?, ?,
              ?, ?, ?, ?
            )
            """,
            (
                company_n,
                clean_str(category),
                code,
                clean_str(ip),
                sku_final,
                name.strip(),
                qty_on_hand,
                unit_cost,
                clean_str(location),
                clean_str(condition),
                clean_str(tags),
                clean_str(notes),
            ),
        )
        item_id = cur.lastrowid
        upsert_resale_listing(conn, item_id, channel=clean_str(channel, DEFAULT_CHANNEL), status=clean_str(status, DEFAULT_STATUS), list_price=list_price, url=clean_str(url))

    return RedirectResponse(url="/resale", status_code=303)
# =========================
# ANCHOR: RESALE_CREATE_DB_WRITE_END
# =========================


# =========================
# ANCHOR: RESALE_EDIT_FORM_BEGIN
# (Render resale item edit form)
# =========================
@router.get("/resale/{item_id}/edit", response_class=HTMLResponse)
def resale_edit_form(request: Request, item_id: int, conn: sqlite3.Connection = Depends(get_db)):
    item = fetch_resale_item_by_id(conn, item_id)
    codes = conn.execute(
        "SELECT code, label FROM codes WHERE is_active=1 AND kind IN ('item','material') ORDER BY kind ASC, code ASC"
    ).fetchall()
    categories = conn.execute(
        "SELECT name, is_active FROM categories WHERE is_active=1 OR name=? ORDER BY name ASC",
        (item["category"] if item else "",),
    ).fetchall()

    if not item:
        return RedirectResponse(url="/resale", status_code=303)

    if item["category"] and not any(c["name"] == item["category"] for c in categories):
        categories = list(categories)
        categories.append({"name": item["category"], "is_active": 0})

    return render("resale_form.html", request, mode="edit", item=item, codes=codes, categories=categories)
# =========================
# ANCHOR: RESALE_EDIT_FORM_END
# =========================


# =========================
# ANCHOR: RESALE_UPDATE_DB_WRITE_BEGIN
# (Update resale item and listing rows)
# =========================
@router.post("/resale/{item_id}/edit")
def resale_update(
    item_id: int,
    company: str = Form("GV"),
    category: str = Form("Collectibles"),
    brand_code: str = Form(""),
    ip: str = Form(""),
    name: str = Form(...),
    qty_on_hand: float = Form(1),
    unit_cost: float = Form(0),
    location: str = Form(""),
    condition: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    status: str = Form(DEFAULT_STATUS),
    channel: str = Form(DEFAULT_CHANNEL),
    list_price: Optional[float] = Form(None),
    url: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Updates an existing resale item.
    SKU remains stable forever (NOT editable / NOT updated here).
    SKU format: {Company}-{BrandCode}-{Sequence} (generated on create only).
    """
    company_n = normalize_code(company, "GV")
    code = normalize_code(brand_code, "MISC")

    with conn:
        existing_item = conn.execute(
            "SELECT id, sku FROM items WHERE id=? AND item_type='resale'", (item_id,)
        ).fetchone()
        if not existing_item:
            return RedirectResponse(url="/resale", status_code=303)

        conn.execute(
            """
            UPDATE items
            SET company=?, category=?, brand_code=?, ip=?, name=?,
                qty_on_hand=?, unit_cost=?, location=?, condition=?, tags=?, notes=?
            WHERE id=? AND item_type='resale'
            """,
            (
                company_n,
                clean_str(category),
                code,
                clean_str(ip),
                name.strip(),
                qty_on_hand,
                unit_cost,
                clean_str(location),
                clean_str(condition),
                clean_str(tags),
                clean_str(notes),
                item_id,
            ),
        )

        channel_v = clean_str(channel, DEFAULT_CHANNEL)
        status_v = clean_str(status, DEFAULT_STATUS)
        url_v = clean_str(url)

        upsert_resale_listing(conn, item_id, channel=channel_v, status=status_v, list_price=list_price, url=url_v)

    return RedirectResponse(url="/resale", status_code=303)
# =========================
# ANCHOR: RESALE_UPDATE_DB_WRITE_END
# =========================


# =========================
# ANCHOR: RESALE_ADJUST_QTY_BEGIN
# (Adjust resale item quantity on hand)
# =========================
@router.post("/resale/{item_id}/adjust")
def resale_adjust_qty(item_id: int, delta: float = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    with conn:
        conn.execute(
            "UPDATE items SET qty_on_hand = qty_on_hand + ? WHERE id = ? AND item_type='resale'",
            (delta, item_id),
        )
    return RedirectResponse(url="/resale", status_code=303)
# =========================
# ANCHOR: RESALE_ADJUST_QTY_END
# =========================


# =========================
# ANCHOR: RESALE_STATUS_UPDATE_BEGIN
# (Update resale listing status for kanban drag/drop)
# =========================
@router.post("/resale/{item_id}/status")
async def resale_update_status(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    status = clean_str(str(payload.get("status", "")))
    if status not in RESALE_STATUSES:
        return JSONResponse({"error": "Invalid status value."}, status_code=400)

    item = conn.execute(
        "SELECT id FROM items WHERE id=? AND item_type='resale'",
        (item_id,),
    ).fetchone()
    if not item:
        return JSONResponse({"error": "Item not found."}, status_code=404)

    with conn:
        upsert_resale_listing(conn, item_id, status=status)

    return JSONResponse({"id": item_id, "status": status}, status_code=200)
# =========================
# ANCHOR: RESALE_STATUS_UPDATE_END
# =========================
