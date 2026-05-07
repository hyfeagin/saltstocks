from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from io import StringIO
from io import BytesIO
from dataclasses import asdict, replace
from datetime import date as date_type, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from urllib.parse import quote
import csv
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app.deps import BASE_DIR, get_db, render
from app.db import DB_PATH
from app.accounting.catalog import CATALOG, get_template, resolve_lines
from app.accounting.exceptions import EmptyEntryError, UnbalancedEntryError, VoidedEntryError
from app.accounting.posting import (
    JournalLineInput,
    LineItem as InventoryCostLineItem,
    PostEntryRequest,
    allocate_freight_in,
    post_entries,
    post_entry,
    void_entry,
)
from app.accounting.questionnaire import (
    QuestionnaireSession,
    build_answer_set,
    go_back,
    is_complete,
    next_visible_step,
    record_answer,
    step_progress,
)
from app.accounting.reports import (
    balance_sheet,
    expenses_by_category,
    general_ledger,
    profit_and_loss,
    receipt_index,
    sales_tax_summary,
)
from app.accounting.schemas import TransactionAnswerSet
from app.utils import get_next_sku

router = APIRouter()

_ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
_TYPE_LABELS = {
    "asset":     "Assets",
    "liability": "Liabilities",
    "equity":    "Equity",
    "income":    "Income",
    "expense":   "Expenses",
}

_PAYMENT_ACCOUNT_FILTER_BY_TEMPLATE = {
    "BANK_FEE": "bank",
    "PAYMENT_PROCESSING_FEE": "bank,cash",
    "OWNER_CONTRIBUTION": "bank,cash",
    "OWNER_DRAW": "bank,cash",
    "PAY_CREDIT_CARD": "bank",
    "SALES_TAX_REMITTED": "bank",
}

_DEFAULT_NEW_RESALE_COMPANY = "GV"
_DEFAULT_NEW_RESALE_CODE = "MISC"


def _csv_response(filename: str, rows: list[list[object]]) -> Response:
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _pnl_csv_rows(report: dict) -> list[list[object]]:
    rows: list[list[object]] = [
        ["Profit & Loss"],
        ["From", report["from_date"]],
        ["To", report["to_date"]],
        [],
        ["Revenue"],
    ]
    for line in report["revenue"]:
        rows.append([line.account_code, line.account_name, f"{line.amount:.2f}"])
    rows.append(["", "Total Revenue", f"{report['total_revenue']:.2f}"])
    rows.append([])
    rows.append(["Cost of Goods Sold"])
    for line in report["cogs"]:
        rows.append([line.account_code, line.account_name, f"{line.amount:.2f}"])
    rows.append(["", "Total COGS", f"{report['total_cogs']:.2f}"])
    rows.append(["", "Gross Profit", f"{report['gross_profit']:.2f}"])
    rows.append([])
    rows.append(["Operating Expenses"])
    for line in report["operating_expenses"]:
        rows.append([line.account_code, line.account_name, f"{line.amount:.2f}"])
    rows.append(["", "Total Expenses", f"{report['total_expenses']:.2f}"])
    rows.append(["", "Net Income", f"{report['net_income']:.2f}"])
    return rows


def _balance_sheet_csv_rows(report: dict) -> list[list[object]]:
    rows: list[list[object]] = [
        ["Balance Sheet"],
        ["As of", report["as_of"]],
        [],
        ["Assets"],
    ]
    for line in report["assets"]:
        rows.append([line.account_code, line.account_name, f"{line.amount:.2f}"])
    rows.append(["", "Total Assets", f"{report['total_assets']:.2f}"])
    rows.append([])
    rows.append(["Liabilities"])
    for line in report["liabilities"]:
        rows.append([line.account_code, line.account_name, f"{line.amount:.2f}"])
    rows.append(["", "Total Liabilities", f"{report['total_liabilities']:.2f}"])
    rows.append([])
    rows.append(["Equity"])
    for line in report["equity"]:
        rows.append([line.account_code, line.account_name, f"{line.amount:.2f}"])
    rows.append(["", "Total Equity", f"{report['total_equity']:.2f}"])
    rows.append(["", "Total Liabilities + Equity", f"{report['total_liabilities_and_equity']:.2f}"])
    return rows


def _expenses_by_category_csv_rows(report: dict) -> list[list[object]]:
    rows: list[list[object]] = [
        ["Expenses by Category"],
        ["From", report["from_date"]],
        ["To", report["to_date"]],
        [],
        ["Account Code", "Category", "Amount"],
    ]
    for line in report["categories"]:
        rows.append([line.account_code, line.account_name, f"{line.amount:.2f}"])
    rows.append(["", "Total Expenses", f"{report['total_expenses']:.2f}"])
    return rows


def _sales_tax_csv_rows(report: dict) -> list[list[object]]:
    return [
        ["Sales Tax Summary"],
        ["From", report["from_date"]],
        ["To", report["to_date"]],
        [],
        ["Metric", "Amount"],
        ["Collected", f"{report['collected']:.2f}"],
        ["Remitted", f"{report['remitted']:.2f}"],
        ["Net Liability", f"{report['net_liability']:.2f}"],
    ]


def _ledger_csv_rows(report: dict) -> list[list[object]]:
    rows: list[list[object]] = [
        ["General Ledger / Account Activity"],
        ["Account", f"{report['account_code']} - {report['account_name']}"],
        ["From", report["from_date"] or ""],
        ["To", report["to_date"] or ""],
        ["Opening Balance", f"{report['opening_balance']:.2f}"],
        [],
        ["Date", "Entry ID", "Template", "Description", "Memo", "Debit", "Credit", "Running Balance"],
    ]
    for line in report["lines"]:
        rows.append(
            [
                line.entry_date,
                line.entry_id,
                line.template_id,
                line.description,
                line.memo or "",
                f"{line.debit:.2f}",
                f"{line.credit:.2f}",
                f"{line.running_balance:.2f}",
            ]
        )
    rows.append([])
    rows.append(["", "", "", "", "Closing Balance", "", "", f"{report['closing_balance']:.2f}"])
    return rows


def _all_ledgers_csv_rows(conn: sqlite3.Connection, *, year: int) -> list[list[object]]:
    from_date = f"{year}-01-01"
    to_date = f"{year}-12-31"
    rows: list[list[object]] = [["General Ledger"], ["From", from_date], ["To", to_date], []]
    account_rows = conn.execute(
        """
        SELECT DISTINCT a.id, a.code, a.name
        FROM accounts a
        JOIN journal_lines jl ON jl.account_id = a.id
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE je.is_void = 0
          AND je.entry_date >= ?
          AND je.entry_date <= ?
        ORDER BY a.code
        """,
        (from_date, to_date),
    ).fetchall()

    for acct in account_rows:
        report = general_ledger(
            conn,
            account_id=acct["id"],
            from_date=from_date,
            to_date=to_date,
        )
        rows.append([f"{report['account_code']} - {report['account_name']}"])
        rows.extend(_ledger_csv_rows(report))
        rows.append([])
    return rows


def _build_year_end_export_zip(
    conn: sqlite3.Connection,
    *,
    year: int,
    base_dir: Path,
    db_path: Path,
) -> bytes:
    from_date = f"{year}-01-01"
    to_date = f"{year}-12-31"

    pnl_report = profit_and_loss(conn, from_date=from_date, to_date=to_date)
    expense_report = expenses_by_category(conn, from_date=from_date, to_date=to_date)
    ledger_rows = _all_ledgers_csv_rows(conn, year=year)

    receipt_rows = conn.execute(
        """
        SELECT r.id, r.original_filename, r.stored_path, je.entry_date
        FROM receipts r
        JOIN journal_entries je ON je.id = r.entry_id
        WHERE je.is_void = 0
          AND je.entry_date >= ?
          AND je.entry_date <= ?
        ORDER BY je.entry_date, r.id
        """,
        (from_date, to_date),
    ).fetchall()

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"reports/profit-and-loss_{year}.csv", _csv_text(_pnl_csv_rows(pnl_report)))
        zf.writestr(
            f"reports/expenses-by-category_{year}.csv",
            _csv_text(_expenses_by_category_csv_rows(expense_report)),
        )
        zf.writestr(f"reports/general-ledger_{year}.csv", _csv_text(ledger_rows))

        for row in receipt_rows:
            month = row["entry_date"][5:7]
            source_path = base_dir / row["stored_path"]
            if not source_path.exists():
                continue
            safe_name = f"{row['id']}_{row['original_filename']}"
            zf.write(source_path, arcname=f"receipts/{month}/{safe_name}")

        if db_path.exists():
            zf.write(db_path, arcname=f"database/saltstocks_{year}.db")

    return archive.getvalue()


