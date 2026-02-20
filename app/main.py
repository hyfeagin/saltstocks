from __future__ import annotations

import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import csv
import io
from fastapi import UploadFile, File
from fastapi.responses import StreamingResponse


from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .db import get_conn, init_db, DB_PATH
from .integrations.ebay import (
    EbayIntegrationError,
    EbaySettings,
    build_iso_date_range,
    fetch_orders,
    refresh_access_token,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
BACKUP_DIR = BASE_DIR / "data" / "backups"

app = FastAPI(title="Salt Stocks")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ==========================================================
# ANCHOR INDEX (search "ANCHOR:" to jump)
# - ANCHOR: STARTUP_INIT_DB_BEGIN/END - initialize DB and backups
# - ANCHOR: DASHBOARD_VIEW_BEGIN/END - dashboard counts + summary view
# - ANCHOR: BACKUP_NOW_BEGIN/END - create timestamped DB backup
# - ANCHOR: RESALE_LIST_VIEW_BEGIN/END - list/search resale inventory
# - ANCHOR: RESALE_NEW_FORM_BEGIN/END - resale create form
# - ANCHOR: RESALE_CREATE_DB_WRITE_BEGIN/END - create resale item + listing
# - ANCHOR: CONFIG_CODES_CRUD_BEGIN/END - config codes list/create/update/delete
# - ANCHOR: CONFIG_CATEGORIES_CRUD_BEGIN/END - config categories list/create/update/toggle
# - ANCHOR: RESALE_EDIT_FORM_BEGIN/END - resale edit form
# - ANCHOR: RESALE_UPDATE_DB_WRITE_BEGIN/END - update resale item + listing
# - ANCHOR: RESALE_ADJUST_QTY_BEGIN/END - adjust resale quantity
# - ANCHOR: RESALE_BULK_UPDATE_BEGIN/END - bulk update resale items/listings
# - ANCHOR: RESALE_EXPORT_CSV_BEGIN/END - export resale CSV
# - ANCHOR: RESALE_IMPORT_CSV_BEGIN/END - import resale CSV updates
# ==========================================================

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


def get_ebay_settings(conn) -> Dict[str, Optional[str]]:
    row = conn.execute(
        """
        SELECT client_id, client_secret, environment, refresh_token, updated_at
        FROM ebay_settings
        WHERE id=1
        """
    ).fetchone()
    if not row:
        return {
            "client_id": "",
            "client_secret": "",
            "environment": "SANDBOX",
            "refresh_token": "",
            "updated_at": None,
        }
    return {
        "client_id": row["client_id"] or "",
        "client_secret": row["client_secret"] or "",
        "environment": row["environment"] or "SANDBOX",
        "refresh_token": row["refresh_token"] or "",
        "updated_at": row["updated_at"],
    }


def missing_ebay_credentials(settings: Dict[str, Optional[str]]) -> List[str]:
    missing = []
    for key in ["client_id", "client_secret", "refresh_token"]:
        if not (settings.get(key) or "").strip():
            missing.append(key)
    return missing


def extract_line_item_id(order_id: str, line: Dict[str, Any], line_idx: int) -> str:
    val = (
        line.get("lineItemId")
        or line.get("legacyReference", {}).get("legacyOrderLineItemId")
        or line.get("legacyReferenceId")
    )
    if val:
        return str(val)
    return f"{order_id}-line-{line_idx}"


def extract_line_item_sku(line: Dict[str, Any]) -> str:
    for key in ["sku", "sellerSku", "inventoryReferenceId", "merchantSku"]:
        value = line.get(key)
        if value:
            return str(value).strip()
    return ""


def extract_line_item_qty(line: Dict[str, Any]) -> float:
    for key in ["quantity", "lineItemQuantity", "quantityPurchased"]:
        value = line.get(key)
        if value is None:
            continue
        try:
            qty = float(value)
            return qty if qty > 0 else 0.0
        except (TypeError, ValueError):
            continue
    return 0.0


# =========================
# ANCHOR: STARTUP_INIT_DB_BEGIN
# (Initialize database and backup directory on startup)
# =========================
@app.on_event("startup")
def _startup():
    init_db()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
# =========================
# ANCHOR: STARTUP_INIT_DB_END
# =========================


# =========================
# ANCHOR: DASHBOARD_VIEW_BEGIN
# (Dashboard counts for resale inventory status)
# =========================
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
# =========================
# ANCHOR: DASHBOARD_VIEW_END
# =========================


# =========================
# ANCHOR: BACKUP_NOW_BEGIN
# (Create a timestamped database backup)
# =========================
@app.post("/backup")
def backup_now():
    # Make a timestamped copy of the SQLite file
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"saltstocks_{ts}.db"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, dest)
    return RedirectResponse(url="/", status_code=303)
