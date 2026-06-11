"""
Query functions backing the SaltStocks MCP tools.
All functions accept a sqlite3.Connection and return plain dicts
with all monetary fields as two-decimal strings and an `as_of` timestamp.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from app.accounting.reports import profit_and_loss


_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_SALE_TEMPLATES = ("SELL_INVENTORY_EBAY", "SELL_INVENTORY_CASH")


def _as_of() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dec(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(_CENT)
    except InvalidOperation:
        return _ZERO


def _fmt(d: Decimal) -> str:
    return f"{d:.2f}"


# ── 1. P&L Summary ────────────────────────────────────────────────────────────

def pnl_summary(
    conn: sqlite3.Connection,
    period: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    today = date.today()
    year = today.year
    month = today.month

    if start_date and end_date:
        from_d, to_d = start_date, end_date
        label = f"{from_d} to {to_d}"
    elif period == "mtd":
        from_d = f"{year}-{month:02d}-01"
        to_d = today.isoformat()
        label = f"Month to date ({year}-{month:02d})"
    elif period == "ytd":
        from_d = f"{year}-01-01"
        to_d = today.isoformat()
        label = f"Year to date ({year})"
    else:  # all_time
        from_d = "1900-01-01"
        to_d = today.isoformat()
        label = "All time"

    pnl = profit_and_loss(conn, from_date=from_d, to_date=to_d)

    rev = pnl["total_revenue"]
    gp = pnl["gross_profit"]
    margin = (gp / rev * 100).quantize(_CENT) if rev else _ZERO

    items_sold = conn.execute(
        """
        SELECT COUNT(DISTINCT je.id) AS cnt
        FROM journal_entries je
        WHERE je.template_id = 'COGS_RECOGNITION'
          AND je.is_void = 0
          AND je.entry_date >= ? AND je.entry_date <= ?
        """,
        (from_d, to_d),
    ).fetchone()["cnt"] or 0

    return {
        "as_of": _as_of(),
        "currency": "USD",
        "period_label": label,
        "revenue": _fmt(pnl["total_revenue"]),
        "cogs": _fmt(pnl["total_cogs"]),
        "gross_profit": _fmt(gp),
        "gross_margin_pct": _fmt(margin),
        "expenses": [
            {"account_name": ln.account_name, "amount": _fmt(ln.amount)}
            for ln in pnl["operating_expenses"]
        ],
        "total_expenses": _fmt(pnl["total_expenses"]),
        "net_income": _fmt(pnl["net_income"]),
        "items_sold_count": items_sold,
    }


# ── 2. Inventory Snapshot ─────────────────────────────────────────────────────

def inventory_snapshot(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT i.id, i.sku, i.name, i.category, i.qty_on_hand, i.unit_cost, i.lot_id,
               COALESCE(rl.status, 'unlisted') AS status
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.item_type = 'resale' AND i.qty_on_hand > 0
        ORDER BY i.name
        """
    ).fetchall()

    by_status: dict[str, dict] = {}
    items = []
    total_value = _ZERO

    for r in rows:
        item_value = (_dec(r["qty_on_hand"]) * _dec(r["unit_cost"])).quantize(_CENT)
        total_value += item_value
        status = r["status"]

        if status not in by_status:
            by_status[status] = {"status": status, "count": 0, "value": _ZERO}
        by_status[status]["count"] += 1
        by_status[status]["value"] += item_value

        entry: dict = {
            "id": r["id"],
            "sku": r["sku"],
            "name": r["name"],
            "category": r["category"],
            "status": status,
            "qty_on_hand": str(r["qty_on_hand"]),
            "unit_cost": _fmt(_dec(r["unit_cost"])),
            "total_cost": _fmt(item_value),
        }
        if r["lot_id"] is not None:
            entry["lot_id"] = r["lot_id"]
        items.append(entry)

    return {
        "as_of": _as_of(),
        "currency": "USD",
        "total_inventory_value": _fmt(total_value),
        "total_items_count": len(items),
        "by_status": [
            {
                "status": s["status"],
                "count": s["count"],
                "value": _fmt(s["value"]),
            }
            for s in sorted(by_status.values(), key=lambda x: x["status"])
        ],
        "items": items,
    }


# ── 3. Item Detail ────────────────────────────────────────────────────────────