def _csv_text(rows: list[list[object]]) -> str:
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue()


def _owner_reimbursement_balance(conn: sqlite3.Connection) -> Decimal:
    """Return how much the business currently owes the owner back."""
    rows = conn.execute(
        """
        SELECT a.code AS account_code, jl.debit, jl.credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE je.is_void = 0
          AND a.code IN ('3100', '3200')
        """
    ).fetchall()

    contributions = Decimal("0")
    draws = Decimal("0")
    for row in rows:
        debit = Decimal(row["debit"])
        credit = Decimal(row["credit"])
        if row["account_code"] == "3100":
            contributions += credit - debit
        elif row["account_code"] == "3200":
            draws += debit - credit

    return (contributions - draws).quantize(Decimal("0.01"))


def _validate_reimburse_owner_amount(
    conn: sqlite3.Connection,
    amount: Decimal,
) -> Decimal:
    """Validate a reimbursement amount against the current owner balance."""
    balance = _owner_reimbursement_balance(conn)
    if amount <= Decimal("0"):
        raise ValueError("Reimbursement amount must be greater than $0.00.")
    if balance <= Decimal("0"):
        raise ValueError("There is no owner reimbursement balance available right now.")
    if amount > balance:
        raise ValueError(
            f"Reimbursement amount cannot exceed the current owed balance of ${balance:.2f}."
        )
    return balance


def _owner_balance_detail(conn: sqlite3.Connection) -> dict:
    """Return contribution/draw detail lists with running totals for owner balance."""
    rows = conn.execute(
        """
        SELECT
          je.id AS entry_id,
          je.entry_date,
          je.description,
          je.template_id,
          a.code AS account_code,
          jl.debit,
          jl.credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE je.is_void = 0
          AND a.code IN ('3100', '3200')
        ORDER BY je.entry_date, je.id, jl.id
        """
    ).fetchall()

    contributions: list[dict] = []
    draws: list[dict] = []
    contributions_total = Decimal("0.00")
    draws_total = Decimal("0.00")

    for row in rows:
        debit = Decimal(str(row["debit"] or "0")).quantize(Decimal("0.01"))
        credit = Decimal(str(row["credit"] or "0")).quantize(Decimal("0.01"))
        if row["account_code"] == "3100":
            amount = (credit - debit).quantize(Decimal("0.01"))
            if amount == Decimal("0.00"):
                continue
            contributions_total = (contributions_total + amount).quantize(Decimal("0.01"))
            contributions.append(
                {
                    "entry_id": row["entry_id"],
                    "entry_date": row["entry_date"],
                    "description": row["description"],
                    "template_id": row["template_id"],
                    "amount": amount,
                    "running_total": contributions_total,
                }
            )
        elif row["account_code"] == "3200":
            amount = (debit - credit).quantize(Decimal("0.01"))
            if amount == Decimal("0.00"):
                continue
            draws_total = (draws_total + amount).quantize(Decimal("0.01"))
            draws.append(
                {
                    "entry_id": row["entry_id"],
                    "entry_date": row["entry_date"],
                    "description": row["description"],
                    "template_id": row["template_id"],
                    "amount": amount,
                    "running_total": draws_total,
                }
            )

    return {
        "contributions": contributions,
        "draws": draws,
        "total_contributions": contributions_total,
        "total_draws": draws_total,
        "balance": (contributions_total - draws_total).quantize(Decimal("0.01")),
    }


def _account_filter_for_step(step_id: str, answers: dict) -> Optional[str]:
    """Return the effective account filter for a step given current answers."""
    if step_id == "payment_account_id":
        template_id = answers.get("template_id")
        return _PAYMENT_ACCOUNT_FILTER_BY_TEMPLATE.get(template_id, "bank,credit_card,cash")
    return None


def _parse_sales_tax_rate_percent(raw_rate: str) -> Decimal:
    """Parse a percent-form sales-tax rate like '4.75' into stored decimal form."""
    cleaned = raw_rate.strip().replace("%", "")
    if not cleaned:
        raise ValueError("Sales tax rate is required.")
    try:
        percent = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Sales tax rate must be a number like 4.75.") from exc
    if percent < Decimal("0"):
        raise ValueError("Sales tax rate cannot be negative.")
    return (percent / Decimal("100")).quantize(Decimal("0.0001"))


def _serialize_sales_tax_rate(rate: Decimal) -> str:
    text = format(rate, "f").rstrip("0").rstrip(".")
    return text or "0"


def _load_sales_tax_rates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, jurisdiction, rate, is_default, effective_from, effective_to
        FROM sales_tax_rates
        ORDER BY is_default DESC, jurisdiction COLLATE NOCASE, effective_from DESC, id DESC
        """
    ).fetchall()

    return [
        {
            "id": row["id"],
            "jurisdiction": row["jurisdiction"],
            "rate": row["rate"],
            "rate_percent": (Decimal(row["rate"]) * Decimal("100")).quantize(Decimal("0.01")),
            "is_default": bool(row["is_default"]),
            "effective_from": row["effective_from"],
            "effective_to": row["effective_to"] or "",
        }
        for row in rows
    ]


def _load_default_sales_tax_rate(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, jurisdiction, rate, effective_from, effective_to
        FROM sales_tax_rates
        WHERE is_default=1
        ORDER BY effective_from DESC, id DESC
        LIMIT 1
        """
    ).fetchone()


def _normalize_sales_tax_answers(
    conn: sqlite3.Connection,
    answer_dict: dict[str, object],
) -> dict[str, object]:
    if answer_dict.get("template_id") != "SELL_INVENTORY_CASH":
        return answer_dict

    applies = answer_dict.get("sales_tax_applies")
    if applies != "yes":
        answer_dict["sales_tax_amount"] = Decimal("0")
        answer_dict["sales_tax_jurisdiction_id"] = None
        return answer_dict

    mode = answer_dict.get("sales_tax_mode")
    total_amount = Decimal(str(answer_dict.get("total_amount", "0"))).quantize(Decimal("0.01"))

    if mode == "manual":
        tax = Decimal(str(answer_dict.get("sales_tax_amount", "0"))).quantize(Decimal("0.01"))
        if tax < Decimal("0"):
            raise ValueError("Sales tax amount cannot be negative.")
        if tax > total_amount:
            raise ValueError("Sales tax amount cannot exceed the sale total.")
        answer_dict["sales_tax_amount"] = tax
        return answer_dict

    if mode == "auto":
        rate_row = _load_default_sales_tax_rate(conn)
        if rate_row is None:
            raise ValueError("Set a default sales tax rate before using auto-calculate.")
        rate = Decimal(str(rate_row["rate"]))
        if rate < Decimal("0"):
            raise ValueError("Default sales tax rate cannot be negative.")
        tax = (total_amount * rate / (Decimal("1") + rate)).quantize(Decimal("0.01"))
        answer_dict["sales_tax_amount"] = tax
        answer_dict["sales_tax_jurisdiction_id"] = rate_row["id"]
        return answer_dict

    raise ValueError("Choose whether to auto-calculate sales tax or enter it manually.")