# =========================
# ANCHOR: BACKUP_NOW_END
# =========================


# =========================
# ANCHOR: RESALE_LIST_VIEW_BEGIN
# (List and search resale inventory)
# =========================
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
# =========================
# ANCHOR: RESALE_LIST_VIEW_END
# =========================


# =========================
# ANCHOR: RESALE_NEW_FORM_BEGIN
# (Render the resale item creation form)
# =========================
@app.get("/resale/new", response_class=HTMLResponse)
def resale_new_form(request: Request):
    conn = get_conn()
    codes = conn.execute(
        "SELECT code, label FROM codes WHERE is_active=1 AND kind IN ('item','material') ORDER BY kind ASC, code ASC"
    ).fetchall()
    categories = conn.execute(
        "SELECT name, is_active FROM categories WHERE is_active=1 ORDER BY name ASC"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(
        "resale_form.html",
        {"request": request, "mode": "new", "item": None, "codes": codes, "categories": categories},
    )
# =========================
# ANCHOR: RESALE_NEW_FORM_END
# =========================


# =========================
# ANCHOR: RESALE_CREATE_DB_WRITE_BEGIN
# (Create resale item and listing rows)
# =========================
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
# =========================
# ANCHOR: RESALE_CREATE_DB_WRITE_END
# =========================

# =========================
# ANCHOR: CONFIG_CODES_CRUD_BEGIN
# (List/create/update/delete config codes)
# =========================
@app.get("/config", response_class=HTMLResponse)
def config_home(request: Request):
    conn = get_conn()
    codes = conn.execute(
        "SELECT * FROM codes ORDER BY kind ASC, code ASC"
    ).fetchall()
    categories = conn.execute(
        "SELECT * FROM categories ORDER BY name ASC"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "codes": codes, "categories": categories},
    )


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
# =========================
# ANCHOR: CONFIG_CODES_CRUD_END
# =========================

# =========================
# ANCHOR: CONFIG_CATEGORIES_CRUD_BEGIN
# (List/create/update/toggle config categories)
# =========================
@app.post("/config/categories/new")
def config_category_create(name: str = Form(...)):
    name_n = (name or "").strip()
    if not name_n:
        return RedirectResponse(url="/config", status_code=303)

    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO categories(name, is_active) VALUES (?, 1)",
            (name_n,),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@app.post("/config/categories/{category_id}/update")
