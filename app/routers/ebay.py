from __future__ import annotations

import sqlite3
import urllib.parse as _up
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..accounting.posting import JournalLineInput, PostEntryRequest, _insert_post_entry, _validate_post_entry
from ..constants import DEFAULT_ENVIRONMENT
from ..deps import render, get_db
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
    build_auth_url,
    build_iso_date_range,
    exchange_code_for_tokens,
    fetch_orders,
    refresh_access_token,
)

router = APIRouter()


def _post_ebay_revenue_entry(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    line_item_id: str,
    item_name: str,
    sku: Optional[str],
    sale_price: Decimal,
    transaction_fees: Decimal,
    shipping_cost: Decimal,
) -> Optional[int]:
    """Post the revenue/expense entry for an eBay sale line item."""
    if sale_price <= Decimal("0"):
        return None

    account_rows = conn.execute(
        "SELECT code, id FROM accounts WHERE code IN ('1020', '4000', '6050', '6060')"
    ).fetchall()
    account_map = {row["code"]: row["id"] for row in account_rows}
    missing = [c for c in ("1020", "4000", "6050", "6060") if c not in account_map]
    if missing:
        raise ValueError(f"Missing required accounts for eBay revenue entry: {missing}")

    net_payout = (sale_price - transaction_fees - shipping_cost).quantize(Decimal("0.01"))
    label = sku or item_name or f"order {order_id}"
    lines = [
        JournalLineInput(account_id=account_map["4000"], credit=sale_price, memo="eBay sale revenue"),
    ]
    if transaction_fees > Decimal("0"):
        lines.append(JournalLineInput(account_id=account_map["6060"], debit=transaction_fees, memo="eBay transaction fees"))
    if shipping_cost > Decimal("0"):
        lines.append(JournalLineInput(account_id=account_map["6050"], debit=shipping_cost, memo="Shipping label"))
    lines.append(JournalLineInput(account_id=account_map["1020"], debit=net_payout, memo="eBay net payout"))

    req = PostEntryRequest(
        entry_date=date.today(),
        description=f"eBay sale — {label}",
        template_id="EBAY_SALE",
        total_amount=sale_price,
        lines=lines,
        created_by_method="import_ebay",
        notes=f"Auto-generated from eBay order {order_id} line {line_item_id}",
    )
    _validate_post_entry(req)
    return _insert_post_entry(conn, req)


def _post_ebay_cogs_entry(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    line_item_id: str,
    item_id: int,
    item_name: str,
    sku: Optional[str],
    quantity_deducted: Decimal,
    unit_cost: Decimal,
) -> Optional[int]:
    """Post the auto-COGS entry corresponding to an eBay inventory deduction."""
    if quantity_deducted <= Decimal("0") or unit_cost <= Decimal("0"):
        return None

    account_rows = conn.execute(
        "SELECT code, id FROM accounts WHERE code IN ('1200', '5000')"
    ).fetchall()
    account_map = {row["code"]: row["id"] for row in account_rows}
    if "1200" not in account_map or "5000" not in account_map:
        raise ValueError("Missing required accounting accounts for auto COGS (1200 and 5000).")

    cogs_amount = (quantity_deducted * unit_cost).quantize(Decimal("0.01"))
    if cogs_amount <= Decimal("0"):
        return None

    label = sku or item_name or f"item {item_id}"
    req = PostEntryRequest(
        entry_date=date.today(),
        description=f"Auto COGS — eBay import — {label}",
        template_id="COGS_RECOGNITION",
        total_amount=cogs_amount,
        lines=[
            JournalLineInput(
                account_id=account_map["5000"],
                debit=cogs_amount,
                memo="Auto COGS",
                inventory_item_id=item_id,
            ),
            JournalLineInput(
                account_id=account_map["1200"],
                credit=cogs_amount,
                memo="Auto COGS",
                inventory_item_id=item_id,
            ),
        ],
        created_by_method="import_ebay",
        notes=f"Auto-generated from eBay order {order_id} line {line_item_id}",
    )
    _validate_post_entry(req)
    return _insert_post_entry(conn, req)


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
    return render("ebay_import.html", request,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        status_filter=status_filter,
        dry_run=dry_run,
        preview_rows=preview_rows,
        summary=summary,
        error_message=error_message,
        success_message=success_message,
        status_note=status_note,
        missing_credentials=missing_credentials or [],
    )


