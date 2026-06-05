# Changelog

All notable changes to SaltStocks are documented here. Versions follow [Semantic Versioning](https://semver.org/).

---

## [2.2.0] — 2026-05-08

### Added — Split Purchase Flow (Mixed Inventory + Expense on One Receipt)

- New `ExpenseSplit` Pydantic model added to `app/accounting/schemas.py`; `TransactionAnswerSet` gains an `expense_splits: list[ExpenseSplit] = []` field.
- Two new questionnaire steps added to `BUY_INVENTORY` and `BUY_INVENTORY_PERSONAL` flows (after freight-in / purchase-tax):
  - **"Was anything else on this receipt NOT for resale inventory?"** — required yes/no gate
  - **"What non-inventory items were on this receipt?"** — checkbox list of all active expense accounts, each with a live amount field; only shown when previous answer is "yes"
- New `app/templates/accounting/_step_expense_splits.html` partial — interactive expense picker with per-row enable/disable and live expense subtotal display.
- `_build_split_purchase_lines()` added to `routes.py` — constructs multi-debit journal entries: separate Dr. lines for inventory and each expense category, single Cr. to payment account or Owner Contributions for the full total.
- Inventory debit uses only the capitalized portion (item subtotals + freight + purchase tax); expense splits are debited to their respective expense accounts — the accounting distinction is correctly preserved.
- `build_answer_set` in `questionnaire.py` automatically adds expense split totals into the computed `total_amount`.
- Confirm screen shows each expense split as a chip (account name + amount) alongside inventory items.
- Plain-English summary updated: e.g., *"You bought 10 × 'Funko Pops' for $50.00 + $3.00 shipping + $12.50 in other expenses = $65.50 total."*

### Added — AI Assist: Detect & Route Mixed Inventory + Expense Purchases

- `NLPResult` gains three new fields: `is_split_purchase: bool`, `inventory_amount: Optional[str]`, `expense_splits: Optional[list[dict]]`.
- System prompt updated with explicit mixed-purchase detection rules — model identifies when a description contains both inventory and non-inventory items, assigns splits to the closest chart-of-accounts codes, and validates `inventory_amount + Σ(splits) == total_amount` before returning.
- Math sanity check in `parse_transaction` clears the split if amounts don't add up within $0.02, preventing bad prefills.
- `_prefill_session_from_nlp` auto-fills `has_expense_splits = "yes"` and the full `expense_splits` list when AI confidence ≥ 0.70 — user skips those steps and lands directly at the inventory picker.
- When AI confidence on the split is below threshold, the yes/no question appears in the questionnaire for manual confirmation — no broken states on fallback.

### Fixed

- `_build_summary_text`: corrected undefined `item_desc` variable reference in the `SELL_INVENTORY_CASH` summary branch (now uses `inventory_item_name`).

---

## [2.1.0] — 2026-05-08

### Added — AI Accounting Assistant (Phase 5)

- New `app/accounting/nlp.py` — sends transaction description + template catalog + chart of accounts to Claude Haiku via the Anthropic Messages API; returns a partial `TransactionAnswerSet` with per-field confidence scores (0.0–1.0).
- `POST /accounting/entry/parse` — JSON API: `{description}` → partial answer set + list of remaining low-confidence steps + redirect URL.
- `POST /accounting/entry/ai-start` — HTML handler: parses description, prefills a questionnaire session, redirects to first unanswered step.
- Any field with confidence < 0.70 (`CONFIDENCE_THRESHOLD`) is left unanswered; the questionnaire presents that step for manual entry.
- `GET/POST /accounting/settings/ai` — AI mode enable/disable toggle and Anthropic API key storage.
- Fallback: if the API is unreachable or the key is missing, a notice is displayed and the questionnaire chooser opens directly.

### Changed — Entry Chooser Redesign

- Replaced the flat template-dropdown chooser with a two-column card layout: **Personal Funds** (green tint) vs. **Business Spending** (blue tint).
- Each card contains rows for inventory purchase, 13 expense categories, and owner transaction types — clicking a row starts the questionnaire with that template pre-selected.
- "Other" pill row below the cards for SELL_INVENTORY_CASH, PAY_CREDIT_CARD, SALES_TAX_REMITTED, OTHER_INCOME.
- Natural-language AI input card appears at the top when AI mode is enabled.

### Changed — Multi-Item Inventory Purchase Picker

- `inventory_link` questionnaire step redesigned to support unlimited SKUs per shipment via an "+ Add another item" button.
- Each row has: existing-item dropdown (or "New item" free-text tab), quantity field, and unit cost field (4 decimal places).
- Running subtotal updates live; freight-in note shown below.
- Answer serializes as a JSON array; backend processes each line through `allocate_freight_in`, distributing freight + purchase tax proportionally across all SKUs.

### Added — Purchase Tax Field

- New `purchase_tax_amount` questionnaire step on BUY_INVENTORY / BUY_INVENTORY_PERSONAL flows (immediately after freight-in).
- Purchase tax is capitalized into inventory cost — same allocation logic as freight-in.
- `TransactionAnswerSet.purchase_tax_amount` field added to schema.
- Confirm screen shows a "Purchase tax" chip when non-zero.

### Changed — Unit Cost Precision

- Inventory unit cost input now accepts 4 decimal places (`$0.1807`, `$0.1681`, etc.) via `step="0.0001"` on the HTML input.
- All unit cost `Decimal` quantization updated to `Decimal("0.0001")` throughout schema, questionnaire, and routes.
- Entry totals and subtotals remain at 2 decimal places.

### Added — Dashboard Overhaul

- "Owed back to you" balance widget added to the main dashboard — shows running Owner Contributions − Owner Draws total with a one-click "Reimburse myself" shortcut.
- Financial health panel with recent accounting entries.
- Quick-record shortcuts for common entry types on the dashboard.

### Added — Wave CSV Import Utility

- New `wave_import.py` script reads a Wave accounting export and produces records compatible with SaltStocks journal entries — intended as a one-time migration tool for users moving off Wave.

---

## [2.0.1] — 2026-05-07

### Changed — Backup Includes Receipt Files

- `backup_now()` in `app/routers/dashboard.py` now copies the full `data/receipts/` directory into the backup archive alongside the SQLite snapshot.
- Receipts are written to a `receipts_{timestamp}/` subfolder matching the `.db` backup filename.
- Backup is now a complete restore point: database + all attached receipt images and PDFs.

---

## [2.0.0] — 2026-05-06

### Added — Full Double-Entry Accounting System (Phases 1–4)

This release adds a complete bookkeeping layer to SaltStocks covering the full transaction lifecycle: data entry → journal posting → reports → tax export.

**Core engine (`app/accounting/`)**
- `posting.py` — `post_entry()` / `post_entries()`: atomic write with debit/credit balance validation (`UnbalancedEntryError`, `EmptyEntryError`); `void_entry()` writes a reversing pair and marks both rows `is_void=True`.
- `allocate_freight_in()` — dollar-weighted freight allocation across multi-SKU shipments; last item absorbs rounding remainder per GAAP.
- `catalog.py` — 24 transaction templates: BUY_INVENTORY, BUY_INVENTORY_PERSONAL, BUY_EXPENSE_PERSONAL, REIMBURSE_OWNER, SELL_INVENTORY_CASH, COGS_RECOGNITION, BUSINESS_MEAL, TRAVEL_HOTEL, TRAVEL_TRANSPORT, OFFICE_SUPPLIES, SOFTWARE_SUBSCRIPTION, SHIPPING_OUTBOUND, EBAY_FEES, PAYMENT_PROCESSING_FEE, UTILITIES, RENT, PROFESSIONAL_SERVICES, BANK_FEE, OWNER_CONTRIBUTION, OWNER_DRAW, PAY_CREDIT_CARD, SALES_TAX_REMITTED, OTHER_EXPENSE, OTHER_INCOME.
- `schemas.py` — `TransactionAnswerSet` Pydantic model (canonical answer schema shared by questionnaire and AI paths), `InventoryLink`, `LineItem`.

**Database**
- `accounts` table — chart of accounts seeded on first boot with system-protected defaults across asset, liability, equity, revenue, and expense types.
- `journal_entries` and `journal_lines` tables — debit/credit CHECK constraint (exactly one side must be zero per line).
- `receipts` table — SHA-256-deduplicated file storage under `data/receipts/YYYY/MM/`.
- `sales_tax_rates` table — jurisdiction + rate, seeded with North Carolina default.
- `questionnaire_sessions` table — server-side session persistence for in-progress questionnaire flows.

**Questionnaire engine**
- `questionnaire.py` — `Step` / `QuestionnaireSession` / `GLOBAL_FLOW`: a single ordered step list with `shown_when` lambdas driving all branching. Sessions survive browser refresh; Back navigation undoes the last answer.
- Step input types: `multi_choice`, `number`, `date`, `text`, `account_picker`, `inventory_picker`, `file_upload`.
- Confirm screen: plain-English summary card, collapsible accounting-details table with debit/credit columns, receipt upload.

**Inventory integration**
- `BUY_INVENTORY` / `BUY_INVENTORY_PERSONAL`: creates new SKU or increments existing qty; recalculates unit cost via weighted average including freight-in allocation.
- `SELL_INVENTORY_CASH`: automatically posts a paired `COGS_RECOGNITION` entry (Dr. 5000 COGS / Cr. 1200 Inventory) at the item's recorded unit cost.
- eBay import hook: auto-posts `COGS_RECOGNITION` for every line item deducted during import.

**Reports (Phase 3)**
- `GET /accounting/reports/pnl` — Profit & Loss: revenue, COGS, gross profit, operating expenses, net income. HTML + CSV.
- `GET /accounting/reports/balance-sheet` — Assets, liabilities, equity snapshot. HTML + CSV.
- `GET /accounting/reports/expenses-by-category` — Expense breakdown by account. HTML + CSV.
- `GET /accounting/reports/ledger/{account_id}` — Chronological general ledger with running balance.
- `GET /accounting/reports/year-end-export/{year}` — ZIP bundle: all CSVs + receipts folder + SQLite snapshot.
- `GET /accounting/reports/sales-tax` — Collected, remitted, and net liability summary.

**Sales tax (Phase 4)**
- `GET/POST /accounting/settings/sales-tax` — add/edit tax jurisdictions and rates.
- `SELL_INVENTORY_CASH` questionnaire: taxable yes/no → manual amount or auto-calculate from default jurisdiction rate.
- `SALES_TAX_REMITTED` template clears the Sales Tax Payable balance.

**Receipt vault (Phase 4)**
- Upload receipts during the questionnaire or from any entry detail page.
- `GET /accounting/receipts` — thumbnail/icon grid with filters (date range, has/missing receipt, template type); click through to the linked entry.

**Owner balance (Phase 4)**
- "Owed back to you" widget on the dashboard — Owner Contributions balance minus Owner Draws.
- `GET /accounting/owner-balance` — detail page with contributions list, draws list, running totals, and "Reimburse myself" button that pre-fills the REIMBURSE_OWNER questionnaire.

**Chart of accounts UI**
- `GET /accounting/accounts` — grouped account list (asset / liability / equity / revenue / expense).
- `POST /accounting/accounts` — add custom accounts (name, type, subtype).
- `POST /accounting/accounts/{id}` — rename or toggle `is_active`; system-protected accounts block deactivation.

**Entry history**
- `GET /accounting/entries` — paginated list with date, template, vendor, amount, void status; filters by date range and template.
- `GET /accounting/entries/{id}` — detail view with full journal lines, receipt thumbnails, and void button.
- `/accounting/entry/manual` retained for developer/admin direct entry.

---

## [1.11.0] — 2026-05-04

### Added — Inventory List Filters

- Filter controls added to `/resale`: status, channel, condition, category, location, and tag.
- Filters are additive (AND logic) and persist in the URL query string — results are shareable and bookmarkable.
- Distinct condition, category, and location values queried dynamically from the database for dropdown population.
- Active-filter badge and "Clear filters" link appear when any filter is active.
- Works alongside the existing text search box without conflict.

---

## [1.10.0] — 2026-04-30

### Changed — Resale Inventory UI Refresh

- Added page-specific static asset support for richer resale UI work.
- Rebuilt `/resale` into client-side tabs for Inventory, Import, and Bulk Edit without changing existing form actions.
- Replaced the inventory table with a compact card-list default view and a kanban-by-status view persisted in `localStorage` under `rs-view-mode`.
- Added contextual inline bulk edit controls that appear only when one or more resale items are selected.
- Added `POST /resale/{id}/status` for kanban drag-and-drop status changes with JSON success/error responses.
- Kept search, export, CSV import, qty adjust, and edit flows on the existing resale endpoints.
- Restyled the resale create/edit form to match the new inventory theme using the shared resale stylesheet.

### Validation

- `python -m py_compile` passed for the updated FastAPI modules.
- Jinja template compilation passed for `base.html` and `resale_list.html`.
- Live app smoke test covered startup plus requests to `/resale` and `/static/css/resale.css`.
- Follow-up live smoke test confirmed `/resale/new` renders successfully with the themed form.

---

## [1.9.0] — 2026-04-30

### Refactored — Extract Resale JOIN Query Helper

- Added `fetch_resale_rows(conn, q=None)` to `app/utils.py` — the full `SELECT i.* … LEFT JOIN resale_listings` query, with optional search filter on name, SKU, and tags.
- Added `fetch_resale_item_by_id(conn, item_id)` to `app/utils.py` — single-row lookup by id for the edit form.
- Removed `_fetch_resale_rows()` private helper from `resale.py`; the `resale_list` inline duplicate is also gone.
- `resale_export_csv` query left intact — it uses an explicit column list for CSV header alignment and is intentionally separate.
- Unused `Dict` and `Any` typing imports removed from `resale.py`.

### Added — Native Desktop Window (`python3 -m app.cli window`)

- New `window` subcommand in `app/cli.py` wraps the app in a native macOS window using `pywebview` (WKWebView under the hood — no Electron, no extra runtime).
- Uvicorn starts in a background thread; the app polls until the server is ready before opening the window.
- Window title: **SaltStocks**, 1280×800 default, 800×600 minimum.
- Closing the window cleanly shuts down the server.
- Falls back gracefully with instructions if `pywebview` is not installed.
- `pywebview==6.2.1` added to `requirements.txt`.

---

## [1.8.0] — 2026-04-30

### Refactored — `clean_str()` Helper for Strip-or-None Pattern

- Added `clean_str(val, default=None)` to `app/utils.py` — strips whitespace and returns `default` (None by default) when the result is empty.
- Replaced 17 occurrences of `x.strip() or None` / `x.strip() or "default"` in `resale.py` and 8 occurrences of `(x or "").strip()` / `(x or "default").strip()` in `config.py`.

### Refactored — Constants File for Status/Channel/Environment Values

- New `app/constants.py` is the single source of truth for all hardcoded string enums: `RESALE_STATUSES`, `RESALE_CHANNELS`, `EBAY_ENVIRONMENTS`, `ITEM_TYPE_RESALE`, and their defaults.
- Default values derive directly from the lists (`DEFAULT_STATUS = RESALE_STATUSES[0]`) so they can never drift out of sync.
- Constants registered as Jinja2 template globals in `app/deps.py` — all templates receive them automatically with no per-route changes needed.
- All three routers (`resale.py`, `config.py`, `ebay.py`) and `app/utils.py` updated to import from constants.
- Status and channel dropdowns in `resale_form.html`, `resale_list.html`, and the environment dropdown in `config.html` now loop over the constant lists. Adding a new status or channel is a one-line change in `constants.py`.

---

## [1.7.0] — 2026-04-30

### Refactored — DB Connection Dependency Injection

- Replaced the manual `conn = get_conn()` / `conn.close()` pattern (27 call sites, 16 close calls) across all routers with a FastAPI `Depends(get_db)` dependency.
- Added `get_db()` generator to `app/deps.py` — yields an open connection and closes it in a `finally` block, guaranteeing cleanup on every request including exceptions and early returns.
- All four routers updated: `dashboard.py`, `config.py`, `resale.py`, `ebay.py`.
- Connection leaks are now structurally impossible — the framework owns the lifecycle.

### Added — CLI Launch with Auto-Open Browser

- New `app/cli.py` entry point replaces the manual run-script + navigate workflow.
- Run `python3 -m app.cli serve` from the project directory to start the server and open the browser automatically.
- Supports `--port`, `--no-browser`, and `--no-reload` flags.

---

## [1.6.0] — 2026-03-25

### Added — eBay OAuth In-App Token Flow

- New **Connect to eBay** button on the Settings → eBay section launches a guided OAuth flow entirely within the app — no terminal scripts or manual token exchange required.
- `/ebay/oauth/start` builds the eBay authorization URL from saved credentials and presents a paste-back form for the redirect URL.
- `/ebay/oauth/exchange` receives the pasted redirect URL, extracts the authorization code, exchanges it for tokens via eBay's OAuth endpoint, and saves the new refresh token directly to the database.
- Robust code extraction handles eBay's `#`-containing auth codes correctly — browsers that decode `%23` to `#` in the address bar no longer produce a truncated/invalid code.
- New `ru_name` field added to `ebay_credentials` table (migration auto-runs on startup). RuName is required for the OAuth flow and stored per environment.
- Error pages at `ebay_oauth_error.html` and `ebay_oauth_start.html` give clear feedback if credentials are missing or eBay returns an error.
- Config page updated: RuName input, inline setup instructions, and environment-aware JS profile switcher now includes `ru_name`.

---

## [1.5.0] — 2026-03-15

### Refactored — Router Split (main.py → modular routers)

- Split monolithic `app/main.py` (1,694 lines) into four focused router modules:
  - `app/routers/dashboard.py` — dashboard and backup routes
  - `app/routers/resale.py` — all `/resale/*` routes (list, create, edit, adjust, bulk update, CSV import/export)
  - `app/routers/config.py` — all `/config/*` and `/settings/ebay` routes
  - `app/routers/ebay.py` — all `/ebay/*` routes with a shared error-response helper
- Extracted shared code into two new modules:
  - `app/deps.py` — shared `templates` object, `BACKUP_DIR`, and `TEMPLATES_DIR`
  - `app/utils.py` — shared helper functions: `normalize_code`, `get_next_sku`, `get_ebay_profile_bundle`, `missing_ebay_credentials`, and eBay line-item extraction helpers
- `app/main.py` reduced to 26 lines (app setup + startup hook only)

### Fixed — CSV Export `brand` Column Crash

- Removed `i.brand` from the CSV export query and header row; replaced with `brand_code` which is the correct column name in the `items` table. Previously, any CSV export would crash at runtime with a "no such column" error.

### Changed — DEV_GUIDE Rewritten

- Removed ~450 lines of duplicate and outdated content (old "Codex" workflow, Option A/B sections, emoji commentary)
- Updated migration section to reflect that `migrate()` now runs automatically on startup
- Added "Adding a New Integration" section using the eBay pattern as a reference template
- Consolidated troubleshooting into a clean reference section

---

## [1.4.0] — 2026-03-13

### Added — Project Backlog

- Created `docs/BACKLOG.md` to track prioritized features and improvements with status, goals, acceptance criteria, and file references.
- Initial backlog items: Square API inventory sync, CSV export `brand` column crash fix, schema init fix (resolved), eBay status filter UI cleanup, and CSV import preview-first/transactional flow.

### Fixed — Auto-Run Migration on Startup

- `migrate()` is now called automatically on app startup, immediately after `init_db()`. A fresh database no longer requires manually running `python app/migrate.py` — all columns, tables, and seed data are applied on first boot.

### Changed — Changelog Reformatted

- Rewrote `Changelog.md` with semantic versioning, dates, and per-entry categories (Added / Fixed / Improved). Removed internal file paths and dev commands that don't belong in a changelog.

---

## [1.3.0] — 2026-02-21

### Fixed — eBay Import: Filter & Dry-Run Parsing

- Fixed `getOrders` filter construction that caused invalid `orderfulfillmentstatus:{COMPLETED}` API requests. Status filtering is now skipped server-side; date-range filtering is used exclusively for MVP safety.
- Added UI note informing the user that status filtering is coming in a future release.
- Improved eBay API error reporting to include HTTP status code and response body on failed OAuth/order calls.
- Fixed dry-run form parsing using a hidden + checkbox value pair (`dry_run=0` hidden, `dry_run=1` checkbox); backend now correctly sets dry-run only when value is `"1"`.

---

## [1.2.0] — 2026-02-20

### Added — eBay Credential Profiles (Sandbox & Production)

- Separate credential storage for `SANDBOX` and `PRODUCTION` eBay environments via a new `ebay_credentials` table.
- Active environment state tracked in a new `ebay_state` table.
- Migration backfill from legacy `ebay_settings` table preserves any previously saved credentials.
- Configurator UI: selecting `SANDBOX` or `PRODUCTION` in the dropdown now immediately swaps in that profile's saved keys.
- Import flow uses the currently active environment profile.

---

## [1.1.0] — 2026-02-20

### Added — eBay Order Import (MVP)

- eBay integration module with refresh-token OAuth grant and paginated `getOrders` support.
- New `ebay_settings` and `ebay_import_log` tables added to schema and migration.
- `/ebay/import` route with preview-first UI (defaults: last 7 days, status `PAID`, dry-run ON).
- Import preview shows matched, unmatched, already-imported, and oversold-clamped line items.
- Deductions applied only after explicit user confirmation; transactional writes with idempotency via unique `(order_id, line_item_id)`.
- Deduction rule clamps at zero: `new_qty = max(0, qty_on_hand - qty_sold)`.
- eBay settings UI added under Configurator tab; navigation updated.
- `requests` added to `requirements.txt`.

---

## [1.0.3] — 2026-01-28

### Improved — CSV Import & Export

- Improved resale CSV import to support bulk item management workflows.
- Updated CSV export format for consistency.

---

## [1.0.2] — 2026-01-21

### Fixed — Quantity Adjust Controls

- Fixed qty adjust form controls in the resale inventory view.

---

## [1.0.1] — 2026-01-10

### Added — Configurable Categories

- Added category management to the `/config` page.
- Categories are now configurable per business profile rather than hardcoded.

---

## [1.0.0] — 2026-01-08

### Initial Release

- Auto-generated, read-only SKUs using `COMPANY-BRAND-SEQUENCE` format (e.g., `GV-FUNKO-000001`).
- Resale inventory tracking: quantity, cost, condition, location, tags, status, channel.
- Bulk edit operations: status, channel, location, append tags, unit cost.
- One-click SQLite database backup.
- Local-first architecture — no hosting required; runs via FastAPI + uvicorn.
- SKU prefix system supports multiple businesses (`GV` for Geekery Vault, `UM` for Umivera).