def config_category_update(category_id: int, name: str = Form(...)):
    name_n = (name or "").strip()
    if not name_n:
        return RedirectResponse(url="/config", status_code=303)

    conn = get_conn()
    with conn:
        conn.execute(
            """
            UPDATE categories
            SET name=?, updated_at=datetime('now')
            WHERE id=?
            """,
            (name_n, category_id),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@app.post("/config/categories/{category_id}/toggle")
def config_category_toggle(category_id: int):
    conn = get_conn()
    with conn:
        conn.execute(
            """
            UPDATE categories
            SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END,
                updated_at=datetime('now')
            WHERE id=?
            """,
            (category_id,),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)
# =========================
# ANCHOR: CONFIG_CATEGORIES_CRUD_END
# =========================


# =========================
# ANCHOR: EBAY_SETTINGS_BEGIN
# (Configure local eBay OAuth credentials)
# =========================
@app.get("/settings/ebay", response_class=HTMLResponse)
def ebay_settings_page(request: Request, saved: int = 0):
    conn = get_conn()
    settings = get_ebay_settings(conn)
    conn.close()

    return templates.TemplateResponse(
        "ebay_settings.html",
        {
            "request": request,
            "settings": settings,
            "missing": missing_ebay_credentials(settings),
            "saved": saved == 1,
        },
    )


@app.post("/settings/ebay")
def ebay_settings_save(
    client_id: str = Form(""),
    client_secret: str = Form(""),
    environment: str = Form("SANDBOX"),
    refresh_token: str = Form(""),
):
    env_value = (environment or "SANDBOX").strip().upper()
    if env_value not in {"PRODUCTION", "SANDBOX"}:
        env_value = "SANDBOX"

    conn = get_conn()
    with conn:
        conn.execute(
            """
            INSERT INTO ebay_settings (id, client_id, client_secret, environment, refresh_token, updated_at)
            VALUES (1, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
              client_id=excluded.client_id,
              client_secret=excluded.client_secret,
              environment=excluded.environment,
              refresh_token=excluded.refresh_token,
              updated_at=datetime('now')
            """,
            (
                (client_id or "").strip(),
                (client_secret or "").strip(),
                env_value,
                (refresh_token or "").strip(),
            ),
        )
    conn.close()
    return RedirectResponse(url="/settings/ebay?saved=1", status_code=303)
# =========================
# ANCHOR: EBAY_SETTINGS_END
# =========================


# =========================
# ANCHOR: EBAY_IMPORT_BEGIN
# (Preview/apply inventory deductions from eBay orders)
# =========================
@app.get("/ebay/import", response_class=HTMLResponse)
def ebay_import_page(request: Request):
    end_date_value = date.today()
    start_date_value = end_date_value - timedelta(days=7)
    conn = get_conn()
    settings = get_ebay_settings(conn)
    conn.close()
    return templates.TemplateResponse(
        "ebay_import.html",
        {
            "request": request,
            "start_date": start_date_value.isoformat(),
            "end_date": end_date_value.isoformat(),
            "status_filter": "PAID",
            "dry_run": True,
            "preview_rows": [],
            "summary": None,
            "error_message": None,
            "success_message": None,
            "missing_credentials": missing_ebay_credentials(settings),
        },
    )


@app.post("/ebay/import", response_class=HTMLResponse)
def ebay_import_run(
    request: Request,
    start_date: str = Form(""),
    end_date: str = Form(""),
    status_filter: str = Form("PAID"),
    dry_run: Optional[str] = Form("on"),
    action: str = Form("preview"),
):
    today = date.today()
    default_start = today - timedelta(days=7)

    try:
        start_date_value = date.fromisoformat(start_date) if start_date else default_start
        end_date_value = date.fromisoformat(end_date) if end_date else today
    except ValueError:
        start_date_value = default_start
        end_date_value = today
        return templates.TemplateResponse(
            "ebay_import.html",
            {
                "request": request,
                "start_date": start_date_value.isoformat(),
                "end_date": end_date_value.isoformat(),
                "status_filter": status_filter,
                "dry_run": True,
                "preview_rows": [],
                "summary": None,
                "error_message": "Invalid date format. Use YYYY-MM-DD.",
                "success_message": None,
                "missing_credentials": [],
            },
        )

    if end_date_value < start_date_value:
        return templates.TemplateResponse(
            "ebay_import.html",
            {
                "request": request,
                "start_date": start_date_value.isoformat(),
                "end_date": end_date_value.isoformat(),
                "status_filter": status_filter,
                "dry_run": True,
                "preview_rows": [],
                "summary": None,
                "error_message": "End date must be on or after start date.",
                "success_message": None,
                "missing_credentials": [],
            },
        )

    conn = get_conn()
    settings_dict = get_ebay_settings(conn)
    missing_credentials = missing_ebay_credentials(settings_dict)
    if missing_credentials:
        conn.close()
        return templates.TemplateResponse(
            "ebay_import.html",
            {
                "request": request,
                "start_date": start_date_value.isoformat(),
                "end_date": end_date_value.isoformat(),
                "status_filter": status_filter,
                "dry_run": True,
                "preview_rows": [],
                "summary": None,
                "error_message": (
                    "Missing eBay credentials. Configure Settings > eBay first: "
                    + ", ".join(missing_credentials)
                ),
                "success_message": None,
                "missing_credentials": missing_credentials,
            },
        )

    settings = EbaySettings(
        client_id=settings_dict["client_id"] or "",
        client_secret=settings_dict["client_secret"] or "",
        environment=settings_dict["environment"] or "SANDBOX",
        refresh_token=settings_dict["refresh_token"] or "",
    )

    preview_rows: List[Dict[str, Any]] = []
    success_message = None

    try:
        start_iso, end_iso = build_iso_date_range(start_date_value, end_date_value)
        access_token = refresh_access_token(settings)
        orders = fetch_orders(settings, access_token, start_iso, end_iso, status_filter=status_filter)
    except EbayIntegrationError as exc:
        conn.close()
        return templates.TemplateResponse(
            "ebay_import.html",
            {
                "request": request,
                "start_date": start_date_value.isoformat(),
                "end_date": end_date_value.isoformat(),
                "status_filter": status_filter,
                "dry_run": True,
                "preview_rows": [],
                "summary": None,
                "error_message": str(exc),
                "success_message": None,
                "missing_credentials": [],
            },
        )

    line_candidates: List[Dict[str, Any]] = []
    for order in orders:
        order_id = str(order.get("orderId") or "").strip()
        if not order_id:
            continue
        purchase_date = order.get("creationDate") or order.get("lastModifiedDate") or ""
        line_items = order.get("lineItems") or []
        for idx, line in enumerate(line_items, start=1):
            line_item_id = extract_line_item_id(order_id, line, idx)
            sku = extract_line_item_sku(line)
            qty_sold = extract_line_item_qty(line)
            line_candidates.append(
                {
                    "order_id": order_id,
                    "line_item_id": line_item_id,
                    "purchase_date": purchase_date,
                    "sku": sku,
                    "qty_sold": qty_sold,
                }
            )

    order_ids = sorted({r["order_id"] for r in line_candidates})
    imported_keys = set()
    if order_ids:
        placeholders = ",".join(["?"] * len(order_ids))
        imported_rows = conn.execute(
            f"""
            SELECT order_id, line_item_id
            FROM ebay_import_log
            WHERE order_id IN ({placeholders})
            """,
            tuple(order_ids),
        ).fetchall()
        imported_keys = {(r["order_id"], r["line_item_id"]) for r in imported_rows}

    skus = sorted({r["sku"] for r in line_candidates if r["sku"]})
    items_by_sku: Dict[str, sqlite3.Row] = {}
    if skus:
        placeholders = ",".join(["?"] * len(skus))
        item_rows = conn.execute(
            f"""
            SELECT id, sku, name, qty_on_hand
            FROM items
            WHERE item_type='resale' AND sku IN ({placeholders})
            ORDER BY id ASC
            """,
            tuple(skus),
        ).fetchall()
        for item in item_rows:
            items_by_sku.setdefault(item["sku"], item)

    for candidate in line_candidates:
        key = (candidate["order_id"], candidate["line_item_id"])
        sku = candidate["sku"]
        item = items_by_sku.get(sku) if sku else None

        status = "unmatched"
        item_name = ""
        item_id = None
        current_qty = None
        projected_qty = None
        clamped = False

        if item is not None:
            item_id = item["id"]
            item_name = item["name"]
            current_qty = float(item["qty_on_hand"] or 0)
            projected_qty = max(0.0, current_qty - candidate["qty_sold"])
            clamped = candidate["qty_sold"] > current_qty
            status = "oversold/clamped" if clamped else "matched"

        if key in imported_keys:
            status = "already imported"
            projected_qty = current_qty

        preview_rows.append(
            {
                **candidate,
                "item_id": item_id,
                "item_name": item_name,
                "current_qty_on_hand": current_qty,
                "projected_qty_on_hand": projected_qty,
                "status": status,
                "clamped": clamped,
            }
        )

    applied_updates = 0
    applied_logs = 0
    action_value = (action or "preview").strip().lower()
    should_apply = action_value == "apply" and not (dry_run == "on")
    if action_value == "apply" and dry_run == "on":
        success_message = None
        error_message = "Dry run is enabled. Uncheck 'Dry run / Preview only' to apply deductions."
    else:
        error_message = None

    if should_apply:
        try:
            with conn:
                for row in preview_rows:
                    if row["status"] not in {"matched", "oversold/clamped"}:
                        continue
                    try:
                        conn.execute(
                            """
                            INSERT INTO ebay_import_log (order_id, line_item_id, sku, qty, imported_at, item_id)
                            VALUES (?, ?, ?, ?, datetime('now'), ?)
                            """,
                            (
                                row["order_id"],
                                row["line_item_id"],
                                row["sku"] or None,
                                float(row["qty_sold"] or 0),
                                row["item_id"],
                            ),
                        )
                    except sqlite3.IntegrityError:
                        # Already imported by prior run or race; skip deduction.
                        continue

                    conn.execute(
                        """
                        UPDATE items
                        SET qty_on_hand = CASE
                            WHEN qty_on_hand - ? < 0 THEN 0
                            ELSE qty_on_hand - ?
                        END
                        WHERE id=? AND item_type='resale'
                        """,
                        (row["qty_sold"], row["qty_sold"], row["item_id"]),
                    )
                    applied_logs += 1
                    applied_updates += 1
            success_message = (
                f"Applied deductions for {applied_updates} line items; "
                f"{applied_logs} import-log rows inserted."
            )
        except sqlite3.Error as exc:
            conn.close()
            return templates.TemplateResponse(
                "ebay_import.html",
                {
                    "request": request,
                    "start_date": start_date_value.isoformat(),
                    "end_date": end_date_value.isoformat(),
                    "status_filter": status_filter,
                    "dry_run": False,
                    "preview_rows": preview_rows,
                    "summary": None,
                    "error_message": f"Apply failed. No deductions were committed: {exc}",
                    "success_message": None,
                    "missing_credentials": [],
                },
            )

    conn.close()

    summary = {
        "orders_seen": len(orders),
        "line_items_seen": len(preview_rows),
        "matched": sum(1 for r in preview_rows if r["status"] == "matched"),
        "unmatched": sum(1 for r in preview_rows if r["status"] == "unmatched"),
        "already_imported": sum(1 for r in preview_rows if r["status"] == "already imported"),
        "clamped": sum(1 for r in preview_rows if r["status"] == "oversold/clamped"),
        "applied": applied_updates,
    }

    return templates.TemplateResponse(
        "ebay_import.html",
        {
            "request": request,
            "start_date": start_date_value.isoformat(),
            "end_date": end_date_value.isoformat(),
            "status_filter": status_filter,
            "dry_run": not should_apply,
            "preview_rows": preview_rows,
            "summary": summary,
            "error_message": error_message,
            "success_message": success_message,
            "missing_credentials": [],
        },
    )
# =========================
# ANCHOR: EBAY_IMPORT_END
# =========================


# =========================
# ANCHOR: RESALE_EDIT_FORM_BEGIN
# (Render resale item edit form)
# =========================
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
    categories = conn.execute(
        "SELECT name, is_active FROM categories WHERE is_active=1 OR name=? ORDER BY name ASC",
        (item["category"] if item else "",),
    ).fetchall()

    conn.close()

    if not item:
        return RedirectResponse(url="/resale", status_code=303)

    if item["category"] and not any(c["name"] == item["category"] for c in categories):
        categories = list(categories)
        categories.append({"name": item["category"], "is_active": 0})

    return templates.TemplateResponse(
        "resale_form.html",
        {"request": request, "mode": "edit", "item": item, "codes": codes, "categories": categories},
    )
# =========================
# ANCHOR: RESALE_EDIT_FORM_END
# =========================

# =========================
# ANCHOR: RESALE_UPDATE_DB_WRITE_BEGIN
# (Update resale item and listing rows)
# =========================
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
# =========================
# ANCHOR: RESALE_UPDATE_DB_WRITE_END
# =========================



# =========================
# ANCHOR: RESALE_ADJUST_QTY_BEGIN
# (Adjust resale item quantity on hand)
# =========================
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
# =========================
# ANCHOR: RESALE_ADJUST_QTY_END
# =========================

# =========================
# ANCHOR: RESALE_BULK_UPDATE_BEGIN
# (Bulk update resale items and listings)
# =========================
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
# =========================
# ANCHOR: RESALE_BULK_UPDATE_END
# =========================

# =========================
# ANCHOR: RESALE_EXPORT_CSV_BEGIN
# (Export resale inventory to CSV)
# =========================
@app.get("/resale/export")
def resale_export_csv():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
          i.id,
          i.company,
          i.category,
          i.brand,
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
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id","company","category","brand","brand_code","ip","sku","name","unit",
        "qty_on_hand","unit_cost","location","condition","tags","notes",
        "status","channel","list_price","url"
    ])

    for r in rows:
        writer.writerow([
            r["id"],
            r["company"] or "",
            r["category"] or "",
            r["brand"] or "",
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
# =========================
# ANCHOR: RESALE_EXPORT_CSV_END
# =========================


# =========================
# ANCHOR: RESALE_IMPORT_CSV_BEGIN
# (Import resale inventory updates from CSV)
# =========================
@app.post("/resale/import")
async def resale_import_csv(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("update")  # "update" or "append_tags"
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
    text = content.decode("utf-8-sig")  # handles Excel BOM if present

    reader = csv.DictReader(io.StringIO(text))
    header_set = {h.strip() for h in (reader.fieldnames or []) if h}

    known_item_fields = {
        "company",
        "category",
        "brand",
        "brand_code",
        "ip",
        "sku",
        "name",
        "unit",
        "qty_on_hand",
        "unit_cost",
        "location",
        "condition",
        "tags",
        "notes",
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

    def parse_float(val: Optional[str]) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except ValueError:
            raise ValueError("invalid number")

    errors: List[str] = []
    processed = 0
    created = 0
    updated = 0
    skipped = 0

    def fetch_resale_rows():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT
              i.*,
              COALESCE(rl.status, 'unlisted') AS status,
              COALESCE(rl.channel, 'unassigned') AS channel,
              rl.list_price,
              rl.url
            FROM items i
            LEFT JOIN resale_listings rl ON rl.item_id = i.id
            WHERE i.item_type='resale'
            ORDER BY i.updated_at DESC
            """
        ).fetchall()
        conn.close()
        return rows

    if not reader.fieldnames:
        summary = {
            "processed": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": ["No headers found in CSV."],
        }
        return templates.TemplateResponse(
            "resale_list.html",
            {"request": request, "rows": fetch_resale_rows(), "q": "", "import_summary": summary},
        )

    conn = get_conn()
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
                        "SELECT id FROM items WHERE id=? AND item_type='resale'",
                        (item_id,),
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
                    qty_val = parse_float(qty_raw)
                if "unit_cost" in header_set:
                    unit_cost_val = parse_float(unit_cost_raw)
                if "list_price" in header_set:
                    list_price_val = parse_float(list_price_raw)
            except ValueError:
                row_errors.append("invalid numeric value")

            sku_val = get_value("sku")
            if not is_create and "sku" in header_set and sku_val:
                conflict = conn.execute(
                    "SELECT id FROM items WHERE sku=? AND id<>?",
                    (sku_val, item_id),
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
                    company_val = normalize_code(get_value("company") or "GV")
                    brand_code_val = normalize_code(get_value("brand_code") or "MISC")
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
                            "SELECT id FROM items WHERE sku=?",
                            (sku_val,),
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
                            company_val,
                            category_val,
                            brand_val,
                            brand_code_val,
                            ip_val,
                            sku_val,
                            name_val,
                            unit_val,
                            qty_final,
                            unit_cost_final,
                            location_val,
                            condition_val,
                            tags_val,
                            notes_val,
                        ),
                    )
                    new_id = cur.lastrowid

                    status_val = get_value("status") or "unlisted"
                    channel_val = get_value("channel") or "unassigned"
                    url_val = get_value("url")
                    conn.execute(
                        """
                        INSERT INTO resale_listings (item_id, channel, status, list_price, url)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (new_id, channel_val, status_val, list_price_val, url_val),
                    )
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
                            new_list_price = (
                                list_price_val if list_price_provided else listing_row["list_price"]
                            )
                            new_url = url_val if url_provided else listing_row["url"]

                            conn.execute(
                                """
                                UPDATE resale_listings
                                SET channel=?, status=?, list_price=?, url=?, updated_at=datetime('now')
                                WHERE item_id=?
                                """,
                                (new_channel, new_status, new_list_price, new_url, item_id),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO resale_listings (item_id, channel, status, list_price, url)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (
                                    item_id,
                                    channel_val or "unassigned",
                                    status_val or "unlisted",
                                    list_price_val,
                                    url_val,
                                ),
                            )

                    updated += 1

                conn.execute("RELEASE resale_import_row")
            except (sqlite3.IntegrityError, ValueError) as exc:
                conn.execute("ROLLBACK TO resale_import_row")
                conn.execute("RELEASE resale_import_row")
                skipped += 1
                errors.append(f"Row {row_num}: {exc}")

    conn.close()

    summary = {
        "processed": processed,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }

    return templates.TemplateResponse(
        "resale_list.html",
        {"request": request, "rows": fetch_resale_rows(), "q": "", "import_summary": summary},
    )
# =========================
# ANCHOR: RESALE_IMPORT_CSV_END
# =========================
