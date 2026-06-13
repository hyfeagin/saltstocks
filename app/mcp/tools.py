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


# Templates allowed for the generic record_expense tool.
# Inventory/materials purchase templates are handled by record_inventory_purchase instead,
# which also manages the item-table side effects.
# SELL_INVENTORY_*, COGS_RECOGNITION, PRODUCTION_RUN stay web-UI only.
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

    # ── Tool 10: Record Inventory / Materials Purchase ────────────────────────

    @mcp.tool(
        description=(
            "Records an inventory or materials purchase: posts the journal entry AND creates "
            "or updates the item record(s) in one atomic operation. "
            "Set item_type='resale' for resale inventory, 'material' for production materials. "
            "Set use_personal_funds=True when you paid out of pocket (offsets to Owner Contributions). "
            "Set create_lot=True for lot purchases (e.g. a Goodwill lot, a wholesale box). "
            "When create_lot=True: fungible=True creates one item row with qty=N (stress balls, "
            "generic stock); fungible=False creates N individual item rows with qty=1 each "
            "(comics, Lego sets — for per-unit tracking). "
            "Set existing_item_id to restock an item already in inventory (triggers WAC recalc). "
            "Always call preview_inventory_purchase first and confirm with the user before writing."
        )
    )
    def record_inventory_purchase(
        entry_date: str,
        total_amount: str,
        description: str,
        item_name: str,
        quantity: int,
        item_type: str = "resale",
        vendor: Optional[str] = None,
        notes: Optional[str] = None,
        payment_account_code: Optional[str] = None,
        use_personal_funds: bool = False,
        create_lot: bool = False,
        fungible: bool = True,
        existing_item_id: Optional[int] = None,
        category: Optional[str] = None,
    ) -> dict:
        """
        entry_date: ISO date e.g. '2026-06-11'.
        total_amount: total paid for all units e.g. '25.00'.
        item_name: name for the new item / lot.
        quantity: number of units purchased.
        item_type: 'resale' or 'material'.
        payment_account_code: account code for payment method e.g. '1020'. Required unless use_personal_funds=True.
        use_personal_funds: if True, offsets to account 3100 (Owner Contributions).
        create_lot: True for lot purchases — creates an inventory_lots record.
        fungible: used only when create_lot=True. True = single item with qty=N; False = N items with qty=1.
        existing_item_id: integer id of an existing item to restock (WAC is recalculated).
        category: optional item category label.
        """
        from datetime import date as _date
        from decimal import Decimal as _Decimal

        from app.db import get_conn
        from app.accounting.posting import PostEntryRequest, JournalLineInput, post_entries

        # Validate
        if not use_personal_funds and not payment_account_code:
            raise ValueError(
                "Provide payment_account_code (e.g. '1020') or set use_personal_funds=True."
            )
        if item_type not in ("resale", "material"):
            raise ValueError("item_type must be 'resale' or 'material'.")
        if quantity < 1:
            raise ValueError("quantity must be at least 1.")
        if create_lot and fungible is False and quantity > 200:
            raise ValueError(
                "fungible=False would create more than 200 individual item rows. "
                "Use fungible=True for large fungible lots, or break into smaller batches."
            )

        try:
            amount = _Decimal(total_amount)
        except Exception:
            raise ValueError(f"Invalid total_amount '{total_amount}'.")

        unit_cost = (amount / _Decimal(quantity)).quantize(_Decimal("0.0001"))

        # Pick template
        if item_type == "material":
            template_id = "BUY_MATERIALS_PERSONAL" if use_personal_funds else "BUY_MATERIALS"
        else:
            template_id = "BUY_INVENTORY_PERSONAL" if use_personal_funds else "BUY_INVENTORY"

        conn = get_conn()
        try:
            # Resolve account map
            rows = conn.execute(
                "SELECT id, code FROM accounts WHERE is_active = 1"
            ).fetchall()
            account_map = {r["code"]: r["id"] for r in rows}

            inv_account_id = account_map.get("1200")
            if not inv_account_id:
                raise ValueError("Account 1200 (Inventory) not found in chart of accounts.")

            if use_personal_funds:
                cr_account_id = account_map.get("3100")
                if not cr_account_id:
                    raise ValueError("Account 3100 (Owner Contributions) not found.")
            else:
                if payment_account_code not in account_map:
                    raise ValueError(
                        f"Account code '{payment_account_code}' not found. "
                        "Call list_accounts to see valid codes."
                    )
                cr_account_id = account_map[payment_account_code]

            # Resolve existing item info for WAC (if restocking)
            existing_qty = _Decimal("0")
            existing_unit_cost = _Decimal("0")
            if existing_item_id is not None:
                row = conn.execute(
                    "SELECT qty_on_hand, unit_cost FROM items WHERE id = ?",
                    (existing_item_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Item id={existing_item_id} not found.")
                existing_qty = _Decimal(str(row["qty_on_hand"] or 0))
                existing_unit_cost = _Decimal(str(row["unit_cost"] or 0))

            # Restock: we know the item_id now, so link the DR line immediately.
            # New item or lot: item_id is generated in before_insert/after_insert;
            # we backfill via _after_insert for single items, leave unlinked for lots
            # (lots use items.lot_id as the cost anchor instead).
            dr_item_id = existing_item_id  # None for new items / lots
            lines = [
                JournalLineInput(account_id=inv_account_id, debit=amount, inventory_item_id=dr_item_id),
                JournalLineInput(account_id=cr_account_id, credit=amount),
            ]

            req = PostEntryRequest(
                entry_date=_date.fromisoformat(entry_date),
                description=description,
                template_id=template_id,
                total_amount=amount,
                lines=lines,
                created_by_method="manual",
                vendor=vendor,
                notes=notes,
            )

            # Build the item-side-effect callbacks
            lot_ctx = {
                "quantity": quantity,
                "allocated_unit_cost": unit_cost,
                "item_name": item_name,
                "fungible": fungible,
            }

            created_items: list[dict] = []
            created_lot_id: list[int] = []  # mutable container for after_insert closure

            def _before_insert(tx_conn):
                if existing_item_id is not None:
                    # Restock: WAC recalculation
                    new_qty = existing_qty + _Decimal(quantity)
                    new_unit_cost = (
                        (existing_qty * existing_unit_cost + _Decimal(quantity) * unit_cost)
                        / new_qty
                    ).quantize(_Decimal("0.0001"))
                    tx_conn.execute(
                        "UPDATE items SET qty_on_hand=?, unit_cost=? WHERE id=?",
                        (float(new_qty), float(new_unit_cost), existing_item_id),
                    )
                    created_items.append({
                        "item_id": existing_item_id,
                        "action": "restocked",
                        "new_qty": str(new_qty),
                        "new_unit_cost": str(new_unit_cost.quantize(_Decimal("0.01"))),
                    })
                elif not create_lot:
                    # Single new item
                    from app.accounting.routes import _insert_new_inventory_purchase_item
                    iid = _insert_new_inventory_purchase_item(
                        tx_conn,
                        name=item_name,
                        quantity=quantity,
                        unit_cost=unit_cost,
                        item_type=item_type,
                    )
                    if category:
                        tx_conn.execute(
                            "UPDATE items SET category=? WHERE id=?", (category, iid)
                        )
                    created_items.append({"item_id": iid, "action": "created", "qty": quantity})

            def _after_insert(tx_conn, entry_ids):
                # Backfill inventory_item_id on the DR 1200 line for new single items.
                # We couldn't set it earlier because the item didn't exist yet.
                if not create_lot and existing_item_id is None and created_items:
                    new_iid = created_items[0]["item_id"]
                    tx_conn.execute(
                        """
                        UPDATE journal_lines
                        SET inventory_item_id = ?
                        WHERE entry_id = ? AND account_id = ? AND CAST(debit AS REAL) > 0
                        """,
                        (new_iid, entry_ids[0], inv_account_id),
                    )

                if create_lot and existing_item_id is None:
                    from app.accounting.routes import _create_inventory_lot_with_items
                    lid = _create_inventory_lot_with_items(
                        tx_conn,
                        ctx=lot_ctx,
                        journal_entry_id=entry_ids[0],
                        entry_date=entry_date,
                        vendor=vendor,
                        fungible=fungible,
                    )
                    if category:
                        tx_conn.execute(
                            "UPDATE items SET category=? WHERE lot_id=?", (category, lid)
                        )
                    created_lot_id.append(lid)
                    n_items = tx_conn.execute(
                        "SELECT COUNT(*) AS c FROM items WHERE lot_id=?", (lid,)
                    ).fetchone()["c"]
                    created_items.append({
                        "lot_id": lid,
                        "action": "lot_created",
                        "items_created": n_items,
                    })

            entry_ids = post_entries(
                conn, [req],
                before_insert=_before_insert,
                after_insert=_after_insert,
            )

            from datetime import datetime, timezone
            return {
                "success": True,
                "entry_id": entry_ids[0],
                "template_id": template_id,
                "entry_date": entry_date,
                "description": description,
                "vendor": vendor,
                "total_amount": f"{amount:.2f}",
                "unit_cost": f"{unit_cost:.4f}",
                "quantity": quantity,
                "item_type": item_type,
                "inventory_changes": created_items,
                **({"lot_id": created_lot_id[0]} if created_lot_id else {}),
                "message": f"Journal entry #{entry_ids[0]} posted and inventory updated.",
                "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        finally:
            conn.close()

    # ── Tool 11: Record Sale ──────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Records the sale of an inventory item: posts the revenue journal entry AND the "
            "auto-COGS entry, then decrements qty_on_hand (and lot qty_remaining if applicable) "
            "— all in one atomic transaction. "
            "sale_channel='cash' for in-person/card sales; 'ebay' for eBay orders. "
            "For eBay: sale_price is the item price the buyer paid (not including shipping); "
            "ebay_fees is the combined eBay fee; shipping_charged is what the buyer paid for "
            "shipping (added to gross revenue). "
            "For cash: sales_tax is split into a separate Sales Tax Payable credit if > 0. "
            "Always show the user what will be posted (call get_item_detail first to confirm "
            "unit_cost and qty) and get confirmation before calling this."
        )
    )
    def record_sale(
        item_id: int,
        entry_date: str,
        sale_price: str,
        payment_account_code: str,
        quantity: int = 1,
        sale_channel: str = "cash",
        sales_tax: str = "0",
        ebay_fees: str = "0",
        shipping_charged: str = "0",
        description: Optional[str] = None,
        vendor: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """
        item_id: integer id of the item being sold.
        entry_date: ISO date e.g. '2026-06-12'.
        sale_price: what the buyer paid for the item (not including shipping) e.g. '29.99'.
        payment_account_code: where the money lands e.g. '1020' (bank) or '1010' (cash).
        quantity: units sold (default 1).
        sale_channel: 'cash' or 'ebay'.
        sales_tax: cash sales only — tax collected e.g. '2.47'.
        ebay_fees: eBay sales only — combined fee amount e.g. '4.20'.
        shipping_charged: eBay sales only — shipping the buyer paid e.g. '5.00'.
        """
        from datetime import date as _date, datetime, timezone
        from decimal import Decimal as _Decimal

        from app.db import get_conn
        from app.accounting.catalog import resolve_lines
        from app.accounting.posting import PostEntryRequest, JournalLineInput, post_entries

        if sale_channel not in ("cash", "ebay"):
            raise ValueError("sale_channel must be 'cash' or 'ebay'.")
        if quantity < 1:
            raise ValueError("quantity must be at least 1.")

        try:
            price = _Decimal(sale_price)
            tax = _Decimal(sales_tax)
            fees = _Decimal(ebay_fees)
            shipping = _Decimal(shipping_charged)
        except Exception:
            raise ValueError("Monetary values must be decimal numbers e.g. '29.99'.")

        conn = get_conn()
        try:
            # Resolve item
            item = conn.execute(
                "SELECT id, name, qty_on_hand, unit_cost, lot_id, item_type FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if item is None:
                raise ValueError(f"Item id={item_id} not found.")
            if item["item_type"] != "resale":
                raise ValueError(
                    f"Item {item_id} is a material, not a resale item. Only resale items can be sold."
                )

            qty_on_hand = _Decimal(str(item["qty_on_hand"] or 0))
            if qty_on_hand <= 0:
                raise ValueError(f"Item '{item['name']}' has no qty on hand.")
            if _Decimal(quantity) > qty_on_hand:
                raise ValueError(
                    f"Cannot sell {quantity} — only {qty_on_hand} of '{item['name']}' on hand."
                )

            unit_cost = _Decimal(str(item["unit_cost"] or 0)).quantize(_Decimal("0.01"))
            cogs_amount = (unit_cost * _Decimal(quantity)).quantize(_Decimal("0.01"))

            # Account map
            acct_rows = conn.execute("SELECT id, code, name FROM accounts").fetchall()
            account_map = {r["code"]: r["id"] for r in acct_rows}
            id_to_name = {r["id"]: r["name"] for r in acct_rows}
            id_to_code = {r["id"]: r["code"] for r in acct_rows}

            if payment_account_code not in account_map:
                raise ValueError(
                    f"Account code '{payment_account_code}' not found. "
                    "Call list_accounts to see valid codes."
                )
            payment_account_id = account_map[payment_account_code]

            # Template and total_amount
            if sale_channel == "ebay":
                template_id = "SELL_INVENTORY_EBAY"
                gross_revenue = (price + shipping).quantize(_Decimal("0.01"))
                total_amount = price  # resolve_lines adds shipping internally
            else:
                template_id = "SELL_INVENTORY_CASH"
                gross_revenue = price
                total_amount = price

            item_desc = description or f"Sold {item['name']}"

            # Revenue lines
            revenue_lines = resolve_lines(
                template_id,
                total_amount,
                account_map,
                payment_account_id=payment_account_id,
                sales_tax_amount=tax if sale_channel == "cash" else None,
                ebay_fees_amount=fees if sale_channel == "ebay" else None,
                ebay_shipping_charged=shipping if sale_channel == "ebay" else None,
                inventory_item_id=item_id,
            )

            # COGS lines
            cogs_lines = [
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
            ]

            revenue_req = PostEntryRequest(
                entry_date=_date.fromisoformat(entry_date),
                description=item_desc,
                template_id=template_id,
                total_amount=gross_revenue,
                lines=revenue_lines,
                created_by_method="manual",
                vendor=vendor,
                notes=notes,
            )

            cogs_req = PostEntryRequest(
                entry_date=_date.fromisoformat(entry_date),
                description=f"Auto COGS — {item_desc}",
                template_id="COGS_RECOGNITION",
                total_amount=cogs_amount,
                lines=cogs_lines,
                created_by_method="system_auto",
                vendor=vendor,
                notes=notes,
            )

            def _after_sale_insert(tx_conn, _entry_ids):
                tx_conn.execute(
                    "UPDATE items SET qty_on_hand = qty_on_hand - ? WHERE id = ?",
                    (float(quantity), item_id),
                )
                if item["lot_id"] is not None:
                    tx_conn.execute(
                        """
                        UPDATE inventory_lots
                        SET qty_remaining = MAX(0, qty_remaining - ?)
                        WHERE id = ?
                        """,
                        (float(quantity), item["lot_id"]),
                    )

            entry_ids = post_entries(
                conn, [revenue_req, cogs_req],
                after_insert=_after_sale_insert,
            )

            # Build human-readable line summary
            def _fmt_lines(req):
                out = []
                for ln in req.lines:
                    code = id_to_code.get(ln.account_id, "?")
                    out.append({
                        "account_code": code,
                        "account_name": id_to_name.get(ln.account_id, ""),
                        "debit": f"{ln.debit:.2f}",
                        "credit": f"{ln.credit:.2f}",
                    })
                return out

            net_profit = (gross_revenue - (tax if sale_channel == "cash" else _Decimal("0")) - fees - cogs_amount).quantize(_Decimal("0.01"))

            return {
                "success": True,
                "revenue_entry_id": entry_ids[0],
                "cogs_entry_id": entry_ids[1],
                "item_id": item_id,
                "item_name": item["name"],
                "entry_date": entry_date,
                "sale_channel": sale_channel,
                "sale_price": f"{price:.2f}",
                "gross_revenue": f"{gross_revenue:.2f}",
                **({"ebay_fees": f"{fees:.2f}", "shipping_charged": f"{shipping:.2f}"} if sale_channel == "ebay" else {}),
                **({"sales_tax": f"{tax:.2f}"} if sale_channel == "cash" and tax > 0 else {}),
                "cogs": f"{cogs_amount:.2f}",
                "net_profit": f"{net_profit:.2f}",
                "qty_remaining": f"{qty_on_hand - _Decimal(quantity):.0f}",
                "revenue_lines": _fmt_lines(revenue_req),
                "cogs_lines": _fmt_lines(cogs_req),
                "message": (
                    f"Sale recorded: revenue entry #{entry_ids[0]}, "
                    f"COGS entry #{entry_ids[1]}. "
                    f"qty_on_hand for '{item['name']}' is now {qty_on_hand - _Decimal(quantity):.0f}."
                ),
                "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