def _validate_sales_tax_rate_form(
    *,
    jurisdiction: str,
    rate_percent: str,
    effective_from: str,
    effective_to: str,
) -> tuple[str, str, str, Optional[str]]:
    jurisdiction_clean = jurisdiction.strip()
    if not jurisdiction_clean:
        raise ValueError("Jurisdiction is required.")

    rate = _parse_sales_tax_rate_percent(rate_percent)

    try:
        parsed_from = date_type.fromisoformat(effective_from.strip())
    except ValueError as exc:
        raise ValueError("Effective from date must be a valid YYYY-MM-DD date.") from exc

    parsed_to: Optional[date_type] = None
    if effective_to.strip():
        try:
            parsed_to = date_type.fromisoformat(effective_to.strip())
        except ValueError as exc:
            raise ValueError("Effective to date must be a valid YYYY-MM-DD date.") from exc
        if parsed_to < parsed_from:
            raise ValueError("Effective to date cannot be earlier than effective from date.")

    return (
        jurisdiction_clean,
        _serialize_sales_tax_rate(rate),
        parsed_from.isoformat(),
        parsed_to.isoformat() if parsed_to else None,
    )


def _resolve_sale_inventory_context(
    conn: sqlite3.Connection,
    tas: TransactionAnswerSet,
) -> dict:
    """Validate the sold inventory selection and return sale-side COGS context."""
    if tas.inventory_link is None:
        raise ValueError("Manual sales must link to an existing inventory item.")

    link = tas.inventory_link
    if link.mode != "link_existing":
        raise ValueError("Manual sales must link to an existing inventory item.")

    item_id = link.item_id
    if item_id is None and link.sku:
        row = conn.execute(
            "SELECT id FROM items WHERE sku=?", (link.sku,)
        ).fetchone()
        item_id = row["id"] if row else None

    if item_id is None:
        raise ValueError("Could not find the inventory item selected for this sale.")

    row = conn.execute(
        """
        SELECT id, sku, name, item_type, qty_on_hand, unit_cost
        FROM items
        WHERE id=?
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Could not find the inventory item selected for this sale.")
    if row["item_type"] != "resale":
        raise ValueError("Manual sales can only be recorded against resale inventory items.")

    qty_on_hand = Decimal(str(row["qty_on_hand"] or 0)).quantize(Decimal("0.01"))
    unit_cost = Decimal(str(row["unit_cost"] or 0)).quantize(Decimal("0.01"))
    quantity = Decimal(link.quantity)

    if quantity <= Decimal("0"):
        raise ValueError("Sold quantity must be greater than 0.")
    if qty_on_hand <= Decimal("0"):
        raise ValueError(f"'{row['name']}' has no quantity on hand to sell.")
    if quantity > qty_on_hand:
        raise ValueError(
            f"You only have {qty_on_hand:.2f} of '{row['name']}' on hand, so this sale can't record {quantity:.2f}."
        )

    cogs_amount = (unit_cost * quantity).quantize(Decimal("0.01"))
    return {
        "item_id": item_id,
        "item_name": row["name"],
        "quantity": quantity,
        "qty_on_hand": qty_on_hand,
        "unit_cost": unit_cost,
        "cogs_amount": cogs_amount,
    }


def _resolve_inventory_purchase_context(
    conn: sqlite3.Connection,
    tas: TransactionAnswerSet,
) -> dict:
    """Return freight-allocated purchase cost context for inventory entries."""
    if tas.inventory_link is None:
        raise ValueError("Inventory purchases must link to an inventory item.")

    link = tas.inventory_link
    quantity = Decimal(link.quantity)
    if quantity <= Decimal("0"):
        raise ValueError("Purchased quantity must be greater than 0.")

    freight = (tas.freight_in_amount or Decimal("0")).quantize(Decimal("0.01"))
    total_amount = tas.total_amount.quantize(Decimal("0.01"))
    if freight < Decimal("0"):
        raise ValueError("Freight-in amount cannot be negative.")
    if freight > total_amount:
        raise ValueError("Freight-in amount cannot exceed the purchase total.")

    subtotal_cost = (total_amount - freight).quantize(Decimal("0.01"))
    allocated_line = allocate_freight_in(
        [
            InventoryCostLineItem(
                subtotal_cost=subtotal_cost,
                quantity=link.quantity,
            )
        ],
        freight,
    )[0]
    allocated_total_cost = allocated_line.total_cost.quantize(Decimal("0.01"))
    allocated_unit_cost = (allocated_total_cost / quantity).quantize(Decimal("0.01"))

    if link.mode == "create_new":
        return {
            "mode": "create_new",
            "item_id": None,
            "item_name": link.name,
            "quantity": link.quantity,
            "allocated_total_cost": allocated_total_cost,
            "allocated_unit_cost": allocated_unit_cost,
        }

    item_id = link.item_id
    if item_id is None and link.sku:
        row = conn.execute(
            "SELECT id FROM items WHERE sku=?", (link.sku,)
        ).fetchone()
        item_id = row["id"] if row else None
    if item_id is None:
        raise ValueError("Could not find the inventory item selected for this purchase.")

    row = conn.execute(
        """
        SELECT id, name, item_type, qty_on_hand, unit_cost
        FROM items
        WHERE id=?
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Could not find the inventory item selected for this purchase.")
    if row["item_type"] != "resale":
        raise ValueError("Inventory purchases can only be recorded against resale inventory items.")

    current_qty = Decimal(str(row["qty_on_hand"] or 0)).quantize(Decimal("0.01"))
    current_unit_cost = Decimal(str(row["unit_cost"] or 0)).quantize(Decimal("0.01"))
    new_qty = current_qty + quantity
    if new_qty <= Decimal("0"):
        raise ValueError("Inventory quantity after purchase must be positive.")
    weighted_total_cost = (current_qty * current_unit_cost) + allocated_total_cost
    weighted_unit_cost = (weighted_total_cost / new_qty).quantize(Decimal("0.01"))

    return {
        "mode": "link_existing",
        "item_id": item_id,
        "item_name": row["name"],
        "quantity": link.quantity,
        "allocated_total_cost": allocated_total_cost,
        "allocated_unit_cost": allocated_unit_cost,
        "current_qty": current_qty,
        "new_qty": new_qty,
        "weighted_unit_cost": weighted_unit_cost,
    }


@router.get("/", include_in_schema=False)
def accounting_index():
    return RedirectResponse(url="/accounting/accounts")


# ── Chart of Accounts ─────────────────────────────────────────────────────────

@router.get("/accounts", response_class=HTMLResponse)
def accounts_list(
    request: Request,
    saved: int = 0,
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    rows = conn.execute(
        "SELECT * FROM accounts ORDER BY type, code"
    ).fetchall()
    groups = {t: [] for t in _ACCOUNT_TYPES}
    for row in rows:
        groups[row["type"]].append(row)
    return render(
        "accounting/accounts.html", request,
        groups=groups,
        type_labels=_TYPE_LABELS,
        account_types=_ACCOUNT_TYPES,
        saved=saved == 1,
        error=error,
    )


@router.post("/accounts")
def accounts_create(
    code: str = Form(...),
    name: str = Form(...),
    account_type: str = Form(...),
    subtype: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    code = code.strip().upper()
    name = name.strip()
    if not code or not name or account_type not in _ACCOUNT_TYPES:
        return RedirectResponse(
            url="/accounting/accounts?error=Missing+or+invalid+fields",
            status_code=303,
        )
    try:
        with conn:
            conn.execute(
                "INSERT INTO accounts (code, name, type, subtype) VALUES (?,?,?,?)",
                (code, name, account_type, subtype.strip() or None),
            )
    except sqlite3.IntegrityError:
        return RedirectResponse(
            url=f"/accounting/accounts?error=Account+code+{code}+already+exists",
            status_code=303,
        )
    return RedirectResponse(url="/accounting/accounts?saved=1", status_code=303)


@router.post("/accounts/{account_id}")
def accounts_update(
    account_id: int,
    action: str = Form(...),
    name: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute(
        "SELECT * FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    if action == "rename":
        name = name.strip()
        if not name:
            return RedirectResponse(
                url="/accounting/accounts?error=Name+cannot+be+blank",
                status_code=303,
            )
        with conn:
            conn.execute(
                "UPDATE accounts SET name=? WHERE id=?", (name, account_id)
            )

    elif action == "toggle":
        if row["is_system_protected"]:
            return RedirectResponse(
                url="/accounting/accounts?error=Cannot+deactivate+a+system-protected+account",
                status_code=303,
            )
        with conn:
            conn.execute(
                "UPDATE accounts SET is_active=? WHERE id=?",
                (0 if row["is_active"] else 1, account_id),
            )

    return RedirectResponse(url="/accounting/accounts?saved=1", status_code=303)


@router.get("/settings/sales-tax", response_class=HTMLResponse)
def sales_tax_settings_page(
    request: Request,
    saved: int = 0,
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    return render(
        "accounting/settings_sales_tax.html",
        request,
        saved=saved == 1,
        error=error,
        rates=_load_sales_tax_rates(conn),
    )


@router.post("/settings/sales-tax")
def sales_tax_settings_submit(
    action: str = Form(...),
    rate_id: str = Form(""),
    jurisdiction: str = Form(""),
    rate_percent: str = Form(""),
    effective_from: str = Form(""),
    effective_to: str = Form(""),
    is_default: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    def err(message: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"/accounting/settings/sales-tax?error={quote(message)}",
            status_code=303,
        )

    if action not in {"add", "update"}:
        return err("Unknown sales tax settings action.")

    try:
        jurisdiction_clean, rate_text, effective_from_text, effective_to_text = _validate_sales_tax_rate_form(
            jurisdiction=jurisdiction,
            rate_percent=rate_percent,
            effective_from=effective_from,
            effective_to=effective_to,
        )
    except ValueError as exc:
        return err(str(exc))

    wants_default = is_default == "1"

    with conn:
        if action == "add":
            if wants_default:
                conn.execute("UPDATE sales_tax_rates SET is_default=0 WHERE is_default=1")
            conn.execute(
                """
                INSERT INTO sales_tax_rates (jurisdiction, rate, is_default, effective_from, effective_to)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    jurisdiction_clean,
                    rate_text,
                    1 if wants_default else 0,
                    effective_from_text,
                    effective_to_text,
                ),
            )
        else:
            try:
                rate_id_int = int(rate_id)
            except ValueError:
                return err("Unknown sales tax rate.")

            row = conn.execute(
                "SELECT id, is_default FROM sales_tax_rates WHERE id=?",
                (rate_id_int,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Sales tax rate not found")

            if wants_default:
                conn.execute("UPDATE sales_tax_rates SET is_default=0 WHERE is_default=1 AND id <> ?", (rate_id_int,))
            else:
                wants_default = bool(row["is_default"])

            conn.execute(
                """
                UPDATE sales_tax_rates
                SET jurisdiction=?, rate=?, is_default=?, effective_from=?, effective_to=?
                WHERE id=?
                """,
                (
                    jurisdiction_clean,
                    rate_text,
                    1 if wants_default else 0,
                    effective_from_text,
                    effective_to_text,
                    rate_id_int,
                ),
            )

    return RedirectResponse(url="/accounting/settings/sales-tax?saved=1", status_code=303)


# ── Manual Entry Form (developer harness) ─────────────────────────────────────

@router.get("/entry/manual", response_class=HTMLResponse)
def entry_manual_form(
    request: Request,
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    accounts = conn.execute(
        "SELECT id, code, name, type FROM accounts WHERE is_active=1 ORDER BY type, code"
    ).fetchall()
    templates = sorted(CATALOG.values(), key=lambda t: t.id)
    return render(
        "accounting/entry_manual.html", request,
        templates=templates,
        accounts=accounts,
        error=error,
        today=date_type.today().isoformat(),
    )


@router.post("/entry/manual")
def entry_manual_submit(
    template_id: str = Form(...),
    entry_date: str = Form(...),
    total_amount: str = Form(...),
    description: str = Form(""),
    vendor: str = Form(""),
    memo: str = Form(""),
    notes: str = Form(""),
    payment_account_id: str = Form(""),
    expense_category_account_id: str = Form(""),
    sales_tax_amount: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    def err(msg: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"/accounting/entry/manual?error={quote(msg)}",
            status_code=303,
        )

    try:
        tmpl = get_template(template_id)
    except KeyError:
        return err(f"Unknown template: {template_id}")

    try:
        amount = Decimal(total_amount.strip())
    except InvalidOperation:
        return err("Invalid amount — enter a number like 12.50")

    try:
        parsed_date = date_type.fromisoformat(entry_date.strip())
    except ValueError:
        return err("Invalid date")

    pay_id = int(payment_account_id) if payment_account_id.strip() else None
    exp_id = int(expense_category_account_id) if expense_category_account_id.strip() else None
    tax = Decimal(sales_tax_amount.strip()) if sales_tax_amount.strip() else None

    account_map = {
        r["code"]: r["id"]
        for r in conn.execute("SELECT code, id FROM accounts").fetchall()
    }

    try:
        lines = resolve_lines(
            template_id=template_id,
            total_amount=amount,
            account_map=account_map,
            payment_account_id=pay_id,
            expense_category_account_id=exp_id,
            sales_tax_amount=tax,
            memo=memo.strip() or None,
        )
    except (ValueError, KeyError) as exc:
        return err(str(exc))

    req = PostEntryRequest(
        entry_date=parsed_date,
        description=description.strip() or f"Manual: {tmpl.name}",
        template_id=template_id,
        total_amount=amount,
        lines=lines,
        created_by_method="manual",
        vendor=vendor.strip() or None,
        notes=notes.strip() or None,
    )

    try:
        post_entry(conn, req)
    except (UnbalancedEntryError, EmptyEntryError) as exc:
        return err(str(exc))

    return RedirectResponse(url="/accounting/entries?saved=1", status_code=303)


# ── Entry List ─────────────────────────────────────────────────────────────────

@router.get("/entries", response_class=HTMLResponse)
def entries_list(
    request: Request,
    saved: int = 0,
    from_date: str = "",
    to_date: str = "",
    filter_template: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    conditions: list[str] = []
    params: list[str] = []
    if from_date:
        conditions.append("je.entry_date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("je.entry_date <= ?")
        params.append(to_date)
    if filter_template:
        conditions.append("je.template_id = ?")
        params.append(filter_template)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    entries = conn.execute(
        f"""
        SELECT je.id, je.entry_date, je.template_id, je.description,
               je.vendor, je.total_amount, je.is_void, je.created_by_method
        FROM journal_entries je
        {where}
        ORDER BY je.entry_date DESC, je.id DESC
        """,
        params,
    ).fetchall()

    return render(
        "accounting/entry_list.html", request,
        entries=entries,
        template_ids=sorted(CATALOG.keys()),
        from_date=from_date,
        to_date=to_date,
        filter_template=filter_template,
        saved=saved == 1,
    )


# ── Reports ───────────────────────────────────────────────────────────────────

@router.get("/reports/pnl", response_class=HTMLResponse)
def report_profit_and_loss(
    request: Request,
    from_date: str = "",
    to_date: str = "",
    format: str = "html",
    conn: sqlite3.Connection = Depends(get_db),
):
    today = date_type.today()
    if not from_date:
        from_date = today.replace(day=1).isoformat()
    if not to_date:
        to_date = today.isoformat()

    try:
        parsed_from = date_type.fromisoformat(from_date)
        parsed_to = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if parsed_to < parsed_from:
        raise HTTPException(status_code=400, detail="To date must be on or after from date.")

    report = profit_and_loss(conn, from_date=from_date, to_date=to_date)

    if format == "csv":
        filename = f"profit-and-loss_{from_date}_to_{to_date}.csv"
        return _csv_response(filename, _pnl_csv_rows(report))
    if format != "html":
        raise HTTPException(status_code=400, detail="Unsupported format.")

    return render(
        "accounting/report_pnl.html",
        request,
        report=report,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/reports/balance-sheet", response_class=HTMLResponse)
def report_balance_sheet(
    request: Request,
    asof: str = "",
    format: str = "html",
    conn: sqlite3.Connection = Depends(get_db),
):
    if not asof:
        asof = date_type.today().isoformat()

    try:
        date_type.fromisoformat(asof)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    report = balance_sheet(conn, as_of=asof)

    if format == "csv":
        filename = f"balance-sheet_as-of_{asof}.csv"
        return _csv_response(filename, _balance_sheet_csv_rows(report))
    if format != "html":
        raise HTTPException(status_code=400, detail="Unsupported format.")

    return render(
        "accounting/report_balance_sheet.html",
        request,
        report=report,
        asof=asof,
    )


@router.get("/reports/expenses-by-category", response_class=HTMLResponse)
def report_expenses_by_category(
    request: Request,
    from_date: str = "",
    to_date: str = "",
    format: str = "html",
    conn: sqlite3.Connection = Depends(get_db),
):
    today = date_type.today()
    if not from_date:
        from_date = today.replace(day=1).isoformat()
    if not to_date:
        to_date = today.isoformat()

    try:
        parsed_from = date_type.fromisoformat(from_date)
        parsed_to = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if parsed_to < parsed_from:
        raise HTTPException(status_code=400, detail="To date must be on or after from date.")

    report = expenses_by_category(conn, from_date=from_date, to_date=to_date)

    if format == "csv":
        filename = f"expenses-by-category_{from_date}_to_{to_date}.csv"
        return _csv_response(filename, _expenses_by_category_csv_rows(report))
    if format != "html":
        raise HTTPException(status_code=400, detail="Unsupported format.")

    return render(
        "accounting/report_expenses_by_category.html",
        request,
        report=report,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/reports/sales-tax", response_class=HTMLResponse)
def report_sales_tax(
    request: Request,
    from_date: str = "",
    to_date: str = "",
    format: str = "html",
    conn: sqlite3.Connection = Depends(get_db),
):
    today = date_type.today()
    if not from_date:
        from_date = today.replace(day=1).isoformat()
    if not to_date:
        to_date = today.isoformat()

    try:
        parsed_from = date_type.fromisoformat(from_date)
        parsed_to = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if parsed_to < parsed_from:
        raise HTTPException(status_code=400, detail="To date must be on or after from date.")

    report = sales_tax_summary(conn, from_date=from_date, to_date=to_date)

    if format == "csv":
        filename = f"sales-tax_{from_date}_to_{to_date}.csv"
        return _csv_response(filename, _sales_tax_csv_rows(report))
    if format != "html":
        raise HTTPException(status_code=400, detail="Unsupported format.")

    return render(
        "accounting/report_sales_tax.html",
        request,
        report=report,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/reports/ledger/{account_id}", response_class=HTMLResponse)
def report_general_ledger(
    account_id: int,
    request: Request,
    from_date: str = "",
    to_date: str = "",
    format: str = "html",
    conn: sqlite3.Connection = Depends(get_db),
):
    if from_date:
        try:
            date_type.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD.")
    if to_date:
        try:
            date_type.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD.")
    if from_date and to_date and to_date < from_date:
        raise HTTPException(status_code=400, detail="To date must be on or after from date.")

    try:
        report = general_ledger(
            conn,
            account_id=account_id,
            from_date=from_date or None,
            to_date=to_date or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if format == "csv":
        filename = f"ledger_{report['account_code']}_{from_date or 'start'}_to_{to_date or 'today'}.csv"
        return _csv_response(filename, _ledger_csv_rows(report))
    if format != "html":
        raise HTTPException(status_code=400, detail="Unsupported format.")

    return render(
        "accounting/report_ledger.html",
        request,
        report=report,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/reports/year-end-export/{year}")
def report_year_end_export(
    year: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail="Year must be between 2000 and 2100.")

    payload = _build_year_end_export_zip(
        conn,
        year=year,
        base_dir=BASE_DIR,
        db_path=DB_PATH,
    )
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="year-end-export_{year}.zip"'},
    )


@router.get("/receipts", response_class=HTMLResponse)
def receipts_index_page(
    request: Request,
    from_date: str = "",
    to_date: str = "",
    template_id: str = "",
    receipt_status: str = "all",
    conn: sqlite3.Connection = Depends(get_db),
):
    if from_date:
        try:
            date_type.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD.")
    if to_date:
        try:
            date_type.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD.")
    if from_date and to_date and to_date < from_date:
        raise HTTPException(status_code=400, detail="To date must be on or after from date.")
    if receipt_status not in {"all", "has_receipt", "missing_receipt"}:
        raise HTTPException(status_code=400, detail="Unsupported receipt status filter.")

    report = receipt_index(
        conn,
        from_date=from_date or None,
        to_date=to_date or None,
        template_id=template_id or None,
        receipt_status=receipt_status,
    )
    return render(
        "accounting/receipts_index.html",
        request,
        report=report,
        from_date=from_date,
        to_date=to_date,
        template_id=template_id,
        receipt_status=receipt_status,
    )


# ── Entry Detail + Void ────────────────────────────────────────────────────────

@router.get("/entries/{entry_id}", response_class=HTMLResponse)
def entry_detail(
    request: Request,
    entry_id: int,
    voided: int = 0,
    error: str = "",
    warn: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    entry = conn.execute(
        "SELECT * FROM journal_entries WHERE id=?", (entry_id,)
    ).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    lines = conn.execute(
        """
        SELECT jl.id, jl.debit, jl.credit, jl.memo,
               a.code AS account_code, a.name AS account_name
        FROM journal_lines jl
        JOIN accounts a ON a.id = jl.account_id
        WHERE jl.entry_id = ?
        ORDER BY jl.id
        """,
        (entry_id,),
    ).fetchall()

    receipts = conn.execute(
        "SELECT * FROM receipts WHERE entry_id=? ORDER BY uploaded_at",
        (entry_id,),
    ).fetchall()

    return render(
        "accounting/entry_detail.html", request,
        entry=entry,
        lines=lines,
        receipts=receipts,
        voided=voided == 1,
        error=error,
        warn=warn,
    )


@router.post("/entries/{entry_id}/void")
def entry_void(
    entry_id: int,
    void_reason: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not void_reason.strip():
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error=Void+reason+is+required",
            status_code=303,
        )
    try:
        void_entry(conn, entry_id, void_reason.strip())
    except VoidedEntryError as exc:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error={quote(str(exc))}",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/accounting/entries/{entry_id}?voided=1",
        status_code=303,
    )


# ── Receipt Upload + Streaming ─────────────────────────────────────────────────

_RECEIPTS_DIR = BASE_DIR / "data" / "receipts"


@router.post("/entries/{entry_id}/receipts")
async def entry_attach_receipt(
    entry_id: int,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    entry = conn.execute(
        "SELECT id FROM journal_entries WHERE id=?", (entry_id,)
    ).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    content = await file.read()
    if not content:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error=Uploaded+file+is+empty",
            status_code=303,
        )

    sha256 = hashlib.sha256(content).hexdigest()
    original_filename = file.filename or "receipt"
    ext = Path(original_filename).suffix.lower()
    file_uuid = uuid.uuid4().hex
    now = datetime.utcnow()

    rel_path = (
        Path("data") / "receipts"
        / str(now.year)
        / f"{now.month:02d}"
        / f"{file_uuid}{ext}"
    )
    abs_path = BASE_DIR / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)

    duplicate = conn.execute(
        "SELECT id FROM receipts WHERE sha256=?", (sha256,)
    ).fetchone()

    with conn:
        conn.execute(
            """
            INSERT INTO receipts
              (entry_id, original_filename, stored_path, mime_type, file_size_bytes, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                original_filename,
                str(rel_path),
                file.content_type or "application/octet-stream",
                len(content),
                sha256,
            ),
        )

    if duplicate:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?warn=duplicate",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/accounting/entries/{entry_id}",
        status_code=303,
    )


@router.get("/receipts/{receipt_id}/file")
def receipt_file(
    receipt_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    receipt = conn.execute(
        "SELECT * FROM receipts WHERE id=?", (receipt_id,)
    ).fetchone()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    abs_path = BASE_DIR / receipt["stored_path"]
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Receipt file not found on disk")

    return FileResponse(
        path=str(abs_path),
        media_type=receipt["mime_type"],
        filename=receipt["original_filename"],
    )


@router.get("/owner-balance", response_class=HTMLResponse)
def owner_balance_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    detail = _owner_balance_detail(conn)
    return render(
        "accounting/owner_balance.html",
        request,
        detail=detail,
    )


# ── Questionnaire session helpers ─────────────────────────────────────────────

def _load_session(conn: sqlite3.Connection, session_id: str) -> QuestionnaireSession:
    row = conn.execute(
        "SELECT data FROM questionnaire_sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    d = json.loads(row["data"])
    return QuestionnaireSession(**d)


def _save_session(conn: sqlite3.Connection, session: QuestionnaireSession) -> None:
    session.updated_at = datetime.utcnow().isoformat()
    payload = json.dumps(asdict(session), default=str)
    with conn:
        conn.execute(
            """
            INSERT INTO questionnaire_sessions (session_id, data, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
            """,
            (session.session_id, payload, session.created_at, session.updated_at),
        )


def _delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    with conn:
        conn.execute(
            "DELETE FROM questionnaire_sessions WHERE session_id=?", (session_id,)
        )


# ── Questionnaire routes ──────────────────────────────────────────────────────

@router.get("/entry/start")
def entry_start(
    template_id: str = "",
    prefill_owner_balance: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Create a fresh session and redirect to the first step."""
    session = QuestionnaireSession()
    if template_id:
        session.answers["template_id"] = template_id
        session.history.append("template_id")
    if template_id == "REIMBURSE_OWNER" and prefill_owner_balance == 1:
        balance = _owner_reimbursement_balance(conn)
        if balance > Decimal("0.00"):
            session.answers["total_amount"] = balance
            if "total_amount_reimburse_owner" not in session.history:
                session.history.append("total_amount_reimburse_owner")
    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session.session_id}",
        status_code=303,
    )


@router.get("/entry/step/{session_id}", response_class=HTMLResponse)
def entry_step(
    session_id: str,
    request: Request,
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)
    step = next_visible_step(session)

    if step is None:
        # All required steps answered — redirect to preview
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}",
            status_code=303,
        )

    done, total = step_progress(session)
    owner_balance: Optional[Decimal] = None
    step_help_text = step.help_text

    if session.answers.get("template_id") == "REIMBURSE_OWNER":
        owner_balance = _owner_reimbursement_balance(conn)
        if step.id == "total_amount_reimburse_owner":
            step_help_text = (
                f"Current owed-back balance: ${owner_balance:.2f}. "
                "Enter any amount up to this balance."
            )
        elif step.id == "payment_account_id_reimburse_owner":
            step_help_text = (
                f"Current owed-back balance: ${owner_balance:.2f}. "
                "Choose the business bank account the reimbursement should come from."
            )
    elif session.answers.get("template_id") == "SELL_INVENTORY_CASH" and step.id == "sales_tax_mode":
        default_rate = _load_default_sales_tax_rate(conn)
        if default_rate is not None:
            rate_percent = (Decimal(str(default_rate["rate"])) * Decimal("100")).quantize(Decimal("0.01"))
            step_help_text = (
                f"Auto-calc uses your default jurisdiction, {default_rate['jurisdiction']}, "
                f"at {rate_percent}% and backs tax out of the total sale amount."
            )
        else:
            step_help_text = (
                "Auto-calc uses your default sales tax jurisdiction and backs tax out of "
                "the total sale amount. Set a default rate first if you want to use it."
            )

    step = replace(step, help_text=step_help_text)

    # For account_picker steps: fetch matching accounts for the dropdown
    accounts = []
    if step.input_type == "account_picker":
        effective_filter = _account_filter_for_step(step.id, session.answers) or step.account_filter
    else:
        effective_filter = None
    if step.input_type == "account_picker" and effective_filter:
        subtypes = [s.strip() for s in effective_filter.split(",")]
        placeholders = ",".join("?" * len(subtypes))
        accounts = conn.execute(
            f"SELECT id, code, name, type, subtype FROM accounts "
            f"WHERE (subtype IN ({placeholders}) OR type IN ({placeholders})) "
            f"AND is_active=1 ORDER BY type, code",
            subtypes + subtypes,
        ).fetchall()

    # For inventory_picker: fetch existing inventory items
    inventory_items = []
    if step.input_type == "inventory_picker":
        if step.id == "inventory_link_sale":
            inventory_items = conn.execute(
                """
                SELECT id, sku, name
                FROM items
                WHERE item_type='resale' AND qty_on_hand > 0
                ORDER BY name
                """
            ).fetchall()
        else:
            inventory_items = conn.execute(
                "SELECT id, sku, name FROM items ORDER BY name"
            ).fetchall()

    return render(
        "accounting/questionnaire_step.html", request,
        session_id=session_id,
        step=step,
        answers=session.answers,
        done=done,
        total=total,
        accounts=accounts,
        inventory_items=inventory_items,
        owner_balance=owner_balance,
        error=error,
    )


@router.post("/entry/answer")
def entry_answer(
    session_id: str = Form(...),
    step_id: str = Form(...),
    answer: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)

    from app.accounting.questionnaire import _STEP_BY_ID
    step = _STEP_BY_ID.get(step_id)
    if not step:
        raise HTTPException(status_code=400, detail=f"Unknown step: {step_id}")

    # Parse JSON answers for structured types
    raw: object
    if step.input_type == "inventory_picker" and answer:
        try:
            raw = json.loads(answer)
        except json.JSONDecodeError:
            raw = answer
    else:
        raw = answer if answer != "" else None

    if session.answers.get("template_id") == "REIMBURSE_OWNER" and step.maps_to == "total_amount":
        try:
            amount = Decimal(str(raw)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError):
            amount = None
        if amount is not None:
            try:
                _validate_reimburse_owner_amount(conn, amount)
            except ValueError as exc:
                return RedirectResponse(
                    url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
                    status_code=303,
                )

    try:
        record_answer(session, step_id, raw)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    if session.answers.get("template_id") == "SELL_INVENTORY_CASH":
        if step_id == "sales_tax_applies" and session.answers.get("sales_tax_applies") != "yes":
            session.answers.pop("sales_tax_mode", None)
            session.answers.pop("sales_tax_amount", None)
            session.answers.pop("sales_tax_jurisdiction_id", None)
        elif step_id == "sales_tax_mode" and session.answers.get("sales_tax_mode") == "auto":
            session.answers.pop("sales_tax_amount", None)
            session.answers.pop("sales_tax_jurisdiction_id", None)

    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session_id}",
        status_code=303,
    )


@router.post("/entry/back")
def entry_back(
    session_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)
    go_back(session)
    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session_id}",
        status_code=303,
    )


def _build_summary_text(
    tas: TransactionAnswerSet,
    template_name: str,
    payment_account_name: Optional[str],
    expense_account_name: Optional[str],
    inventory_item_name: Optional[str],
    inventory_purchase_ctx: Optional[dict] = None,
) -> str:
    """Return a plain-English sentence describing the transaction."""
    amt = f"${tas.total_amount:.2f}"
    tid = tas.template_id

    # Inventory item description ("3 Funko Pops" or just "items")
    item_desc: Optional[str] = None
    if tas.inventory_link:
        qty = tas.inventory_link.quantity or 1
        name = inventory_item_name or tas.inventory_link.name or "items"
        item_desc = f'{qty} “{name}”' if qty > 1 else f'“{name}”'

    vendor_part = f" from {tas.vendor}" if tas.vendor else ""

    if tid in ("BUY_INVENTORY", "BUY_INVENTORY_PERSONAL"):
        freight = tas.freight_in_amount or Decimal("0")
        unit_cost_part = ""
        if (
            inventory_purchase_ctx is not None
            and freight > 0
            and inventory_purchase_ctx.get("allocated_unit_cost") is not None
            and tas.inventory_link is not None
        ):
            qty = tas.inventory_link.quantity or 1
            item_label = inventory_item_name or tas.inventory_link.name or "item"
            if item_label.endswith("ies") and len(item_label) > 3:
                singular_label = f"{item_label[:-3]}y"
            elif item_label.endswith("s") and len(item_label) > 1:
                singular_label = item_label[:-1]
            else:
                singular_label = item_label
            each_label = singular_label if qty == 1 else item_label
            unit_cost_part = (
                f" Each {each_label} costs "
                f"${inventory_purchase_ctx['allocated_unit_cost']:.2f} with shipping included."
            )
        if freight > 0:
            item_cost = tas.total_amount - freight
            cost_part = f"${item_cost:.2f} + ${freight:.2f} shipping = {amt} total"
        else:
            cost_part = amt
        items_part = f" {item_desc}" if item_desc else ""
        if tid == "BUY_INVENTORY_PERSONAL":
            return (
                f"You bought{items_part} for {cost_part}{vendor_part} using personal funds. "
                f"The business owes you {amt} back.{unit_cost_part}"
            )
        paid = f"your {payment_account_name}" if payment_account_name else "a business account"
        return f"You bought{items_part} for {cost_part}{vendor_part} using {paid}.{unit_cost_part}"

    if tid == "SELL_INVENTORY_CASH":
        tax = tas.sales_tax_amount or Decimal("0")
        items_part = f" of {item_desc}" if item_desc else ""
        if tax > 0:
            revenue = tas.total_amount - tax
            return (
                f"You recorded a sale{items_part} of ${revenue:.2f} + ${tax:.2f} in sales tax "
                f"= {amt} total."
            )
        return f"You recorded a sale{items_part} for {amt}."

    if tid == "REIMBURSE_OWNER":
        to_acct = f" to {payment_account_name}" if payment_account_name else ""
        return f"The business reimbursed you {amt}{to_acct}."

    if tid == "OWNER_CONTRIBUTION":
        return f"You put {amt} into the business."

    if tid == "OWNER_DRAW":
        return f"You withdrew {amt} from the business."

    if tid == "PAY_CREDIT_CARD":
        from_acct = f" from {payment_account_name}" if payment_account_name else ""
        return f"You made a {amt} credit card payment{from_acct}."

    if tid == "SALES_TAX_REMITTED":
        return f"You paid {amt} in sales tax to the state."

    if tid == "OTHER_INCOME":
        return f"You recorded {amt} in other income{vendor_part}."

    # All remaining templates are business expenses
    # (BUY_EXPENSE_PERSONAL, BUSINESS_MEAL, TRAVEL_HOTEL, etc.)
    cat = expense_account_name or template_name
    if tas.funding_source == "personal":
        return (
            f"You recorded a {amt} {cat} expense paid from personal funds. "
            f"The business owes you {amt} back."
        )
    paid = f" from {payment_account_name}" if payment_account_name else ""
    return f"You recorded a {amt} {cat} expense{paid}{vendor_part}."


@router.get("/entry/preview/{session_id}", response_class=HTMLResponse)
def entry_preview_get(
    session_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)

    if not is_complete(session):
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}",
            status_code=303,
        )

    answer_dict = build_answer_set(session)
    try:
        answer_dict = _normalize_sales_tax_answers(conn, answer_dict)
        tas = TransactionAnswerSet(**answer_dict)
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    if tas.template_id == "REIMBURSE_OWNER":
        try:
            _validate_reimburse_owner_amount(conn, tas.total_amount)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
                status_code=303,
            )

    sale_inventory_ctx: Optional[dict] = None
    if tas.template_id == "SELL_INVENTORY_CASH":
        try:
            sale_inventory_ctx = _resolve_sale_inventory_context(conn, tas)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
                status_code=303,
            )

    template = get_template(tas.template_id)

    # Build account_map (code → id) and id → (code, name) for reverse lookup
    acct_rows = conn.execute("SELECT id, code, name FROM accounts").fetchall()
    account_map: dict[str, int] = {r["code"]: r["id"] for r in acct_rows}
    id_to_acct: dict[int, dict] = {r["id"]: r for r in acct_rows}

    try:
        lines = resolve_lines(
            template_id=tas.template_id,
            total_amount=tas.total_amount,
            account_map=account_map,
            payment_account_id=tas.payment_account_id,
            expense_category_account_id=tas.expense_category_account_id,
            sales_tax_amount=tas.sales_tax_amount,
            memo=tas.memo,
            inventory_item_id=sale_inventory_ctx["item_id"] if sale_inventory_ctx else None,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    if tas.template_id == "SELL_INVENTORY_CASH" and sale_inventory_ctx is not None:
        cogs_lines = [
            JournalLineInput(
                account_id=account_map["5000"],
                debit=sale_inventory_ctx["cogs_amount"],
                memo="Auto COGS",
                inventory_item_id=sale_inventory_ctx["item_id"],
            ),
            JournalLineInput(
                account_id=account_map["1200"],
                credit=sale_inventory_ctx["cogs_amount"],
                memo="Auto COGS",
                inventory_item_id=sale_inventory_ctx["item_id"],
            ),
        ]
        lines.extend(cogs_lines)

    enriched_lines = []
    for ln in lines:
        acct = id_to_acct.get(ln.account_id, {})
        enriched_lines.append({
            "account_code": acct.get("code", str(ln.account_id)),
            "account_name": acct.get("name", "Unknown"),
            "debit":  str(ln.debit),
            "credit": str(ln.credit),
            "memo":   ln.memo or "",
        })

    # Resolve display names for the plain-English summary
    payment_account_name: Optional[str] = None
    if tas.payment_account_id and tas.payment_account_id in id_to_acct:
        payment_account_name = id_to_acct[tas.payment_account_id].get("name")

    expense_account_name: Optional[str] = None
    if tas.expense_category_account_id and tas.expense_category_account_id in id_to_acct:
        expense_account_name = id_to_acct[tas.expense_category_account_id].get("name")

    inventory_item_name: Optional[str] = None
    if sale_inventory_ctx is not None:
        inventory_item_name = sale_inventory_ctx["item_name"]
    elif inventory_purchase_ctx is not None:
        inventory_item_name = inventory_purchase_ctx["item_name"]
    elif tas.inventory_link and tas.inventory_link.mode == "link_existing" and tas.inventory_link.item_id:
        row = conn.execute(
            "SELECT name FROM items WHERE id=?", (tas.inventory_link.item_id,)
        ).fetchone()
        if row:
            inventory_item_name = row["name"]
    elif tas.inventory_link and tas.inventory_link.name:
        inventory_item_name = tas.inventory_link.name

    summary_text = _build_summary_text(
        tas,
        template.name,
        payment_account_name,
        expense_account_name,
        inventory_item_name,
        inventory_purchase_ctx=inventory_purchase_ctx,
    )

    inventory_unit_cost: Optional[Decimal] = None
    if inventory_purchase_ctx is not None:
        inventory_unit_cost = inventory_purchase_ctx.get("allocated_unit_cost")

    return render(
        "accounting/entry_confirm.html", request,
        session_id=session_id,
        tas=tas,
        template=template,
        lines=enriched_lines,
        summary_text=summary_text,
        payment_account_name=payment_account_name,
        expense_account_name=expense_account_name,
        inventory_item_name=inventory_item_name,
        inventory_unit_cost=inventory_unit_cost,
    )


@router.post("/entry/preview")
def entry_preview_post(
    session_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Redirect to the GET preview so the form confirm button works."""
    return RedirectResponse(
        url=f"/accounting/entry/preview/{session_id}",
        status_code=303,
    )


@router.post("/entry/back-to-edit")
def entry_back_to_edit(
    session_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Undo the last step so the user can edit from the preview screen."""
    session = _load_session(conn, session_id)
    go_back(session)
    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session_id}",
        status_code=303,
    )


def _resolve_inventory_item_id(
    conn: sqlite3.Connection,
    tas: TransactionAnswerSet,
) -> Optional[int]:
    """Return an items.id to attach to journal lines, creating a new item if needed."""
    if tas.inventory_link is None:
        return None

    link = tas.inventory_link

    if link.mode == "link_existing":
        if link.item_id is not None:
            return link.item_id
        # Fallback: lookup by SKU
        row = conn.execute(
            "SELECT id FROM items WHERE sku=?", (link.sku,)
        ).fetchone()
        return row["id"] if row else None

    # mode == "create_new": insert a minimal resale item
    unit_cost = float(tas.total_amount) / max(link.quantity, 1)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO items (item_type, name, qty_on_hand, unit_cost)
            VALUES ('resale', ?, ?, ?)
            """,
            (link.name, link.quantity, unit_cost),
        )
    return cur.lastrowid


def _insert_new_inventory_purchase_item(
    tx_conn: sqlite3.Connection,
    *,
    name: str,
    quantity: int,
    unit_cost: Decimal,
) -> int:
    """Create a resale inventory item with a generated SKU for accounting purchases."""
    sku = get_next_sku(tx_conn, _DEFAULT_NEW_RESALE_COMPANY, _DEFAULT_NEW_RESALE_CODE)
    column_names = {
        row["name"]
        for row in tx_conn.execute("PRAGMA table_info(items)").fetchall()
    }

    values: dict[str, object] = {
        "item_type": "resale",
        "sku": sku,
        "name": name,
        "qty_on_hand": quantity,
        "unit_cost": float(unit_cost),
    }
    optional_values = {
        "company": _DEFAULT_NEW_RESALE_COMPANY,
        "brand_code": _DEFAULT_NEW_RESALE_CODE,
        "category": "Collectibles",
        "unit": "each",
    }
    for key, value in optional_values.items():
        if key in column_names:
            values[key] = value

    columns = ", ".join(values.keys())
    placeholders = ", ".join("?" for _ in values)
    cur = tx_conn.execute(
        f"INSERT INTO items ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    return cur.lastrowid


def _apply_inventory_purchase(
    tx_conn: sqlite3.Connection,
    req: PostEntryRequest,
    purchase_ctx: dict,
) -> int:
    """Insert or update the purchased inventory item before posting the entry."""
    if purchase_ctx["mode"] == "create_new":
        item_id = _insert_new_inventory_purchase_item(
            tx_conn,
            name=purchase_ctx["item_name"],
            quantity=purchase_ctx["quantity"],
            unit_cost=purchase_ctx["allocated_unit_cost"],
        )
    else:
        item_id = purchase_ctx["item_id"]
        tx_conn.execute(
            """
            UPDATE items
            SET qty_on_hand=?, unit_cost=?
            WHERE id=? AND item_type='resale'
            """,
            (
                float(purchase_ctx["new_qty"]),
                float(purchase_ctx["weighted_unit_cost"]),
                item_id,
            ),
        )

    for ln in req.lines:
        ln.inventory_item_id = item_id
    return item_id


@router.post("/entry/confirm")
async def entry_confirm(
    request: Request,
    session_id: str = Form(...),
    receipt: Optional[UploadFile] = File(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)

    if not is_complete(session):
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}",
            status_code=303,
        )

    answer_dict = build_answer_set(session)
    try:
        answer_dict = _normalize_sales_tax_answers(conn, answer_dict)
        tas = TransactionAnswerSet(**answer_dict)
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    if tas.template_id == "REIMBURSE_OWNER":
        try:
            _validate_reimburse_owner_amount(conn, tas.total_amount)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
                status_code=303,
            )

    sale_inventory_ctx: Optional[dict] = None
    if tas.template_id == "SELL_INVENTORY_CASH":
        try:
            sale_inventory_ctx = _resolve_sale_inventory_context(conn, tas)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
                status_code=303,
            )

    inventory_purchase_ctx: Optional[dict] = None
    if tas.template_id in ("BUY_INVENTORY", "BUY_INVENTORY_PERSONAL"):
        try:
            inventory_purchase_ctx = _resolve_inventory_purchase_context(conn, tas)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
                status_code=303,
            )

    template = get_template(tas.template_id)

    # Resolve inventory item (create if needed) before building lines
    try:
        inventory_item_id = (
            sale_inventory_ctx["item_id"]
            if sale_inventory_ctx is not None
            else inventory_purchase_ctx["item_id"]
            if inventory_purchase_ctx is not None
            else _resolve_inventory_item_id(conn, tas)
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    # Build account_map and journal lines
    acct_rows = conn.execute("SELECT id, code FROM accounts").fetchall()
    account_map: dict[str, int] = {r["code"]: r["id"] for r in acct_rows}

    try:
        lines = resolve_lines(
            template_id=tas.template_id,
            total_amount=tas.total_amount,
            account_map=account_map,
            payment_account_id=tas.payment_account_id,
            expense_category_account_id=tas.expense_category_account_id,
            sales_tax_amount=tas.sales_tax_amount,
            memo=tas.memo,
            inventory_item_id=inventory_item_id,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    # Auto-generate description
    description = tas.description or template.name
    if tas.vendor:
        description = f"{description} — {tas.vendor}"

    req = PostEntryRequest(
        entry_date=tas.entry_date,
        description=description,
        template_id=tas.template_id,
        total_amount=tas.total_amount,
        lines=lines,
        vendor=tas.vendor,
        notes=tas.notes or tas.memo,
    )

    try:
        if tas.template_id == "SELL_INVENTORY_CASH" and sale_inventory_ctx is not None:
            cogs_req = PostEntryRequest(
                entry_date=tas.entry_date,
                description=f"Auto COGS — {description}",
                template_id="COGS_RECOGNITION",
                total_amount=sale_inventory_ctx["cogs_amount"],
                lines=[
                    JournalLineInput(
                        account_id=account_map["5000"],
                        debit=sale_inventory_ctx["cogs_amount"],
                        memo="Auto COGS",
                        inventory_item_id=inventory_item_id,
                    ),
                    JournalLineInput(
                        account_id=account_map["1200"],
                        credit=sale_inventory_ctx["cogs_amount"],
                        memo="Auto COGS",
                        inventory_item_id=inventory_item_id,
                    ),
                ],
                created_by_method="system_auto",
                vendor=tas.vendor,
                notes=f"Auto-generated from sale entry: {description}",
            )

            def _after_sale_insert(tx_conn: sqlite3.Connection, _entry_ids: list[int]) -> None:
                tx_conn.execute(
                    """
                    UPDATE items
                    SET qty_on_hand = qty_on_hand - ?
                    WHERE id=? AND item_type='resale'
                    """,
                    (float(sale_inventory_ctx["quantity"]), inventory_item_id),
                )

            entry_id = post_entries(conn, [req, cogs_req], after_insert=_after_sale_insert)[0]
        elif tas.template_id in ("BUY_INVENTORY", "BUY_INVENTORY_PERSONAL") and inventory_purchase_ctx is not None:
            def _before_inventory_insert(tx_conn: sqlite3.Connection) -> None:
                _apply_inventory_purchase(tx_conn, req, inventory_purchase_ctx)

            entry_id = post_entries(conn, [req], before_insert=_before_inventory_insert)[0]
        else:
            entry_id = post_entry(conn, req)
    except (EmptyEntryError, UnbalancedEntryError) as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    # Attach receipt if one was uploaded
    if receipt and receipt.filename:
        content = await receipt.read()
        if content:
            sha256 = hashlib.sha256(content).hexdigest()
            ext = Path(receipt.filename).suffix.lower()
            file_uuid = uuid.uuid4().hex
            now = datetime.utcnow()
            rel_path = (
                Path("data") / "receipts"
                / str(now.year)
                / f"{now.month:02d}"
                / f"{file_uuid}{ext}"
            )
            abs_path = BASE_DIR / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(content)
            with conn:
                conn.execute(
                    """
                    INSERT INTO receipts
                      (entry_id, original_filename, stored_path, mime_type, file_size_bytes, sha256)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        receipt.filename,
                        str(rel_path),
                        receipt.content_type or "application/octet-stream",
                        len(content),
                        sha256,
                    ),
                )

    # Clean up the session — entry is posted
    _delete_session(conn, session_id)

    return RedirectResponse(
        url=f"/accounting/entries/{entry_id}",
        status_code=303,
    )