# =========================
# ANCHOR: EBAY_OAUTH_BEGIN
# (OAuth2 authorization code flow — get/refresh the user refresh token)
# =========================
@router.get("/ebay/oauth/start", response_class=HTMLResponse)
def ebay_oauth_start(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Show the eBay auth URL and a form to paste the redirect URL back."""
    ebay_bundle = get_ebay_profile_bundle(conn)
    profile = ebay_bundle["active_profile"]
    env = ebay_bundle["active_environment"]

    ru_name = (profile.get("ru_name") or "").strip()
    missing = missing_ebay_credentials(profile)
    missing_for_start = [f for f in missing if f != "refresh_token"]

    if missing_for_start or not ru_name:
        missing_fields = missing_for_start + (["ru_name"] if not ru_name else [])
        return render("ebay_oauth_error.html", request,
            error_message=(
                "Cannot start OAuth flow — missing required fields: "
                + ", ".join(missing_fields)
                + ". Save Client ID, Client Secret, and RuName in Settings first."
            ),
            environment=env,
        )

    settings = EbaySettings(
        client_id=profile["client_id"] or "",
        client_secret=profile["client_secret"] or "",
        environment=env,
        refresh_token="",
    )
    try:
        auth_url = build_auth_url(settings, ru_name)
    except EbayIntegrationError as exc:
        return render("ebay_oauth_error.html", request, error_message=str(exc), environment=env)

    return render("ebay_oauth_start.html", request, auth_url=auth_url, environment=env)


@router.post("/ebay/oauth/exchange", response_class=HTMLResponse)
def ebay_oauth_exchange(
    request: Request,
    redirect_url: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Parse the code from the pasted redirect URL and exchange it for tokens."""
    redirect_url = (redirect_url or "").strip()
    if not redirect_url:
        return render("ebay_oauth_error.html", request, error_message="No URL pasted.", environment="")

    # Accept either a full URL or just the raw code.
    # eBay codes contain '#' chars (encoded as %23 in the URL). Browsers often
    # decode %23 → # in the address bar, which makes urlparse treat the rest of
    # the code as a URL fragment and silently drop it. We extract the code with
    # a raw string search so # is treated as part of the value, not a delimiter.
    code = ""
    error = ""
    if redirect_url.startswith("http"):
        try:
            parsed = _up.urlparse(redirect_url)
            qs = _up.parse_qs(parsed.query)
            error = qs.get("error", [""])[0]
        except Exception:
            pass

        idx = redirect_url.find("code=")
        if idx != -1:
            start = idx + len("code=")
            end = redirect_url.find("&", start)
            raw_code = redirect_url[start:] if end == -1 else redirect_url[start:end]
            code = _up.unquote(raw_code)
    else:
        code = _up.unquote(redirect_url)

    if error:
        return render("ebay_oauth_error.html", request, error_message=f"eBay returned an error: {error}", environment="")
    if not code:
        return render("ebay_oauth_error.html", request, error_message="Could not find a code in the pasted URL.", environment="")

    ebay_bundle = get_ebay_profile_bundle(conn)
    profile = ebay_bundle["active_profile"]
    env = ebay_bundle["active_environment"]
    ru_name = (profile.get("ru_name") or "").strip()

    settings = EbaySettings(
        client_id=profile["client_id"] or "",
        client_secret=profile["client_secret"] or "",
        environment=env,
        refresh_token="",
    )
    try:
        _, refresh_token = exchange_code_for_tokens(settings, ru_name, code)
    except EbayIntegrationError as exc:
        return render("ebay_oauth_error.html", request, error_message=str(exc), environment=env)

    with conn:
        conn.execute(
            """
            INSERT INTO ebay_credentials (environment, client_id, client_secret, refresh_token, ru_name, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(environment) DO UPDATE SET
              refresh_token=excluded.refresh_token,
              updated_at=datetime('now')
            """,
            (env, profile["client_id"] or "", profile["client_secret"] or "", refresh_token, ru_name),
        )

    return RedirectResponse("/config?saved_ebay=1&oauth=1#ebay-settings", status_code=303)


@router.get("/ebay/oauth/callback", response_class=HTMLResponse)
def ebay_oauth_callback(
    request: Request,
    code: str = "",
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    """Automatic OAuth2 callback — eBay redirects here after the user authorizes."""
    if error:
        return render("ebay_oauth_error.html", request,
            error_message=f"eBay authorization was denied: {error}", environment="")

    code = (code or "").strip()
    if not code:
        return render("ebay_oauth_error.html", request,
            error_message="No authorization code in the redirect URL. Try connecting again.", environment="")

    ebay_bundle = get_ebay_profile_bundle(conn)
    profile = ebay_bundle["active_profile"]
    env = ebay_bundle["active_environment"]
    ru_name = (profile.get("ru_name") or "").strip()

    settings = EbaySettings(
        client_id=profile["client_id"] or "",
        client_secret=profile["client_secret"] or "",
        environment=env,
        refresh_token="",
    )
    try:
        _, refresh_token = exchange_code_for_tokens(settings, ru_name, code)
    except EbayIntegrationError as exc:
        return render("ebay_oauth_error.html", request, error_message=str(exc), environment=env)

    with conn:
        conn.execute(
            """
            INSERT INTO ebay_credentials (environment, client_id, client_secret, refresh_token, ru_name, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(environment) DO UPDATE SET
              refresh_token=excluded.refresh_token,
              updated_at=datetime('now')
            """,
            (env, profile["client_id"] or "", profile["client_secret"] or "", refresh_token, ru_name),
        )

    return RedirectResponse("/config?saved_ebay=1&oauth=1#ebay-settings", status_code=303)
# =========================
# ANCHOR: EBAY_OAUTH_END
# =========================


# =========================
# ANCHOR: EBAY_IMPORT_BEGIN
# (Preview/apply inventory deductions from eBay orders)
# =========================
@router.get("/ebay/import", response_class=HTMLResponse)
def ebay_import_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    end_date_value = date.today()
    start_date_value = end_date_value - timedelta(days=7)
    ebay_bundle = get_ebay_profile_bundle(conn)
    settings = {
        **ebay_bundle["active_profile"],
        "environment": ebay_bundle["active_environment"],
    }
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
    conn: sqlite3.Connection = Depends(get_db),
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

    ebay_bundle = get_ebay_profile_bundle(conn)
    settings_dict = {
        **ebay_bundle["active_profile"],
        "environment": ebay_bundle["active_environment"],
    }
    missing = missing_ebay_credentials(settings_dict)
    if missing:
        return _ebay_import_response(
            request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
            status_note, [], None,
            "Missing eBay credentials. Configure Settings > eBay first: " + ", ".join(missing),
            None, missing_credentials=missing,
        )

    settings = EbaySettings(
        client_id=settings_dict["client_id"] or "",
        client_secret=settings_dict["client_secret"] or "",
        environment=settings_dict["environment"] or DEFAULT_ENVIRONMENT,
        refresh_token=settings_dict["refresh_token"] or "",
    )

    preview_rows: List[Dict[str, Any]] = []
    success_message = None

    try:
        start_iso, end_iso = build_iso_date_range(start_date_value, end_date_value)
        access_token = refresh_access_token(settings)
        orders = fetch_orders(settings, access_token, start_iso, end_iso, status_filter="ANY")
    except EbayIntegrationError as exc:
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
            sale_price_val = (line.get("lineItemCost") or {}).get("value")
            fee_total = Decimal("0")
            for fee in (line.get("marketplaceFees") or []):
                fv = (fee.get("amount") or {}).get("value")
                if fv is not None:
                    try:
                        fee_total += Decimal(str(fv))
                    except Exception:
                        pass
            ship_val = ((line.get("deliveryCost") or {}).get("shippingCost") or {}).get("value")
            line_candidates.append({
                "order_id": order_id,
                "line_item_id": extract_line_item_id(order_id, line, idx),
                "purchase_date": purchase_date,
                "sku": extract_line_item_sku(line),
                "qty_sold": extract_line_item_qty(line),
                "sale_price": Decimal(str(sale_price_val)).quantize(Decimal("0.01")) if sale_price_val is not None else Decimal("0"),
                "transaction_fees": fee_total.quantize(Decimal("0.01")),
                "shipping_cost": Decimal(str(ship_val)).quantize(Decimal("0.01")) if ship_val is not None else Decimal("0"),
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
            f"SELECT id, sku, name, qty_on_hand, unit_cost FROM items WHERE item_type='resale' AND sku IN ({placeholders}) ORDER BY id ASC",
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
        item_unit_cost = None
        clamped = False

        if item is not None:
            item_id = item["id"]
            item_name = item["name"]
            current_qty = float(item["qty_on_hand"] or 0)
            item_unit_cost = float(item["unit_cost"] or 0)
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
            "item_unit_cost": item_unit_cost,
            "status": status,
            "clamped": clamped,
            "sale_price": candidate["sale_price"],
            "transaction_fees": candidate["transaction_fees"],
            "shipping_cost": candidate["shipping_cost"],
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
                    quantity_deducted = min(
                        Decimal(str(row["qty_sold"] or 0)),
                        Decimal(str(row["current_qty_on_hand"] or 0)),
                    ).quantize(Decimal("0.01"))
                    _post_ebay_cogs_entry(
                        conn,
                        order_id=row["order_id"],
                        line_item_id=row["line_item_id"],
                        item_id=row["item_id"],
                        item_name=row["item_name"],
                        sku=row["sku"],
                        quantity_deducted=quantity_deducted,
                        unit_cost=Decimal(str(row["item_unit_cost"] or 0)).quantize(Decimal("0.01")),
                    )
                    _post_ebay_revenue_entry(
                        conn,
                        order_id=row["order_id"],
                        line_item_id=row["line_item_id"],
                        item_name=row["item_name"],
                        sku=row["sku"],
                        sale_price=Decimal(str(row.get("sale_price") or 0)).quantize(Decimal("0.01")),
                        transaction_fees=Decimal(str(row.get("transaction_fees") or 0)).quantize(Decimal("0.01")),
                        shipping_cost=Decimal(str(row.get("shipping_cost") or 0)).quantize(Decimal("0.01")),
                    )
                    applied_logs += 1
                    applied_updates += 1
            success_message = (
                f"Applied deductions for {applied_updates} line items; "
                f"{applied_logs} import-log rows inserted."
            )
        except sqlite3.Error as exc:
            return _ebay_import_response(
                request, start_date_value, end_date_value, status_filter_value, dry_run_enabled,
                status_note, preview_rows, None,
                f"Apply failed. No deductions were committed: {exc}", None,
            )

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
