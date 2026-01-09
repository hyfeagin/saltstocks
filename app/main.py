from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import csv
import io
from fastapi import UploadFile, File
from fastapi.responses import StreamingResponse


from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .db import get_conn, init_db, DB_PATH

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
BACKUP_DIR = BASE_DIR / "data" / "backups"

app = FastAPI(title="Salt Stocks")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def normalize_code(s: str) -> str:
    s = (s or "").strip().upper()
    s = s.replace(" ", "").replace("-", "").replace("_", "")
    return s

def get_next_sku(conn, company: str, code: str) -> str:
    company = normalize_code(company)
    code = normalize_code(code)
    if not company or not code:
        raise ValueError("company and code required for SKU generation")

    row = conn.execute(
        "SELECT next_seq FROM sku_counters WHERE company=? AND code=?",
        (company, code),
    ).fetchone()

    if row is None:
        # initialize counter
        next_seq = 1
        conn.execute(
            "INSERT INTO sku_counters(company, code, next_seq) VALUES(?,?,?)",
            (company, code, 2),
        )
    else:
        next_seq = int(row["next_seq"])
        conn.execute(
            "UPDATE sku_counters SET next_seq=? WHERE company=? AND code=?",
            (next_seq + 1, company, code),
        )

    return f"{company}-{code}-{next_seq:06d}"


@app.on_event("startup")
def _startup():
    init_db()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = get_conn()
    resale_count = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE item_type='resale'"
    ).fetchone()["c"]
    unlisted = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.item_type='resale'
          AND COALESCE(rl.status,'unlisted') = 'unlisted'
        """
    ).fetchone()["c"]
    low_qty = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM items
        WHERE item_type='resale' AND qty_on_hand <= 0
        """
    ).fetchone()["c"]
    conn.close()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "resale_count": resale_count, "unlisted": unlisted, "low_qty": low_qty},
    )


@app.post("/backup")
def backup_now():
    # Make a timestamped copy of the SQLite file
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"saltstocks_{ts}.db"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, dest)
    return RedirectResponse(url="/", status_code=303)


@app.get("/resale", response_class=HTMLResponse)
def resale_list(request: Request, q: Optional[str] = None):
    conn = get_conn()
    params = {}
    where = "WHERE i.item_type='resale'"
    if q:
        where += " AND (i.name LIKE :q OR i.sku LIKE :q OR i.tags LIKE :q)"
        params["q"] = f"%{q}%"

    rows = conn.execute(
        f"""
        SELECT
          i.*,
          COALESCE(rl.status, 'unlisted') AS status,
          COALESCE(rl.channel, 'unassigned') AS channel,
          rl.list_price,
          rl.url
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        {where}
        ORDER BY i.updated_at DESC
        """,
        params,
    ).fetchall()
    conn.close()

    return templates.TemplateResponse(
        "resale_list.html",
        {"request": request, "rows": rows, "q": q or ""},
    )


