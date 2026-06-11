SaltStocks — MCP Server Specification
Goal: expose live resale accounting data to Claude via a custom remote MCP connector, enabling real financial conversation without spreadsheet uploads or context drift.
Prepared for Claude Code implementation — June 2026

1. Context and Goal
SaltStocks is a FastAPI/SQLite resale accounting application at hyfeagin/saltstocks. It tracks eBay collectibles inventory (Funko Pops, etc.) using weighted average costing (WAC), records double-entry journal entries for purchases and sales, and produces standard financial reports.
The current pain point: asking Claude about financial performance requires manually exporting data to a spreadsheet and uploading it to a new conversation, which then loses context as the conversation grows. The fix is a read-only MCP server that Claude can connect to as a custom connector, pulling live data on demand in any conversation.
Because SaltStocks is already hosted and publicly reachable, the deployment work for this spec is minimal compared to a greenfield server. The primary work is implementing the MCP layer on top of the existing service layer.
Relationship to existing backlog
The Plain English Financial Summary (Backlog Item 1) and AI-Powered P&L Explainer (Backlog Item 2) are in-app features that render in the SaltStocks UI. The MCP server is a separate interface that exposes the same underlying data to Claude conversations outside the app. They serve different surfaces and do not replace each other. The MCP tools should reuse the same service/query functions built for those backlog items rather than duplicating query logic.
Stack
FastAPI, SQLite, Python. Existing relevant internals:
•	items.unit_cost — weighted average cost (WAC), updated by _resolve_inventory_purchase_context()
•	_resolve_sale_inventory_context() — reads unit_cost as COGS rate at time of sale
•	journal_entries + journal_lines — double-entry ledger; journal_lines.inventory_item_id FK on COGS lines
•	inventory_lots table (if implemented) — qty_received, qty_remaining, unit_cost frozen at receipt, journal_entry_id
•	items.status — kanban statuses from RESALE_STATUSES (available, listed, sold, etc.)
•	Chart of accounts: COGS ~5000, Inventory ~1200, Revenue ~4000, eBay Fees expense, Shipping expense, Cash/AR. Verify exact codes from the live chart before referencing them in any tool response.
Non-goals (v1)
•	No write operations of any kind. The MCP server is read-and-simulate only.
•	No MCP resources or prompts — tools only.
•	No duplication of in-app AI features (Plain English Summary, P&L Explainer) — reuse their query layer.
•	No multi-user support.
2. Architecture
•	Implement the MCP server using FastMCP (or the official MCP Python SDK) with the Streamable HTTP transport. Do not build SSE-only — Claude is phasing it out.
•	Mount the MCP app inside the existing FastAPI application at /mcp. It shares the same SQLite connection/session factory. No separate process.
•	All tool implementations call existing service/repository functions where they exist. Extract shared query functions if needed. Never duplicate SQL.
•	All money values returned as decimal strings with two decimal places plus an explicit currency field. No raw floats.
•	Every response includes as_of (ISO 8601 timestamp).
•	Tool responses are compact aggregated objects. Never return full row dumps or unbounded transaction lists.
3. Tool Catalog
Implement exactly these six tools in the order listed. Tool descriptions are part of the contract — Claude uses them for tool selection.
3.1 get_pnl_summary
Description: "Returns the SaltStocks profit and loss summary: revenue, COGS, gross profit, expense breakdown, and net income for a period. This is the primary tool for any question about profitability, income, or business performance."
Parameters: period ('mtd' | 'ytd' | 'all_time', required), start_date (ISO date, optional — overrides period if provided), end_date (ISO date, optional)
Returns: { as_of, currency, period_label, revenue, cogs, gross_profit, gross_margin_pct, expenses: [{ account_name, amount }], total_expenses, net_income, items_sold_count }
Source: aggregate from journal_lines joined to journal_entries filtered by date. Verify account codes against the live chart of accounts before hardcoding any account number filters.
3.2 get_inventory_snapshot
Description: "Returns the current state of resale inventory: every item with quantity on hand, weighted average cost, and status. Use for questions about what is in stock, total inventory value, or items by status."
Parameters: none.
Returns: { as_of, currency, total_inventory_value, total_items_count, by_status: [{ status, count, value }], items: [{ id, sku, name, category, status, qty_on_hand, unit_cost, total_cost, lot_id? }] }
Include lot_id on items that belong to an inventory_lots record, if that table exists in the repo at implementation time. If not, omit without error.
3.3 get_item_detail
Description: "Returns full detail for one inventory item by SKU or id: cost history, sale event if the item has sold, net profit, and lot membership if applicable. Use for questions about a specific item."
Parameters: item_id (string, required — accepts either internal id or SKU)
Returns: { as_of, id, sku, name, category, status, unit_cost, qty_on_hand, purchase_entries: [{ date, qty, unit_cost, journal_entry_id }], sale_event?: { date, sale_price, ebay_fees, shipping, net_payout, cogs, gross_profit, margin_pct, journal_entry_id }, lot?: { lot_id, qty_received, qty_remaining, purchase_date } }
3.4 get_sale_history
Description: "Returns eBay sale performance over a date range: monthly totals and per-item detail including revenue, COGS, fees, and net profit. Use for trend questions and questions about how sales are going."
Parameters: start_date (ISO date, optional, default 90 days ago), end_date (ISO date, optional, default today), group_by ('month' | 'item', optional, default 'month')
Returns when group_by='month': { as_of, currency, by_month: [{ month, items_sold, revenue, cogs, fees, shipping, net_profit, margin_pct }], totals: { items_sold, revenue, cogs, fees, net_profit } }
Returns when group_by='item': { as_of, currency, items: [{ id, sku, name, sale_date, sale_price, cogs, fees, shipping, net_profit, margin_pct }], totals: {...} }
3.5 simulate_ebay_sale
Description: "Simulates the accounting outcome of listing and selling an item at a given price: estimated eBay fees, expected net payout, COGS at current WAC, and gross profit. Use for pricing decisions and what-if questions before listing."
Parameters: item_id (string, required), list_price (decimal string, required), shipping_cost (decimal string, optional, default '0'), ebay_fee_rate (decimal string, optional, default '0.1325' — the standard 13.25% combined rate)
Returns: { as_of, item: { id, sku, name, unit_cost }, list_price, estimated_ebay_fee, shipping_cost, net_payout, cogs, gross_profit, margin_pct, break_even_price, note: 'Simulation only — no data written.' }
Fee calculation: ebay_fee = list_price * ebay_fee_rate. net_payout = list_price - ebay_fee - shipping_cost. gross_profit = net_payout - unit_cost. break_even_price = unit_cost / (1 - ebay_fee_rate) rounded up to nearest cent. Document the fee-rate assumption in the tool description and in the response note field.
3.6 get_recent_journal_entries
Description: "Returns recent journal entries with their debit/credit lines. Use for accounting questions about specific transactions, verifying a sale was recorded correctly, or reviewing the ledger."
Parameters: limit (integer, optional, default 10, max 50), start_date (ISO date, optional), account_code (string, optional — filter to entries that have a line touching this account)
Returns: { as_of, entries: [{ id, date, description, is_void, lines: [{ account_code, account_name, debit, credit, inventory_item_id? }] }] }
Never return more than 50 entries in a single call. If the caller needs more, they should narrow the date range or account filter.
4. Auth and Connector Registration
SaltStocks is already hosted and publicly reachable, so the only new infrastructure work is adding auth to the MCP endpoint and registering the connector.
•	Transport: Streamable HTTP. Mount at a stable path, e.g. https://[your-hosted-domain]/mcp.
•	Auth: implement OAuth 2.1 using FastMCP's auth provider support. Do not ship this endpoint without auth — it exposes real financial data. If in-app OAuth is too heavy for the sprint, front the /mcp path with a bearer token check as a v1 placeholder, with a TODO comment for OAuth migration.
•	Claude.ai registration: Settings → Connectors → Add custom connector → paste the /mcp URL. Works on web and mobile once added.
•	The MCP endpoint connects from Anthropic's cloud (not Holly's device), so any IP allowlist on the hosted server must include Anthropic's ranges. See Anthropic IP addresses documentation for the current list.
5. Acceptance Criteria
#	Criterion
1	All six tools are registered and discoverable via MCP tools/list with the exact names and descriptions specified in Section 3.
2	get_pnl_summary 'ytd' net_income matches the P&L report total shown in the SaltStocks UI for the same year-to-date period (within rounding tolerance of $0.01).
3	get_inventory_snapshot total_inventory_value equals the sum of (qty_on_hand * unit_cost) across all items with qty_on_hand > 0.
4	simulate_ebay_sale note field always reads 'Simulation only — no data written.' and calling it produces zero database mutations (verified by row-count check before/after).
5	get_item_detail for a sold item returns a sale_event block with correct gross_profit matching the journal entry lines for that sale.
6	simulate_ebay_sale break_even_price for any item satisfies: (break_even_price * (1 - ebay_fee_rate)) - unit_cost >= 0 (i.e., at break-even price the gross profit is >= $0).
7	All monetary fields are decimal strings with exactly two decimal places. No floats appear anywhere in any tool response.
8	Every tool response includes a valid ISO 8601 as_of timestamp.
9	No tool can mutate any database row. Verified by a test that records counts and values before calling each tool and asserts no change after.
10	Unauthenticated requests to /mcp are rejected with 401.
11	get_recent_journal_entries with no filters returns at most 10 entries. Passing limit=51 is rejected with a 400 or clamped to 50.
12	All six tools execute successfully end-to-end from a claude.ai conversation connected to the custom connector.
6. Files to Create / Modify
Inspect the actual repo structure before mapping these paths. Adapt as needed.
•	app/mcp/server.py — FastMCP app, tool registrations, auth wiring, mount point
•	app/mcp/tools.py — tool implementations delegating to service layer
•	app/services/mcp_queries.py — shared query functions for MCP tools (reuse or extract from existing services; do not duplicate SQL)
•	app/services/ebay_fee_calculator.py — fee/margin math for simulate_ebay_sale (pure functions, unit-tested)
•	app/main.py — mount MCP app at /mcp
•	tests/test_mcp_tools.py, tests/test_ebay_fee_calculator.py
•	README.md — add connector registration instructions
7. Out of Scope (v1)
•	Write operations: logging sales, editing items, importing eBay orders through the MCP server. Candidate for v2.
•	A get_balance_sheet tool — add once the balance sheet in the app UI is confirmed accurate.
•	Lot-aware COGS simulation (break-bulk/repackaging) — add after lot tracking is fully implemented.
•	Debt Thermometer data — separate MCP server, separate spec.
•	MCP resources and prompts.

Hand this document to Claude Code with the instruction: “Inspect the repo first. Adapt Section 6 paths to match the actual structure. Reuse existing service/query functions wherever they exist — do not duplicate SQL. Implement tools in order 3.1–3.6, then verify every row of the Section 5 acceptance criteria table before reporting done.”
