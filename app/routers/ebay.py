from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ..db import get_conn
from ..deps import templates
from ..utils import (
    get_ebay_profile_bundle,
    missing_ebay_credentials,
    extract_line_item_id,
    extract_line_item_sku,
    extract_line_item_qty,
)
from ..integrations.ebay import (
    EbayIntegrationError,
    EbaySettings,
    build_iso_date_range,
    fetch_orders,
    refresh_access_token,
)

router = APIRouter()


def _ebay_import_response(
    request: Request,
    start_date: date,
    end_date: date,
    status_filter: str,
    dry_run: bool,
    status_note: Optional[str],
    preview_rows: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]],
    error_message: Optional[str],
    success_message: Optional[str],
    missing_credentials: Optional[List[str]] = None,
):
    """Shared template response builder for the eBay import page."""
    return templates.TemplateResponse(
        "ebay_import.html",
        {
            "request": request,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "status_filter": status_filter,
            "dry_run": dry_run,
            "preview_rows": preview_rows,
            "summary": summary,
            "error_message": error_message,
            "success_message": success_message,
            "status_note": status_note,
            "missing_credentials": missing_credentials or [],
        },
    )


# =========================
# ANCHOR: EBAY_IMPORT_BEGIN
# (Preview/apply inventory deductions from eBay orders)
# =========================
@router.get("/ebay/import", response_class=HTMLResponse)
def ebay_import_page(request: Request):
    end_date_value = date.today()
    start_date_value = end_date_value - timedelta(days=7)
    conn = get_conn()
    ebay_bundle = get_ebay_profile_bundle(conn)
    settings = {
        **ebay_bundle["active_profile"],
        "environment": ebay_bundle["active_environment"],
    }
    conn.close()
    return _ebay_import_response(
        request,
        start_date=start_date_value,
        end_date=end_date_value,
        status_filter="PAID",
        dry_run=True,
        status_note=None,
        preview_rows=[],
        summary=None,
        error_message=None,
        success_message=None,
        missing_credentials=missing_ebay_credentials(settings),
    )


@router.post("/ebay/import", response_class=HTMLResponse)
def ebay_import_run(
    request: Request,
    start_date: str = Form(""),
    end_date: str = Form(""),
    status_filter: str = Form("PAID"),
    dry_run: str = Form("0"),
    action: str = Form("preview"),
):
    today = date.today()
    default_start = today - timedelta(days=7)

    dry_run_enabled = (dry_run or "0").strip() == "1"
    status_filter_value = (status_filter or "ANY").strip().upper() or "ANY"
    status_note: Optional[str] = None
    if status_filter_value != "ANY":
        status_note = (
            "Status filtering is coming soon for eBay getOrders; this run used date range only."
        )

    try:
        start_date_value = date.fromisoformat(start_date) if start_date else default_start
        end_date_value = date.fromisoformat(end_date) if end_date else today
    except ValueError:
        start_date_value = default_start
        end_date_value = today
        return _ebay_import_response(
            request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
            status_note, [], None, "Invalid date format. Use YYYY-MM-DD.", None,
        )

    if end_date_value < start_date_value:
        return _ebay_import_response(
            request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
            status_note, [], None, "End date must be on or after start date.", None,
        )

    conn = get_conn()
    ebay_bundle = get_ebay_profile_bundle(conn)
    settings_dict = {
        **ebay_bundle["active_profile"],
        "environment": ebay_bundle["active_environment"],
    }
    missing = missing_ebay_credentials(settings_dict)
    if missing:
        conn.close()
        return _ebay_import_response(
            request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
            status_note, [], None,
            "Missing eBay credentials. Configure Settings > eBay first: " + ", ".join(missing),
            None, missing_credentials=missing,
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
        orders = fetch_orders(settings, access_token, start_iso, end_iso, status_filter="ANY")
    except EbayIntegrationError as exc:
        conn.close()
        return _ebay_import_response(
            request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
            status_note, [], None, str(exc), None,
        )

    line_candidates: List[Dict[str, Any]] = []
    for order in orders:
        order_id = str(order.get("orderId") or "").strip()
        if not order_id:
            continue
        purchase_date = order.get("creationDate") or order.get("lastModifiedDate") or ""
        line_items = order.get("lineItems") or []
        for idx, line in enumerate(line_items, start=1):
            line_candidates.append({
                "order_id": order_id,
                "line_item_id": extract_line_item_id(order_id, line, idx),
                "purchase_date": purchase_date,
                "sku": extract_line_item_sku(line),
                "qty_sold": extract_line_item_qty(line),
            })

    order_ids = sorted({r["order_id"] for r in line_candidates})
    imported_keys: set = set()
    if order_ids:
        placeholders = ",".join(["?"] * len(order_ids))
        imported_rows = conn.execute(
            f"SELECT order_id, line_item_id FROM ebay_import_log WHERE order_id IN ({placeholders})",
            tuple(order_ids),
        ).fetchall()
        imported_keys = {(r["order_id"], r["line_item_id"]) for r in imported_rows}

    skus = sorted({r["sku"] for r in line_candidates if r["sku"]})
    items_by_sku: Dict[str, sqlite3.Row] = {}
    if skus:
        placeholders = ",".join(["?"] * len(skus))
        item_rows = conn.execute(
            f"SELECT id, sku, name, qty_on_hand FROM items WHERE item_type='resale' AND sku IN ({placeholders}) ORDER BY id ASC",
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

        preview_rows.append({
            **candidate,
            "item_id": item_id,
            "item_name": item_name,
            "current_qty_on_hand": current_qty,
            "projected_qty_on_hand": projected_qty,
            "status": status,
            "clamped": clamped,
        })

    applied_updates = 0
    applied_logs = 0
    action_value = (action or "preview").strip().lower()
    should_apply = action_value == "apply" and not dry_run_enabled

    if action_value == "apply" and dry_run_enabled:
        error_message: Optional[str] = "Dry run is enabled. Uncheck 'Dry run / Preview only' to apply deductions."
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
            return _ebay_import_response(
                request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
                status_note, preview_rows, None,
                f"Apply failed. No deductions were committed: {exc}", None,
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

    return _ebay_import_response(
        request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
        status_note, preview_rows, summary, error_message, success_message,
    )
# =========================
# ANCHOR: EBAY_IMPORT_END
# =========================