@app.get("/resale/new", response_class=HTMLResponse)
def resale_new_form(request: Request):
    conn = get_conn()
    codes = conn.execute(
        "SELECT code, label FROM codes WHERE is_active=1 AND kind IN ('item','material') ORDER BY kind ASC, code ASC"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(
        "resale_form.html",
        {"request": request, "mode": "new", "item": None, "codes": codes},
    )


@app.post("/resale/new")
def resale_create(
    company: str = Form("GV"),
    category: str = Form("Collectibles"),
    brand_code: str = Form(...),     # REQUIRED - selected from dropdown
    ip: str = Form(""),
    name: str = Form(...),
    qty_on_hand: float = Form(1),
    unit_cost: float = Form(0),
    location: str = Form(""),
    condition: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    status: str = Form("unlisted"),
    channel: str = Form("unassigned"),
    list_price: Optional[float] = Form(None),
    url: str = Form(""),
):
    """
    Creates a resale item with auto-generated SKU:
      SKU = {Company}-{BrandCode}-{Sequence}
    SKU is not editable (A+A rule).
    """
    company_n = normalize_code(company) or "GV"
    code = normalize_code(brand_code) or "MISC"

    conn = get_conn()
    with conn:
        # Always generate SKU on create
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
                category.strip() or None,
                code,
                ip.strip() or None,
                sku_final,
                name.strip(),
                qty_on_hand,
                unit_cost,
                location.strip() or None,
                condition.strip() or None,
                tags.strip() or None,
                notes.strip() or None,
            ),
        )
        item_id = cur.lastrowid

        # Create listing row
        conn.execute(
            """
            INSERT INTO resale_listings (item_id, channel, status, list_price, url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                item_id,
                (channel.strip() or "unassigned"),
                (status.strip() or "unlisted"),
                list_price,
                (url.strip() or None),
            ),
        )

    conn.close()
    return RedirectResponse(url="/resale", status_code=303)

@app.get("/config", response_class=HTMLResponse)
def config_home(request: Request):
    conn = get_conn()
    codes = conn.execute(
        "SELECT * FROM codes ORDER BY kind ASC, code ASC"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("config.html", {"request": request, "codes": codes})


@app.post("/config/codes/new")
def config_code_create(
    code: str = Form(...),
    label: str = Form(...),
    kind: str = Form("item"),
):
    code_n = normalize_code(code)
    label_n = (label or "").strip()
    kind_n = (kind or "item").strip()

    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO codes(code, label, kind, is_active) VALUES (?,?,?,1)",
            (code_n, label_n, kind_n),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@app.post("/config/codes/{code_id}/update")
def config_code_update(
    code_id: int,
    label: str = Form(...),
    kind: str = Form("item"),
    is_active: int = Form(1),
):
    conn = get_conn()
    with conn:
        conn.execute(
            """
            UPDATE codes
            SET label=?, kind=?, is_active=?, updated_at=datetime('now')
            WHERE id=?
            """,
            ((label or "").strip(), (kind or "item").strip(), 1 if int(is_active) == 1 else 0, code_id),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@app.post("/config/codes/{code_id}/delete")
def config_code_delete(code_id: int):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM codes WHERE id=?", (code_id,))
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


    conn = get_conn()
    with conn:
        company_n = normalize_code(company) or "GV"
        code = normalize_code(brand_code) or "MISC"
        # If sku blank, generate it
        sku_final = (sku or "").strip()
        if not sku_final:
            sku_final = get_next_sku(conn, company_n, code)

            cur = conn.execute(
            """
            INSERT INTO items (item_type, company, category, brand_code, ip,
                               sku, name, unit, qty_on_hand, unit_cost, location, condition, tags, notes)
            VALUES ('resale', ?, ?, ?, ?, ?, ?, ?, 'each', ?, ?, ?, ?, ?, ?)
            """,
            (
                company_n,
                category.strip(),
                code,
                (ip.strip() or None),
                sku_final,
                name.strip(),
                qty_on_hand,
                unit_cost,
                location.strip(),
                condition.strip(),
                tags.strip(),
                notes.strip(),
            ),
        )

        item_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO resale_listings (item_id, channel, status, list_price, url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, channel.strip(), status.strip(), list_price, url.strip() or None),
        )
    conn.close()
    return RedirectResponse(url="/resale", status_code=303)


@app.get("/resale/{item_id}/edit", response_class=HTMLResponse)
def resale_edit_form(request: Request, item_id: int):
    conn = get_conn()
    item = conn.execute(
        """
        SELECT
          i.*,
          COALESCE(rl.status, 'unlisted') AS status,
          COALESCE(rl.channel, 'unassigned') AS channel,
          rl.list_price,
          rl.url
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.id = ? AND i.item_type='resale'
        """,
        (item_id,),
    ).fetchone()
    codes = conn.execute(
        "SELECT code, label FROM codes WHERE is_active=1 AND kind IN ('item','material') ORDER BY kind ASC, code ASC"
    ).fetchall()

    conn.close()

    if not item:
        return RedirectResponse(url="/resale", status_code=303)

    return templates.TemplateResponse(
        "resale_form.html",
        {"request": request, "mode": "edit", "item": item, "codes": codes},
    )

@app.post("/resale/{item_id}/edit")
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
    status: str = Form("unlisted"),
    channel: str = Form("unassigned"),
    list_price: Optional[float] = Form(None),
    url: str = Form(""),
):
    """
    Updates an existing resale item.
    SKU remains stable forever (NOT editable / NOT updated here).
    SKU format: {Company}-{BrandCode}-{Sequence} (generated on create only).
    """
    company_n = normalize_code(company) or "GV"
    code = normalize_code(brand_code) or "MISC"

    conn = get_conn()
    with conn:
        existing_item = conn.execute(
            "SELECT id, sku FROM items WHERE id=? AND item_type='resale'",
            (item_id,),
        ).fetchone()
        if not existing_item:
            conn.close()
            return RedirectResponse(url="/resale", status_code=303)

        # Update ITEMS (no sku update, no brand field)
        conn.execute(
            """
            UPDATE items
            SET company=?,
                category=?,
                brand_code=?,
                ip=?,
                name=?,
                qty_on_hand=?,
                unit_cost=?,
                location=?,
                condition=?,
                tags=?,
                notes=?
            WHERE id=? AND item_type='resale'
            """,
            (
                company_n,
                category.strip() or None,
                code,
                ip.strip() or None,
                name.strip(),
                qty_on_hand,
                unit_cost,
                location.strip() or None,
                condition.strip() or None,
                tags.strip() or None,
                notes.strip() or None,
                item_id,
            ),
        )

        # Update or create resale_listings
        existing_listing = conn.execute(
            "SELECT id FROM resale_listings WHERE item_id=?",
            (item_id,),
        ).fetchone()

        channel_v = (channel.strip() or "unassigned")
        status_v = (status.strip() or "unlisted")
        url_v = (url.strip() or None)

        if existing_listing:
            conn.execute(
                """
                UPDATE resale_listings
                SET channel=?,
                    status=?,
                    list_price=?,
                    url=?,
                    updated_at=datetime('now')
                WHERE item_id=?
                """,
                (channel_v, status_v, list_price, url_v, item_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO resale_listings (item_id, channel, status, list_price, url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, channel_v, status_v, list_price, url_v),
            )

    conn.close()
    return RedirectResponse(url="/resale", status_code=303)



@app.post("/resale/{item_id}/adjust")
def resale_adjust_qty(item_id: int, delta: float = Form(...)):
    conn = get_conn()
    with conn:
        conn.execute(
            """
            UPDATE items
            SET qty_on_hand = qty_on_hand + ?
            WHERE id = ? AND item_type='resale'
            """,
            (delta, item_id),
        )
    conn.close()
    return RedirectResponse(url="/resale", status_code=303)

@app.post("/resale/bulk-update")
def resale_bulk_update(
    item_id: List[int] = Form([]),
    status: str = Form(""),
    channel: str = Form(""),
    location: str = Form(""),
    append_tags: str = Form(""),
    unit_cost: str = Form(""),
):
    # If nothing selected, just bounce back
    if not item_id:
        return RedirectResponse(url="/resale", status_code=303)

    status = status.strip()
    channel = channel.strip()
    location = location.strip()
    append_tags = append_tags.strip()

    # unit_cost: allow blank = no change
    unit_cost_value = None
    unit_cost = unit_cost.strip()
    if unit_cost:
        try:
            unit_cost_value = float(unit_cost)
        except ValueError:
            # Ignore invalid input rather than crashing
            unit_cost_value = None

    conn = get_conn()
    with conn:
        # ITEMS table updates
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
            # Append tags. If empty -> set. Else -> add ", newtags"
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

        # resale_listings updates (status/channel)
        if status or channel:
            for i in item_id:
                existing = conn.execute(
                    "SELECT id FROM resale_listings WHERE item_id=?",
                    (i,),
                ).fetchone()

                if existing:
                    if status and channel:
                        conn.execute(
                            """UPDATE resale_listings
                               SET status=?, channel=?, updated_at=datetime('now')
                               WHERE item_id=?""",
                            (status, channel, i),
                        )
                    elif status:
                        conn.execute(
                            """UPDATE resale_listings
                               SET status=?, updated_at=datetime('now')
                               WHERE item_id=?""",
                            (status, i),
                        )
                    elif channel:
                        conn.execute(
                            """UPDATE resale_listings
                               SET channel=?, updated_at=datetime('now')
                               WHERE item_id=?""",
                            (channel, i),
                        )
                else:
                    conn.execute(
                        """INSERT INTO resale_listings (item_id, channel, status)
                           VALUES (?, ?, ?)""",
                        (i, channel or "unassigned", status or "unlisted"),
                    )

    conn.close()
    return RedirectResponse(url="/resale", status_code=303)

@app.get("/resale/export")
def resale_export_csv():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
          i.id,
          i.sku,
          i.name,
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
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id","sku","name","qty_on_hand","unit_cost","location","condition","tags","notes",
        "status","channel","list_price","url"
    ])

    for r in rows:
        writer.writerow([
            r["id"],
            r["sku"] or "",
            r["name"] or "",
            r["qty_on_hand"],
            r["unit_cost"],
            r["location"] or "",
            r["condition"] or "",
            r["tags"] or "",
            r["notes"] or "",
            r["status"] or "unlisted",
            r["channel"] or "unassigned",
            r["list_price"] if r["list_price"] is not None else "",
            r["url"] or "",
        ])

    output.seek(0)
    filename = f"saltstocks_resale_export.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/resale/import")
async def resale_import_csv(
    file: UploadFile = File(...),
    mode: str = Form("update")  # "update" or "append_tags"
):
    """
    V1 Guardrails:
    - Matches rows by 'id' (required)
    - Updates allowed fields:
        sku, name, unit_cost, location, condition, notes, status, channel, list_price, url
    - Tags behavior:
        - mode="update" replaces tags with CSV value
        - mode="append_tags" appends CSV tags to existing tags
    - Ignores qty_on_hand on import (protects your counts)
    """
    content = await file.read()
    text = content.decode("utf-8-sig")  # handles Excel BOM if present

    reader = csv.DictReader(io.StringIO(text))
    required = {"id"}
    if not required.issubset(set(reader.fieldnames or [])):
        return RedirectResponse(url="/resale", status_code=303)

    def _to_float(val: str):
        val = (val or "").strip()
        if val == "":
            return None
        try:
            return float(val)
        except ValueError:
            return None

    conn = get_conn()
    updated = 0

    with conn:
        for row in reader:
            rid = (row.get("id") or "").strip()
            if not rid.isdigit():
                continue
            item_id = int(rid)

            # Pull allowed fields (blank means "no change" for most fields)
            sku = (row.get("sku") or "").strip()
            name = (row.get("name") or "").strip()
            location = (row.get("location") or "").strip()
            condition = (row.get("condition") or "").strip()
            notes = (row.get("notes") or "").strip()
            tags = (row.get("tags") or "").strip()

            status = (row.get("status") or "").strip()
            channel = (row.get("channel") or "").strip()

            unit_cost_val = _to_float(row.get("unit_cost") or "")
            list_price_val = _to_float(row.get("list_price") or "")
            url = (row.get("url") or "").strip()

            # Update items table (only apply non-blank values; unit_cost applies if numeric)
            sets = []
            vals = []

            if sku != "":
                sets.append("sku=?")
                vals.append(sku or None)
            if name != "":
                sets.append("name=?")
                vals.append(name)
            if location != "":
                sets.append("location=?")
                vals.append(location)
            if condition != "":
                sets.append("condition=?")
                vals.append(condition)
            if notes != "":
                sets.append("notes=?")
                vals.append(notes)

            if unit_cost_val is not None:
                sets.append("unit_cost=?")
                vals.append(unit_cost_val)

            if tags != "":
                if mode == "append_tags":
                    # append tags
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
                        (tags, tags, item_id),
                    )
                else:
                    # replace tags
                    sets.append("tags=?")
                    vals.append(tags)

            if sets:
                vals.append(item_id)
                conn.execute(
                    f"UPDATE items SET {', '.join(sets)} WHERE id=? AND item_type='resale'",
                    tuple(vals),
                )

            # Update listing fields (status/channel/list_price/url)
            if status or channel or (list_price_val is not None) or (url != ""):
                existing = conn.execute(
                    "SELECT id FROM resale_listings WHERE item_id=?",
                    (item_id,),
                ).fetchone()

                # default fallbacks if creating
                c = channel or "unassigned"
                s = status or "unlisted"

                if existing:
                    # only update provided fields; keep existing if blank
                    # easiest: fetch current then write back merged
                    cur = conn.execute(
                        "SELECT channel, status, list_price, url FROM resale_listings WHERE item_id=?",
                        (item_id,),
                    ).fetchone()
                    new_channel = channel or cur["channel"]
                    new_status = status or cur["status"]
                    new_list_price = list_price_val if list_price_val is not None else cur["list_price"]
                    new_url = url or (cur["url"] or "")

                    conn.execute(
                        """
                        UPDATE resale_listings
                        SET channel=?, status=?, list_price=?, url=?, updated_at=datetime('now')
                        WHERE item_id=?
                        """,
                        (new_channel, new_status, new_list_price, new_url or None, item_id),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO resale_listings (item_id, channel, status, list_price, url)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (item_id, c, s, list_price_val, url or None),
                    )

            updated += 1

    conn.close()
    return RedirectResponse(url="/resale", status_code=303)