def item_detail(conn: sqlite3.Connection, item_id_or_sku: str) -> dict:
    # Resolve by id (int) or SKU (string)
    row = None
    try:
        iid = int(item_id_or_sku)
        row = conn.execute(
            "SELECT * FROM items WHERE id = ? AND item_type = 'resale'", (iid,)
        ).fetchone()
    except ValueError:
        pass
    if row is None:
        row = conn.execute(
            "SELECT * FROM items WHERE sku = ? AND item_type = 'resale'", (item_id_or_sku,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Item '{item_id_or_sku}' not found.")

    item_id = row["id"]

    # Purchase history: debit lines on account 1200 linked to this item
    inv_acct = conn.execute("SELECT id FROM accounts WHERE code = '1200'").fetchone()
    purchase_rows = (
        conn.execute(
            """
            SELECT je.id AS entry_id, je.entry_date, CAST(jl.debit AS REAL) AS amount,
                   je.vendor
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id
            WHERE jl.inventory_item_id = ?
              AND jl.account_id = ?
              AND CAST(jl.debit AS REAL) > 0
              AND je.is_void = 0
            ORDER BY je.entry_date
            """,
            (item_id, inv_acct["id"]),
        ).fetchall()
        if inv_acct
        else []
    )

    purchase_entries = [
        {
            "journal_entry_id": p["entry_id"],
            "date": p["entry_date"],
            "amount": _fmt(_dec(p["amount"])),
            "vendor": p["vendor"],
        }
        for p in purchase_rows
    ]

    # Sale event: most recent COGS_RECOGNITION entry for this item
    cogs_acct = conn.execute("SELECT id FROM accounts WHERE code = '5000'").fetchone()
    sale_event = None
    if cogs_acct:
        cogs_row = conn.execute(
            """
            SELECT je.id AS entry_id, je.entry_date, je.notes,
                   CAST(jl.debit AS REAL) AS cogs_amount
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id
            WHERE jl.inventory_item_id = ?
              AND jl.account_id = ?
              AND je.is_void = 0
            ORDER BY je.entry_date DESC
            LIMIT 1
            """,
            (item_id, cogs_acct["id"]),
        ).fetchone()

        if cogs_row:
            cogs = _dec(cogs_row["cogs_amount"])
            sale_entry = None

            # Find matching revenue entry by notes (works for eBay imports)
            if cogs_row["notes"]:
                sale_entry = conn.execute(
                    """
                    SELECT je.id, je.total_amount,
                           SUM(CASE WHEN a.code='6060' THEN CAST(jl.debit AS REAL) ELSE 0 END) AS fees,
                           SUM(CASE WHEN a.code='6050' THEN CAST(jl.debit AS REAL) ELSE 0 END) AS shipping
                    FROM journal_entries je
                    JOIN journal_lines jl ON jl.entry_id = je.id
                    JOIN accounts a ON a.id = jl.account_id
                    WHERE je.notes = ?
                      AND je.template_id IN ('SELL_INVENTORY_EBAY', 'SELL_INVENTORY_CASH')
                      AND je.is_void = 0
                    GROUP BY je.id
                    LIMIT 1
                    """,
                    (cogs_row["notes"],),
                ).fetchone()

            if sale_entry:
                sale_price = _dec(sale_entry["total_amount"])
                fees = _dec(sale_entry["fees"])
                shipping = _dec(sale_entry["shipping"])
                net_payout = (sale_price - fees - shipping).quantize(_CENT)
                gp = (net_payout - cogs).quantize(_CENT)
                margin = (gp / sale_price * 100).quantize(_CENT) if sale_price else _ZERO
                sale_event = {
                    "date": cogs_row["entry_date"],
                    "sale_price": _fmt(sale_price),
                    "ebay_fees": _fmt(fees),
                    "shipping": _fmt(shipping),
                    "net_payout": _fmt(net_payout),
                    "cogs": _fmt(cogs),
                    "gross_profit": _fmt(gp),
                    "margin_pct": _fmt(margin),
                    "journal_entry_id": sale_entry["id"],
                }
            else:
                # Fallback: report what we know from the COGS entry
                sale_event = {
                    "date": cogs_row["entry_date"],
                    "cogs": _fmt(cogs),
                    "journal_entry_id": cogs_row["entry_id"],
                }

    # Lot info
    lot = None
    if row["lot_id"]:
        lot_row = conn.execute(
            "SELECT id, qty_received, qty_remaining, unit_cost, received_date, vendor FROM inventory_lots WHERE id = ?",
            (row["lot_id"],),
        ).fetchone()
        if lot_row:
            lot = {
                "lot_id": lot_row["id"],
                "qty_received": lot_row["qty_received"],
                "qty_remaining": lot_row["qty_remaining"],
                "unit_cost": _fmt(_dec(lot_row["unit_cost"])),
                "purchase_date": lot_row["received_date"],
                "vendor": lot_row["vendor"],
            }

    return {
        "as_of": _as_of(),
        "id": item_id,
        "sku": row["sku"],
        "name": row["name"],
        "category": row["category"],
        "status": None,  # populated below
        "unit_cost": _fmt(_dec(row["unit_cost"])),
        "qty_on_hand": str(row["qty_on_hand"]),
        "purchase_entries": purchase_entries,
        **({"sale_event": sale_event} if sale_event else {}),
        **({"lot": lot} if lot else {}),
    }


# ── 4. Sale History ───────────────────────────────────────────────────────────

def sale_history(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    group_by: str = "month",
) -> dict:
    params = (start_date, end_date)

    # Revenue, fees, shipping from SELL entries
    rev_rows = conn.execute(
        """
        SELECT
            strftime('%Y-%m', je.entry_date) AS month,
            COUNT(DISTINCT je.id)            AS sale_count,
            SUM(CASE WHEN a.code='4000' THEN CAST(jl.credit AS REAL) ELSE 0 END) AS revenue,
            SUM(CASE WHEN a.code='6060' THEN CAST(jl.debit AS REAL) ELSE 0 END)  AS fees,
            SUM(CASE WHEN a.code='6050' THEN CAST(jl.debit AS REAL) ELSE 0 END)  AS shipping
        FROM journal_entries je
        JOIN journal_lines jl ON jl.entry_id = je.id
        JOIN accounts a ON a.id = jl.account_id
        WHERE je.template_id IN ('SELL_INVENTORY_EBAY', 'SELL_INVENTORY_CASH')
          AND je.is_void = 0
          AND je.entry_date >= ? AND je.entry_date <= ?
        GROUP BY month
        ORDER BY month
        """,
        params,
    ).fetchall()

    # COGS from COGS_RECOGNITION entries
    cogs_rows = conn.execute(
        """
        SELECT
            strftime('%Y-%m', je.entry_date) AS month,
            SUM(CAST(jl.debit AS REAL)) AS cogs
        FROM journal_entries je
        JOIN journal_lines jl ON jl.entry_id = je.id
        JOIN accounts a ON a.id = jl.account_id
        WHERE je.template_id = 'COGS_RECOGNITION'
          AND a.code = '5000'
          AND je.is_void = 0
          AND je.entry_date >= ? AND je.entry_date <= ?
        GROUP BY month
        ORDER BY month
        """,
        params,
    ).fetchall()

    cogs_by_month = {r["month"]: _dec(r["cogs"]) for r in cogs_rows}

    if group_by == "month":
        months = []
        tot_items = tot_rev = tot_cogs = tot_fees = tot_net = _ZERO
        for r in rev_rows:
            m = r["month"]
            rev = _dec(r["revenue"])
            fees = _dec(r["fees"])
            shipping = _dec(r["shipping"])
            cogs = cogs_by_month.get(m, _ZERO)
            net = (rev - cogs - fees - shipping).quantize(_CENT)
            margin = (net / rev * 100).quantize(_CENT) if rev else _ZERO
            months.append({
                "month": m,
                "items_sold": r["sale_count"],
                "revenue": _fmt(rev),
                "cogs": _fmt(cogs),
                "fees": _fmt(fees),
                "shipping": _fmt(shipping),
                "net_profit": _fmt(net),
                "margin_pct": _fmt(margin),
            })
            tot_items += r["sale_count"]
            tot_rev += rev
            tot_cogs += cogs
            tot_fees += fees
            tot_net += net

        return {
            "as_of": _as_of(),
            "currency": "USD",
            "by_month": months,
            "totals": {
                "items_sold": int(tot_items),
                "revenue": _fmt(tot_rev),
                "cogs": _fmt(tot_cogs),
                "fees": _fmt(tot_fees),
                "net_profit": _fmt(tot_net),
            },
        }

    # group_by == "item"
    item_rows = conn.execute(
        """
        SELECT
            i.id, i.sku, i.name,
            cogs_je.entry_date AS sale_date,
            CAST(cogs_jl.debit AS REAL) AS cogs,
            cogs_je.notes AS cogs_notes,
            cogs_je.id AS cogs_entry_id
        FROM journal_entries cogs_je
        JOIN journal_lines cogs_jl ON cogs_jl.entry_id = cogs_je.id
        JOIN accounts ca ON ca.id = cogs_jl.account_id AND ca.code = '5000'
        JOIN items i ON i.id = cogs_jl.inventory_item_id
        WHERE cogs_je.template_id = 'COGS_RECOGNITION'
          AND cogs_je.is_void = 0
          AND cogs_je.entry_date >= ? AND cogs_je.entry_date <= ?
        ORDER BY cogs_je.entry_date DESC
        """,
        params,
    ).fetchall()

    items = []
    tot_rev = tot_cogs = tot_fees = tot_net = _ZERO

    for r in item_rows:
        cogs = _dec(r["cogs"])
        # Match revenue entry by notes
        rev_entry = None
        if r["cogs_notes"]:
            rev_entry = conn.execute(
                """
                SELECT je.total_amount,
                       SUM(CASE WHEN a.code='6060' THEN CAST(jl.debit AS REAL) ELSE 0 END) AS fees,
                       SUM(CASE WHEN a.code='6050' THEN CAST(jl.debit AS REAL) ELSE 0 END) AS shipping
                FROM journal_entries je
                JOIN journal_lines jl ON jl.entry_id = je.id
                JOIN accounts a ON a.id = jl.account_id
                WHERE je.notes = ?
                  AND je.template_id IN ('SELL_INVENTORY_EBAY', 'SELL_INVENTORY_CASH')
                  AND je.is_void = 0
                GROUP BY je.id
                LIMIT 1
                """,
                (r["cogs_notes"],),
            ).fetchone()

        sale_price = _dec(rev_entry["total_amount"]) if rev_entry else _ZERO
        fees = _dec(rev_entry["fees"]) if rev_entry else _ZERO
        shipping = _dec(rev_entry["shipping"]) if rev_entry else _ZERO
        net = (sale_price - cogs - fees - shipping).quantize(_CENT)
        margin = (net / sale_price * 100).quantize(_CENT) if sale_price else _ZERO

        items.append({
            "id": r["id"],
            "sku": r["sku"],
            "name": r["name"],
            "sale_date": r["sale_date"],
            "sale_price": _fmt(sale_price),
            "cogs": _fmt(cogs),
            "fees": _fmt(fees),
            "shipping": _fmt(shipping),
            "net_profit": _fmt(net),
            "margin_pct": _fmt(margin),
        })
        tot_rev += sale_price
        tot_cogs += cogs
        tot_fees += fees
        tot_net += net

    return {
        "as_of": _as_of(),
        "currency": "USD",
        "items": items,
        "totals": {
            "items_sold": len(items),
            "revenue": _fmt(tot_rev),
            "cogs": _fmt(tot_cogs),
            "fees": _fmt(tot_fees),
            "net_profit": _fmt(tot_net),
        },
    }


# ── 5. Recent Journal Entries ─────────────────────────────────────────────────

def recent_journal_entries(
    conn: sqlite3.Connection,
    limit: int = 10,
    start_date: Optional[str] = None,
    account_code: Optional[str] = None,
) -> dict:
    limit = min(max(1, limit), 50)

    clauses = ["je.is_void = 0"]
    params: list = []
    if start_date:
        clauses.append("je.entry_date >= ?")
        params.append(start_date)
    if account_code:
        clauses.append(
            """je.id IN (
                SELECT DISTINCT jl.entry_id FROM journal_lines jl
                JOIN accounts a ON a.id = jl.account_id WHERE a.code = ?
            )"""
        )
        params.append(account_code)

    where = " AND ".join(clauses)
    params.append(limit)

    entry_rows = conn.execute(
        f"""
        SELECT je.id, je.entry_date, je.description, je.template_id, je.is_void
        FROM journal_entries je
        WHERE {where}
        ORDER BY je.entry_date DESC, je.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    entries = []
    for e in entry_rows:
        line_rows = conn.execute(
            """
            SELECT a.code AS account_code, a.name AS account_name,
                   jl.debit, jl.credit, jl.inventory_item_id
            FROM journal_lines jl
            JOIN accounts a ON a.id = jl.account_id
            WHERE jl.entry_id = ?
            ORDER BY jl.id
            """,
            (e["id"],),
        ).fetchall()

        lines = [
            {
                "account_code": ln["account_code"],
                "account_name": ln["account_name"],
                "debit": _fmt(_dec(ln["debit"])),
                "credit": _fmt(_dec(ln["credit"])),
                **({"inventory_item_id": ln["inventory_item_id"]}
                   if ln["inventory_item_id"] is not None else {}),
            }
            for ln in line_rows
        ]

        entries.append({
            "id": e["id"],
            "date": e["entry_date"],
            "description": e["description"],
            "template_id": e["template_id"],
            "is_void": bool(e["is_void"]),
            "lines": lines,
        })

    return {"as_of": _as_of(), "entries": entries}
