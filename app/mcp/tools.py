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
