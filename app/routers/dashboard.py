from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..accounting.reports import profit_and_loss
from ..accounting.routes import _owner_reimbursement_balance
from ..db import DB_PATH
from ..deps import render, BACKUP_DIR, BASE_DIR, get_db

router = APIRouter()


def _contributions_draws(conn: sqlite3.Connection) -> tuple[Decimal, Decimal]:
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
        debit = Decimal(str(row["debit"] or "0"))
        credit = Decimal(str(row["credit"] or "0"))
        if row["account_code"] == "3100":
            contributions += credit - debit
        elif row["account_code"] == "3200":
            draws += debit - credit
    return contributions.quantize(Decimal("0.01")), draws.quantize(Decimal("0.01"))


def _balance_by_code(conn: sqlite3.Connection, code: str) -> Decimal:
    row = conn.execute(
        """
        SELECT a.type,
               SUM(CAST(jl.debit AS REAL)) AS d,
               SUM(CAST(jl.credit AS REAL)) AS c
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE je.is_void = 0 AND a.code = ?
        """,
        (code,),
    ).fetchone()
    if not row or row["d"] is None:
        return Decimal("0.00")
    d = Decimal(str(row["d"] or "0"))
    c = Decimal(str(row["c"] or "0"))
    sign = 1 if row["type"] in ("asset", "expense") else -1
    return ((d - c) * Decimal(sign)).quantize(Decimal("0.01"))


def _cash_accounts(conn: sqlite3.Connection) -> list[dict]:
    """Bank, cash, and credit-card accounts with nonzero balances."""
    rows = conn.execute(
        """
        SELECT a.name, a.subtype, a.type,
               SUM(CAST(jl.debit AS REAL)) AS d,
               SUM(CAST(jl.credit AS REAL)) AS c
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE je.is_void = 0
          AND ((a.type = 'asset' AND a.subtype IN ('bank', 'cash'))
               OR (a.type = 'liability' AND a.subtype = 'credit_card'))
        GROUP BY a.id, a.name, a.subtype, a.type
        ORDER BY a.type ASC, a.code ASC
        """
    ).fetchall()
    result = []
    for row in rows:
        d = Decimal(str(row["d"] or "0"))
        c = Decimal(str(row["c"] or "0"))
        sign = 1 if row["type"] == "asset" else -1
        balance = ((d - c) * Decimal(sign)).quantize(Decimal("0.01"))
        result.append({
            "name": row["name"],
            "subtype": row["subtype"] or "",
            "is_liability": row["type"] == "liability",
            "balance": balance,
        })
    return result


def _missing_receipts_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM journal_entries je
        WHERE je.is_void = 0
          AND NOT EXISTS (
            SELECT 1 FROM receipts r WHERE r.entry_id = je.id
          )
        """
    ).fetchone()
    return row["c"] if row else 0


def _recent_entries(conn: sqlite3.Connection, limit: int = 7) -> list[dict]:
    rows = conn.execute(
        """
        SELECT je.id, je.entry_date, je.description, je.template_id,
               SUM(CAST(jl.debit AS REAL)) AS amount,
               EXISTS(SELECT 1 FROM receipts r WHERE r.entry_id = je.id) AS has_receipt
        FROM journal_entries je
        LEFT JOIN journal_lines jl ON jl.entry_id = je.id
        WHERE je.is_void = 0
        GROUP BY je.id
        ORDER BY je.entry_date DESC, je.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "date": row["entry_date"],
            "desc": row["description"],
            "template_id": row["template_id"],
            "amount": Decimal(str(row["amount"] or "0")).quantize(Decimal("0.01")),
            "has_receipt": bool(row["has_receipt"]),
        }
        for row in rows
    ]


