"""
The six MCP tools for SaltStocks.

Each tool opens its own DB connection (no FastAPI request context here),
delegates to app.services.mcp_queries or app.services.ebay_fee_calculator,
and returns a plain dict. FastMCP serialises the return value to JSON.

All monetary fields are decimal strings with exactly two decimal places.
Every response includes an `as_of` ISO 8601 UTC timestamp.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from fastmcp import FastMCP


# Templates that post_entry can write via MCP.
# Inventory-purchase templates (BUY_INVENTORY, BUY_MATERIALS, SELL_INVENTORY_*,
# COGS_RECOGNITION, PRODUCTION_RUN) are excluded because they require item-table
# side effects that must go through the full questionnaire flow.
_ALLOWED_TEMPLATES = frozenset({
    "BUSINESS_MEAL",
    "TRAVEL_HOTEL",
    "TRAVEL_TRANSPORT",
    "OFFICE_SUPPLIES",
    "SOFTWARE_SUBSCRIPTION",
    "SHIPPING_OUTBOUND",
    "EBAY_FEES",
    "PAYMENT_PROCESSING_FEE",
    "UTILITIES",
    "RENT",
    "PROFESSIONAL_SERVICES",
    "BANK_FEE",
    "OTHER_EXPENSE",
    "OTHER_INCOME",
    "OWNER_CONTRIBUTION",
    "OWNER_DRAW",
    "PAY_CREDIT_CARD",
    "SALES_TAX_REMITTED",
    "REIMBURSE_OWNER",
    "BUY_EXPENSE_PERSONAL",
})


def _validate_template_id(template_id: str) -> None:
    if template_id not in _ALLOWED_TEMPLATES:
        raise ValueError(
            f"template_id '{template_id}' is not supported via MCP. "
            f"Allowed templates: {sorted(_ALLOWED_TEMPLATES)}. "
            "Inventory purchase/sale templates require the SaltStocks web UI."
        )


def _build_entry_preview(
    conn,
    *,
    template_id: str,
    entry_date: str,
    total_amount: str,
    description: str,
    payment_account_code: str,
    vendor=None,
    expense_category_account_code=None,
) -> dict:
    """Resolve journal lines for a template without writing. Returns preview dict."""
    from datetime import datetime, timezone
    from decimal import Decimal as _Decimal

    from app.accounting.catalog import resolve_lines

    _validate_template_id(template_id)

    try:
        amount = _Decimal(total_amount)
    except Exception:
        raise ValueError(f"Invalid total_amount '{total_amount}' — must be a decimal number.")

    # Build account_map (code → id) for all accounts
    rows = conn.execute("SELECT id, code FROM accounts WHERE is_active = 1").fetchall()
    account_map = {r["code"]: r["id"] for r in rows}

    # Resolve payment account
    if payment_account_code not in account_map:
        raise ValueError(
            f"Account code '{payment_account_code}' not found. "
            "Call list_accounts to see valid codes."
        )
    payment_account_id = account_map[payment_account_code]

    # Resolve optional expense category account
    expense_category_account_id = None
    if expense_category_account_code:
        if expense_category_account_code not in account_map:
            raise ValueError(
                f"expense_category_account_code '{expense_category_account_code}' not found."
            )
        expense_category_account_id = account_map[expense_category_account_code]

    lines = resolve_lines(
        template_id,
        amount,
        account_map,
        payment_account_id=payment_account_id,
        expense_category_account_id=expense_category_account_id,
    )

    # Build display lines (human-readable) and raw lines (for post_entry)
    code_to_name = {
        r["code"]: r["name"]
        for r in conn.execute("SELECT code, name FROM accounts").fetchall()
    }
    id_to_code = {v: k for k, v in account_map.items()}

    display_lines = []
    raw_lines = []
    for ln in lines:
        code = id_to_code.get(ln.account_id, "?")
        display_lines.append({
            "account_code": code,
            "account_name": code_to_name.get(code, ""),
            "debit": f"{ln.debit:.2f}",
            "credit": f"{ln.credit:.2f}",
        })
        raw_lines.append({
            "account_id": ln.account_id,
            "debit": str(ln.debit),
            "credit": str(ln.credit),
            "memo": ln.memo,
        })

    return {
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "template_id": template_id,
        "entry_date": entry_date,
        "description": description,
        "vendor": vendor,
        "total_amount": f"{amount:.2f}",
        "lines": display_lines,
        "lines_raw": raw_lines,
        "note": "Preview only — nothing has been written.",
    }


def register(mcp: FastMCP) -> None:
    """Attach all six tools to the FastMCP instance."""

    # ── Tool 1: P&L Summary ───────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Returns the SaltStocks profit and loss summary: revenue, COGS, gross profit, "
            "expense breakdown, and net income for a period. This is the primary tool for any "
            "question about profitability, income, or business performance."
        )
    )
    def get_pnl_summary(
        period: Literal["mtd", "ytd", "all_time"],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """
        period: 'mtd' = month to date, 'ytd' = year to date, 'all_time' = all records.
        start_date / end_date (ISO date, e.g. '2025-01-01') override period when both provided.
        """
        from app.db import get_conn
        from app.services import mcp_queries
        conn = get_conn()
        try:
            return mcp_queries.pnl_summary(conn, period, start_date, end_date)
        finally:
            conn.close()

    # ── Tool 2: Inventory Snapshot ────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Returns the current state of resale inventory: every item with quantity on hand, "
            "weighted average cost, and status. Use for questions about what is in stock, total "
            "inventory value, or items by status."
        )
    )
    def get_inventory_snapshot() -> dict:
        from app.db import get_conn
        from app.services import mcp_queries
        conn = get_conn()
        try:
            return mcp_queries.inventory_snapshot(conn)
        finally:
            conn.close()

    # ── Tool 3: Item Detail ───────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Returns full detail for one inventory item by SKU or id: cost history, sale event "
            "if the item has sold, net profit, and lot membership if applicable. Use for "
            "questions about a specific item."
        )
    )
    def get_item_detail(item_id: str) -> dict:
        """
        item_id: internal integer id or SKU string (e.g. 'GV-FUNK-000042').
        """
        from app.db import get_conn
        from app.services import mcp_queries
        conn = get_conn()
        try:
            return mcp_queries.item_detail(conn, item_id)
        finally:
            conn.close()

    # ── Tool 4: Sale History ──────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Returns eBay sale performance over a date range: monthly totals and per-item detail "
            "including revenue, COGS, fees, and net profit. Use for trend questions and questions "
            "about how sales are going."
        )
    )
    def get_sale_history(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        group_by: Literal["month", "item"] = "month",
    ) -> dict:
        """
        start_date / end_date: ISO date strings. Default window is 90 days ago to today.
        group_by: 'month' for monthly totals, 'item' for per-sale line items.
        """
        today = date.today()
        sd = start_date or (today - timedelta(days=90)).isoformat()
        ed = end_date or today.isoformat()

        from app.db import get_conn
        from app.services import mcp_queries
        conn = get_conn()
        try:
            return mcp_queries.sale_history(conn, sd, ed, group_by)
        finally:
            conn.close()

    # ── Tool 5: Simulate eBay Sale ────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Simulates the accounting outcome of listing and selling an item at a given price: "
            "estimated eBay fees, expected net payout, COGS at current weighted average cost, "
            "and gross profit. Use for pricing decisions and what-if questions before listing. "
            "Fee rate defaults to 13.25% (combined eBay standard rate). "
            "Simulation only — no data is written."
        )
    )
    def simulate_ebay_sale(
        item_id: str,
        list_price: str,
        shipping_cost: str = "0",
        ebay_fee_rate: str = "0.1325",
    ) -> dict:
        """
        item_id: internal id or SKU.
        list_price / shipping_cost: decimal strings e.g. '29.99'.
        ebay_fee_rate: decimal fraction e.g. '0.1325' for 13.25%.
        """
        from app.db import get_conn
        from app.services import mcp_queries, ebay_fee_calculator as calc

        try:
            lp = Decimal(list_price)
            sc = Decimal(shipping_cost)
            fr = Decimal(ebay_fee_rate)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal value: {exc}") from exc

        conn = get_conn()
        try:
            item = mcp_queries.item_detail(conn, item_id)
        finally:
            conn.close()

        unit_cost = Decimal(item["unit_cost"])
        result = calc.calculate_sale(lp, unit_cost, sc, fr)

        return {
            "as_of": item["as_of"],
            "item": {
                "id": item["id"],
                "sku": item["sku"],
                "name": item["name"],
                "unit_cost": item["unit_cost"],
            },
            "list_price": f"{lp:.2f}",
            "estimated_ebay_fee": f"{result['ebay_fee']:.2f}",
            "shipping_cost": f"{sc:.2f}",
            "net_payout": f"{result['net_payout']:.2f}",
            "cogs": f"{result['cogs']:.2f}",
            "gross_profit": f"{result['gross_profit']:.2f}",
            "margin_pct": f"{result['margin_pct']:.2f}",
            "break_even_price": f"{result['break_even_price']:.2f}",
            "note": "Simulation only — no data written.",
        }

    # ── Tool 7: List Accounts ─────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Returns the SaltStocks chart of accounts grouped by type (Asset, Liability, Equity, "
            "Revenue, Expense). Each account has an id, code, name, and normal_balance. "
            "Use this before calling preview_entry or record_expense to look up the correct "
            "payment_account_code (e.g. Chase Checking = '1010', business credit card = '2010')."
        )
    )
    def list_accounts() -> dict:
        from app.db import get_conn
        from app.services import mcp_queries
        conn = get_conn()
        try:
            return mcp_queries.chart_of_accounts(conn)
        finally:
            conn.close()

    # ── Tool 8: Preview Entry ─────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Dry-run: shows the exact journal lines that would be posted for a given template "
            "and amounts WITHOUT writing anything to the database. Use this to confirm the entry "
            "is correct before calling record_expense. "
            "template_id must be one of the ALLOWED_EXPENSE_TEMPLATES (call list_accounts first "
            "to get valid payment account codes). "
            "Inventory-purchase templates (BUY_INVENTORY, BUY_MATERIALS) are not supported here "
            "because they have inventory side effects — use the SaltStocks web UI for those."
        )
    )
    def preview_entry(
        template_id: str,
        entry_date: str,
        total_amount: str,
        description: str,
        payment_account_code: str,
        vendor: Optional[str] = None,
        expense_category_account_code: Optional[str] = None,
    ) -> dict:
        """
        template_id: e.g. 'BUSINESS_MEAL', 'OFFICE_SUPPLIES', 'SOFTWARE_SUBSCRIPTION'.
        entry_date: ISO date e.g. '2026-06-11'.
        total_amount: decimal string e.g. '42.50'.
        payment_account_code: chart-of-accounts code for the payment method (e.g. '1010', '2010').
        expense_category_account_code: required only for OTHER_EXPENSE template.
        """
        from app.db import get_conn
        conn = get_conn()
        try:
            return _build_entry_preview(
                conn,
                template_id=template_id,
                entry_date=entry_date,
                total_amount=total_amount,
                description=description,
                payment_account_code=payment_account_code,
                vendor=vendor,
                expense_category_account_code=expense_category_account_code,
            )
        finally:
            conn.close()

    # ── Tool 9: Record Expense ─────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Posts a balanced journal entry to SaltStocks from a receipt or expense. "
            "This WRITES to the database — always call preview_entry first and confirm with "
            "the user before calling this. "
            "Supported templates: BUSINESS_MEAL, TRAVEL_HOTEL, TRAVEL_TRANSPORT, "
            "OFFICE_SUPPLIES, SOFTWARE_SUBSCRIPTION, SHIPPING_OUTBOUND, EBAY_FEES, "
            "PAYMENT_PROCESSING_FEE, UTILITIES, RENT, PROFESSIONAL_SERVICES, BANK_FEE, "
            "OTHER_EXPENSE, OTHER_INCOME, OWNER_CONTRIBUTION, OWNER_DRAW, "
            "PAY_CREDIT_CARD, SALES_TAX_REMITTED, REIMBURSE_OWNER, BUY_EXPENSE_PERSONAL. "
            "Returns the new journal entry id and the lines posted."
        )
    )
    def record_expense(
        template_id: str,
        entry_date: str,
        total_amount: str,
        description: str,
        payment_account_code: str,
        vendor: Optional[str] = None,
        notes: Optional[str] = None,
        expense_category_account_code: Optional[str] = None,
    ) -> dict:
        """
        template_id: must be one of the supported expense/equity templates.
        entry_date: ISO date e.g. '2026-06-11'.
        total_amount: decimal string e.g. '42.50'.
        payment_account_code: chart-of-accounts code for the payment method (e.g. '1010', '2010').
        vendor: optional vendor/payee name.
        notes: optional free-text notes (e.g. receipt number, eBay order id).
        expense_category_account_code: required only for OTHER_EXPENSE and BUY_EXPENSE_PERSONAL.
        """
        from datetime import date as _date
        from decimal import Decimal as _Decimal

        from app.db import get_conn
        from app.accounting.posting import PostEntryRequest, post_entry

        _validate_template_id(template_id)

        conn = get_conn()
        try:
            preview = _build_entry_preview(
                conn,
                template_id=template_id,
                entry_date=entry_date,
                total_amount=total_amount,
                description=description,
                payment_account_code=payment_account_code,
                vendor=vendor,
                expense_category_account_code=expense_category_account_code,
            )

            from app.accounting.posting import JournalLineInput
            lines = [
                JournalLineInput(
                    account_id=ln["account_id"],
                    debit=_Decimal(ln["debit"]),
                    credit=_Decimal(ln["credit"]),
                    memo=ln.get("memo"),
                )
                for ln in preview["lines_raw"]
            ]

            req = PostEntryRequest(
                entry_date=_date.fromisoformat(entry_date),
                description=description,
                template_id=template_id,
                total_amount=_Decimal(total_amount),
                lines=lines,
                created_by_method="manual",
                vendor=vendor,
                notes=notes,
            )

            entry_id = post_entry(conn, req)

            return {
                "success": True,
                "entry_id": entry_id,
                "template_id": template_id,
                "entry_date": entry_date,
                "description": description,
                "total_amount": f"{_Decimal(total_amount):.2f}",
                "lines": preview["lines"],
                "message": f"Journal entry #{entry_id} posted successfully.",
            }
        finally:
            conn.close()

    # ── Tool 6: Recent Journal Entries ────────────────────────────────────────

    @mcp.tool(
        description=(
            "Returns recent journal entries with their debit/credit lines. Use for accounting "
            "questions about specific transactions, verifying a sale was recorded correctly, "
            "or reviewing the ledger."
        )
    )
    def get_recent_journal_entries(
        limit: int = 10,
        start_date: Optional[str] = None,
        account_code: Optional[str] = None,
    ) -> dict:
        """
        limit: max entries to return (1–50, default 10).
        start_date: ISO date — only return entries on or after this date.
        account_code: chart-of-accounts code e.g. '5000' — filter to entries touching this account.
        """
        if limit > 50:
            raise ValueError("limit cannot exceed 50. Narrow the date range or account filter instead.")

        from app.db import get_conn
        from app.services import mcp_queries
        conn = get_conn()
        try:
            return mcp_queries.recent_journal_entries(conn, limit, start_date, account_code)
        finally:
            conn.close()