# =========================
# ANCHOR: DASHBOARD_VIEW_BEGIN
# (Dashboard counts for resale inventory status)
# =========================
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    month_end = today.isoformat()

    # Last month date range
    last_day_prev = today.replace(day=1) - timedelta(days=1)
    last_month_start = last_day_prev.replace(day=1).isoformat()
    last_month_end = last_day_prev.isoformat()

    # Year to date
    year_start = today.replace(month=1, day=1).isoformat()

    # ── Resale inventory counts ──────────────────────────────────────
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

    listed = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM items i
        JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.item_type='resale' AND rl.status = 'listed'
        """
    ).fetchone()["c"]

    low_qty = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM items
        WHERE item_type='resale' AND qty_on_hand <= 0
        """
    ).fetchone()["c"]

    # ── Accounting ───────────────────────────────────────────────────
    owner_reimbursement_balance = _owner_reimbursement_balance(conn)
    contributions_total, draws_total = _contributions_draws(conn)

    pnl_mtd = profit_and_loss(conn, from_date=month_start, to_date=month_end)
    net_income_mtd = pnl_mtd["net_income"]

    pnl_last = profit_and_loss(conn, from_date=last_month_start, to_date=last_month_end)
    net_income_last = pnl_last["net_income"]

    pnl_ytd = profit_and_loss(conn, from_date=year_start, to_date=month_end)
    net_income_ytd = pnl_ytd["net_income"]

    if net_income_last != Decimal("0"):
        trend_pct: int | None = round(
            float((net_income_mtd - net_income_last) / abs(net_income_last) * 100)
        )
    else:
        trend_pct = None

    cash_accounts = _cash_accounts(conn)
    sales_tax_payable = _balance_by_code(conn, "2100")
    inventory_value = _balance_by_code(conn, "1200")
    missing_receipts = _missing_receipts_count(conn)
    recent_entries = _recent_entries(conn)

    # ── Nag list ─────────────────────────────────────────────────────
    nags = []
    if missing_receipts > 0:
        label = f"{missing_receipts} entr{'y' if missing_receipts == 1 else 'ies'} missing receipts"
        nags.append({"label": label, "action_label": "Open vault", "action_url": "/accounting/receipts"})
    if low_qty > 0:
        label = f"{low_qty} item{'s' if low_qty > 1 else ''} out of stock"
        nags.append({"label": label, "action_label": "Review inventory", "action_url": "/resale"})

    # ── MCP staged writes pending review ─────────────────────────────
    try:
        mcp_pending_count = conn.execute(
            "SELECT COUNT(*) FROM mcp_pending_writes WHERE status='pending'"
        ).fetchone()[0]
    except Exception:
        mcp_pending_count = 0

    # ── New job leads waiting in the Job Search inbox ────────────────
    try:
        from ..services.kanban import inbox_count
        job_inbox_count = inbox_count(conn, "job-search")
    except Exception:
        job_inbox_count = 0

    return render(
        "dashboard.html",
        request,
        resale_count=resale_count,
        unlisted=unlisted,
        listed=listed,
        low_qty=low_qty,
        owner_reimbursement_balance=owner_reimbursement_balance,
        contributions_total=contributions_total,
        draws_total=draws_total,
        net_income_mtd=net_income_mtd,
        net_income_ytd=net_income_ytd,
        trend_pct=trend_pct,
        cash_accounts=cash_accounts,
        sales_tax_payable=sales_tax_payable,
        inventory_value=inventory_value,
        missing_receipts=missing_receipts,
        recent_entries=recent_entries,
        nags=nags,
        month_name=today.strftime("%b"),
        mcp_pending_count=mcp_pending_count,
        job_inbox_count=job_inbox_count,
    )
# =========================
# ANCHOR: DASHBOARD_VIEW_END
# =========================


# =========================
# ANCHOR: BACKUP_NOW_BEGIN
# (Create a timestamped database backup)
# =========================
@router.post("/backup")
def backup_now():
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"saltstocks_{ts}.db"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, dest)
    receipts_src = BASE_DIR / "data" / "receipts"
    if receipts_src.exists():
        shutil.copytree(receipts_src, BACKUP_DIR / f"receipts_{ts}")
    return RedirectResponse(url="/", status_code=303)
# =========================
# ANCHOR: BACKUP_NOW_END
# =========================
