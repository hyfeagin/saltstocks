# SaltStocks Backlog

This file tracks prioritized features and improvements. Items are ordered by priority within each status bucket.

**Status legend:**
- `[ ]` Backlog — not started
- `[~]` In Progress
- `[x]` Done

---

## Sprint 1 – Receipt Vault + Purchase Intake

### Sprint Goal
Create an Accounting → New Purchase flow that:

- Uploads receipt
- Captures expense or inventory purchase
- Tracks funding source
- Tracks reimbursable status
- Stores receipt locally
- Saves transaction to SQLite
- Generates Wave-ready export CSV
- Generates reimbursement report

**Immediate payoff:** Eliminate spreadsheet bookkeeping chaos.

---

### Architecture Decisions

**Business Units:**
- Geekery Vault
- Umivera
- Yvessa
- Personal/Other

**Database Path:** `~/Documents/saltstocks/data/saltstocks.db`

**Menu Location:** Top Nav → Accounting → New Purchase

**Classification Branching:**
If "Inventory" selected → prompt for:
- SKU (existing or new)
- Quantity
- Cost per unit

---

### Database Changes

#### receipts table
- id (pk), original_filename, stored_path, sha256, vendor_guess, date_guess, total_guess, created_at

#### purchases table
- id (pk), purchase_date, vendor_name, total_amount, sales_tax_amount
- classification (inventory | expense), category, business_unit
- paid_from (biz_checking | biz_savings | personal_cc | cash)
- reimbursable (bool), notes, created_at

#### purchase_receipts table
- purchase_id, receipt_id

---

### File Storage System

Path: `~/Documents/saltstocks/receipts/YYYY/MM/vendor/`
Filename: `YYYY-MM-DD_vendor_total_flag.jpg`
Example: `2026-02-20_amazon_18.97_inventory_r.jpg`

---

### UI Tasks

#### Accounting Menu
- [ ] Add "Accounting" to top nav
- [ ] Add submenu: New Purchase, Purchase Log, Exports

#### New Purchase Page
- [ ] Receipt Upload
- [ ] Purchase Date, Vendor Name, Total Amount, Sales Tax (optional)
- [ ] Classification dropdown (Inventory | Expense)
- [ ] Category, Business Unit, Paid From dropdowns
- [ ] Reimbursable checkbox, Notes field

#### Conditional Inventory Section (when Classification = Inventory)
- [ ] Existing SKU dropdown OR Create New SKU field
- [ ] Quantity, Cost per unit
- On Save: insert purchase, insert inventory adjustment, increase qty, store cost

---

### Backend Tasks (FastAPI)

- [ ] POST /accounting/purchase
- [ ] GET /accounting/purchases
- [ ] POST /accounting/upload_receipt
- [ ] GET /accounting/export/journal
- [ ] GET /accounting/export/reimbursements

---

### Receipt Handling
- [ ] Compute SHA256 hash
- [ ] Check duplicate receipt
- [ ] Save to structured path
- [ ] Store in receipts table, link to purchase

---

### Export Logic

#### Journal Export (Wave Compatible)

| Paid From | Debit | Credit |
|---|---|---|
| personal_cc | Expense or Inventory | Owner Investment/Drawings |
| biz_checking | Expense | Business Checking |
| inventory | Inventory Asset | Owner Investment OR Biz Checking |

CSV Columns: Date, Description, Debit Account, Credit Account, Amount, Business Unit, Category, Receipt Path

#### Reimbursement Report
Filter: paid_from = personal_cc AND reimbursable = true
Output: Date, Vendor, Amount, Category, Business Unit, Receipt Path
Include: **TOTAL OWED TO HOLLY**

---

### Testing Tasks
- [ ] Upload receipt test
- [ ] Duplicate receipt detection
- [ ] Inventory branch test
- [ ] Expense branch test
- [ ] CSV export correctness
- [ ] Reimbursement total correctness
- [ ] Database write verification

---

### Sprint Timeline (14 days)
- Days 1–2: Database schema + migrations
- Days 3–4: Receipt upload + storage
- Days 5–6: New Purchase UI
- Days 7–8: Inventory branching
- Days 9–10: Export logic
- Days 11–12: Testing + cleanup
- Days 13–14: Polish + edge cases

**Scope guardrails (not this sprint):** bank feed automation, AI chat, COGS per sale, dashboard metrics, tax estimation, API integrations, rewriting inventory engine

---

## Feature Backlog

### [ ] Square API — Inventory Sync on Sale

**Goal:** When a sale is recorded in Square POS, deduct sold quantity from matching SaltStocks inventory item.

**Acceptance criteria:**
- Square API key credentials configurable (similar to eBay credential profile pattern)
- Manual "pull from Square" flow fetches recent orders — preview-first, idempotent
- Matched items (Square catalog item ID → SaltStocks SKU) have quantity decremented in SQLite
- Unmatched items surfaced in UI for manual review
- Dry-run mode available before committing

**Open questions:**
- Matching strategy: Square catalog item ID → SaltStocks SKU? (manual mapping vs. SKU in Square item notes?)
- Square sandbox credentials needed for dev

---

### [x] Fix CSV Export — Remove Non-Existent `brand` Column

**Priority:** High — runtime crash

**Goal:** Prevent CSV export from crashing due to missing column reference.

**Context:** Export query selects `i.brand`, but only `brand_code` exists in the `items` table.

**Acceptance criteria:**
- CSV export completes without error
- Remove `brand` from export query/header, or add column to schema if intended

**Files:** `app/main.py` (line 1259), `app/schema.sql`

---

### [x] Fix Schema Initialization — Fresh DB Works Without Manual Migration

**Priority:** High — architectural

**Goal:** Fresh database from `schema.sql` works without manually running `migrate.py` first.

**Context:** Columns `company`, `category`, `brand_code`, `ip` only exist after `migrate.py` runs.

**Acceptance criteria:**
- `schema.sql` includes all required columns, OR `migrate.py` auto-invoked on startup
- Fresh install flow documented in README/DEV_GUIDE

**Files:** `app/schema.sql`, `app/migrate.py`, `app/main.py`

---

### [ ] Disable Non-Functional Status Filter in eBay Import UI

**Priority:** Medium — UX / misleading feature

**Goal:** Remove or disable status filter dropdown in eBay import screen (has no effect).

**Context:** Dropdown shows PAID/FULFILLED/COMPLETED but backend ignores it entirely.

**Acceptance criteria:**
- Dropdown removed or rendered disabled with tooltip explaining it's coming soon

**Files:** `app/templates/ebay_import.html` (line 46), `app/main.py` (line 748)

---

### [ ] Make CSV Import Preview-First / Fully Transactional

**Priority:** Medium — data integrity

**Goal:** Preview before committing CSV imports; ensure atomic (all-or-nothing) transactions.

**Context:** Failed mid-import leaves partial data with no rollback. Inconsistent with eBay import flow.

**Acceptance criteria:**
- Dry-run/preview mode shows what would change before committing
- All rows succeed or none committed (single transaction)
- UI shows summary before confirming

**Files:** `app/main.py` (line 1354–1674), `app/templates/resale_list.html`

---

### [ ] eBay Deep Integration — Status Sync + Listing Creation

**Goal:** Two-way eBay integration: sync item status from eBay (listed/unlisted) back into SaltStocks, and allow listing a new item for sale directly from SaltStocks.

**Phase 1 — Status Sync:**
- Pull current listing status from eBay and update `resale_listings.status` (listed, unlisted, sold) for matched SKUs
- Run on demand (manual pull, same pattern as order import)
- Preview changes before applying

**Phase 2 — List Item from SaltStocks:**
- From an item's detail/edit page, trigger a "List on eBay" action
- Collect required eBay listing fields: title, description, price, condition, images, category, shipping details
- Submit via eBay Trading or Inventory API and store the resulting listing ID
- Update `resale_listings` with channel=ebay, status=listed, url

**Open questions:**
- eBay Inventory API vs. legacy Trading API — which is available on the current credentials?
- Image hosting: eBay requires publicly accessible URLs; local images need to be uploaded to eBay's image hosting first
- How much of the listing form should mirror the SaltStocks item form vs. be a separate eBay-specific step?

**Files:** `app/integrations/ebay.py`, `app/main.py`, `app/templates/`

---

### [ ] Inventory List Filters

**Goal:** Allow filtering the resale inventory list by one or more criteria beyond the current text search.

**Acceptance criteria:**
- Filter controls for: status (unlisted, listed, sold, etc.), channel (eBay, Etsy, local, etc.), condition, category, location, and tag
- Filters are additive (AND logic) and persist across the current session
- Filter state is reflected in the URL query string so results are shareable/bookmarkable
- Works alongside the existing search box

**Files:** `app/main.py` (resale_list route), `app/templates/resale_list.html`

---

### [ ] Search Clear Button (×)

**Goal:** Add a one-click clear button to the search box so users don't have to manually erase the search term and resubmit.

**Acceptance criteria:**
- An × button appears inside or beside the search input when the field is non-empty
- Clicking × clears the input and immediately reloads the list showing all items (no manual re-submit)
- Works without JavaScript frameworks — plain JS or an HTML form reset is fine

**Files:** `app/templates/resale_list.html`

---

### [ ] Reporting Dashboard

**Goal:** A dedicated dashboard page showing key business metrics at a glance.

**Suggested metrics (v1):**
- Total items in inventory, broken down by status (unlisted / listed / sold / donated / trashed)
- Total inventory value (qty × unit_cost) by business unit
- Items added this week / this month
- Top categories by item count
- eBay import history summary (last import date, total deductions applied)

**Acceptance criteria:**
- Accessible from the top nav
- All data pulled from SQLite — no external calls
- Loads fast; no heavy aggregation queries

**Files:** new route in `app/main.py`, new template `app/templates/dashboard.html`, update `app/templates/base.html` nav

---
Cancelled Business is moving to wholesale model and Recipes and batch making will no longer be the used as a business model
~~## [ ] Umivera COGS — Materials, Recipes & Batch Costing~~

~~**Goal:** Build the production cost tracking side of SaltStocks for Umivera bath salt products. Track what materials go into each product, calculate cost per unit, and record production batches.~~

~~**Data model:**~~
~~- `materials` table — ingredient inventory (name, unit, unit_cost, qty_on_hand)~~
~~- `recipes` table — each Umivera product SKU has a recipe~~
~~- `recipe_lines` table — (recipe_id, material_id, qty_per_batch)~~
~~- `batches` table — a production run (recipe_id, batch_size, date, notes)~~
~~- On batch save: deduct material qty, calculate total COGS, record cost per unit on the finished item~~

~~**Acceptance criteria:**~~
~~- Can define a recipe for a product (e.g., "16oz Lavender Bath Salts" uses X oz salt, Y oz oil, Z jar)~~
~~- Can record a batch (units produced, date)~~
~~- COGS per unit is calculated and stored on the item record~~
~~- Material stock is decremented when a batch is recorded~~
~~- Report shows COGS per batch and per unit~~

~~**Files:** new tables in `app/migrate.py`, new routes + templates, separate from resale flows~~

---

### [X] Auto-Launch on Mac (Terminal Double-Click)

**Goal:** Make it easy to start SaltStocks without opening Terminal and typing commands — double-click a file to launch.

**Acceptance criteria:**
- A `.command` file (or `.app` wrapper) in the project root starts the venv, runs the server, and opens the browser automatically
- If the server is already running, it should not start a second instance (check port 8000 before launching)
- Works on macOS without installing anything extra

**Notes:**
- `run.command` already exists but only starts uvicorn — extend it to also open the browser and add the port check
- Consider adding a matching stop script or using `lsof -ti:8000` to detect existing instance

**Files:** `run.command`

---

## Accounting Vision Backlog

Items sourced from the accounting vision document. These represent the evolution of SaltStocks from an inventory tracker into an accounting-aware inventory assistant.

---

### [ ] Plain-Language Event Entry

**Vision area:** A

**Goal:** Allow the user to describe a business event in ordinary language and have SaltStocks interpret it, classify it, and update inventory and bookkeeping records automatically.

**Examples:** "I bought this sock today for $10." / "Used 4 socks in spa bags." / "Sold 2 bags at the booth for $40 cash." / "Took 1 felted bar as a tester."

**Acceptance criteria:**
- System accepts transaction input in free-text / plain-language form
- Classifies event type: purchase, sale, inventory adjustment, bundle consumption, owner-funded purchase, owner draw/personal use, sample/display, damage/write-off
- Prompts for missing required context only when necessary
- Stores both the original user-entered text and the structured interpretation
- Maps event to accounting treatment and updates inventory accordingly
- User can review the interpretation before final save

**Notes:** This is a large feature that likely arrives after the Sprint 1 purchase intake is complete and proven out. Could start with a small set of recognized patterns.

---

### [ ] Purchase Lot Tracking & Multi-Cost Basis

**Vision area:** B (extension of Sprint 1)

**Goal:** Extend the Sprint 1 purchase ledger to support multiple lots per SKU at different unit costs, enabling accurate COGS calculation per lot.

**Acceptance criteria:**
- Each purchase can be assigned a lot or batch ID
- Multiple lots for the same SKU are stored with separate unit costs
- System can display purchase history (all lots) for a given SKU
- COGS calculation uses per-lot cost rather than a single average (or supports configurable method: FIFO, average)
- Inventory valuation uses actual lot costs

**Notes:** Builds directly on the `purchases` table from Sprint 1. Requires a `purchase_lots` or similar join table.

---

### [ ] Inventory Valuation Report

**Vision area:** C

**Goal:** Show the cost value (qty × unit_cost) of current on-hand inventory, filterable and exportable.

**Acceptance criteria:**
- Report shows on-hand quantity and cost value per SKU
- Filterable by: category, brand/line, channel allocation, status, business unit
- Shows total inventory value at the filtered and unfiltered level
- Non-sellable statuses can be excluded from "available for sale" views but included in value totals
- Exportable to CSV

**Notes:** Depends on unit_cost being tracked on items or lots (see Purchase Lot Tracking). Can start with a simple qty × item.unit_cost calculation before lots are implemented.

---

### [ ] Bundle/Kit Costing for Resale

**Vision area:** D

**Goal:** Support resale bundles — gift sets, convention assortments, spa bags — where multiple SKUs are grouped and sold together. Calculate bundle cost from components.

**Acceptance criteria:**
- A bundle product can reference component SKUs and required quantities
- System calculates total bundle cost from current cost basis of its components
- Bundle cost recalculates when component costs change (future builds)
- When a bundle is sold, the correct component quantities are deducted from inventory
- Reports show bundle margin based on recorded or proposed sale price

**Notes:** Different from Umivera COGS (which covers handmade production). This is for resale bundles with no manufacturing step. May share DB schema ideas but should be a separate flow.

---

### [ ] COGS Recognition on eBay / Square Import

**Vision area:** E (enhancement to existing integrations)

**Goal:** When an eBay or Square import runs and deducts quantity, also record the sale price, calculate COGS from the item's unit cost, and record gross profit per line item.

**Acceptance criteria:**
- Import records the sale price per line item (from the API response) in addition to deducting qty
- COGS is calculated from the item's recorded unit cost at time of sale
- Gross profit (sale price − COGS) is stored and viewable
- Fee, shipping, and discount fields are captured if available in the API response
- No duplicate COGS records if the same line item is re-imported

**Notes:** Enhancement to `app/routers/ebay.py` and the future Square integration. Requires `unit_cost` to be populated on items. Impacts the `ebay_import_log` table schema.

---

### [ ] Owner Draw / Personal-Use Inventory Events

**Vision area:** G (partial — Sprint 1 covers owner-funded purchases; this covers the reverse)

**Goal:** Record events where inventory is removed from stock for personal use, display, tester, or sample — and classify those removals correctly for bookkeeping.

**Acceptance criteria:**
- User can record an "owner draw" or "personal use" event against an inventory item
- Event records: item, quantity, date, reason (personal use / tester / display / sample / damage)
- Inventory qty is decremented on save
- Event is classified differently from a sale (owner draw vs. revenue)
- Reports can show total inventory removed via owner draw over a period
- Wave export includes these events with the correct accounting classification

**Notes:** Overlaps with the expanded inventory statuses (Gap 7). Status change to "tester" or "display" may be the trigger that generates this event.

---

### [ ] Expanded Inventory Statuses + Status History

**Vision area:** H

**Goal:** Support a richer set of inventory statuses that reflect actual business states, and record status changes historically rather than only storing current state.

**Additional statuses (beyond unlisted / listed / sold / donated / trashed):**
- reserved, booth-allocated, tester/sample, display-only, damaged, non-sellable, backstock

**Acceptance criteria:**
- All statuses above can be assigned to an inventory item
- Status changes are recorded in a history log (item, from_status, to_status, changed_at, reason)
- Availability views exclude non-sellable statuses
- Reports can show inventory by status and related cost value
- Accounting treatment can differ by status-change event type (e.g., "display" → owner draw classification)

**Notes:** Requires a `resale_listing_status_log` or similar history table. May drive the Owner Draw feature (Gap 6).

---

### [ ] Channel Allocation (Pre-Sale)

**Vision area:** I

**Goal:** Allow inventory to be allocated to a sales channel before it is sold — e.g., "this batch is set aside for the next booth" — so inventory can be mentally segmented by destination.

**Acceptance criteria:**
- Inventory can be allocated to a channel (booth, eBay, Etsy, backstock, etc.) without being listed or sold
- System reports inventory quantity and cost value by channel allocation
- System warns when total allocation for a SKU exceeds on-hand quantity
- Actual sale records retain the real channel/source
- Reports can show revenue, COGS, and gross profit by channel

**Notes:** Distinct from the existing `channel` field on `resale_listings` (which reflects where an item is actively listed). Allocation is a planning-level field, separate from listing state.

---

### [ ] Margin & Pricing Support

**Vision area:** J

**Goal:** Use cost basis and sale price data to help evaluate profitability and support pricing decisions.

**Acceptance criteria:**
- System stores proposed or actual selling price per SKU (separate from cost)
- System calculates: gross profit ($), gross margin (%), markup (%)
- User can set a target margin threshold; system flags items below it
- Margin is viewable per item, per category, and per channel
- Bundle margin is calculated using component-based cost

**Notes:** Depends on unit_cost being populated (Purchase Lot Tracking, Gap 2). Good candidate for a dedicated "Pricing" tab on the item edit page.

---

### [ ] Wave Export History + Export Marking

**Vision area:** K (enhancement to Sprint 1 export)

**Goal:** Extend the Sprint 1 Wave export to mark records as exported and prevent accidental duplicate export of the same events.

**Acceptance criteria:**
- Every record that can be Wave-exported has an `exported_at` timestamp (or a separate export log)
- Export report shows only unexported records by default; user can re-export intentionally
- Export history is visible: date, record count, exported by which run
- Re-export is controlled and requires explicit opt-in

**Notes:** Enhances the export routes added in Sprint 1. Likely a `wave_export_log` table (export_id, exported_at, record_count) and an `exported_at` column on purchases/events.

---

### [ ] Extended Financial Reports

**Vision area:** L

**Goal:** Add dedicated financial reports beyond the operational Reporting Dashboard — covering COGS, profitability, purchase history, and dead stock.

**Candidate reports:**
- Purchase history report (by SKU, vendor, date range)
- COGS report (what has been recognized as cost of goods sold)
- Gross profit report (revenue − COGS by period or channel)
- Owner contribution report (total personal-funded purchases)
- Dead stock / slow-moving stock report (items with zero movement over N days)
- Bundle profitability report

**Acceptance criteria:**
- Each report is filterable by date range
- Additional filters where applicable: SKU, category, channel, vendor
- Reports are exportable to CSV
- Report numbers are traceable to underlying transactions

**Notes:** This is a reporting layer on top of data that other backlog items will populate. Implement incrementally as the data becomes available.

---

### [ ] Inventory-to-Ledger Event Audit Trail

**Vision area:** F

**Goal:** Link every inventory-affecting event (purchase, sale, adjustment, draw, bundle assembly) to a structured accounting output, and allow users to trace what downstream records each event created.

**Acceptance criteria:**
- Every supported event type has a defined accounting treatment mapping
- Events generate a structured accounting output (debit account, credit account, amount, date, classification)
- Inventory changes and accounting outputs are linked to the originating event (foreign key / event ID)
- User can view an event trail: event → what downstream records it produced
- Ambiguous or unclassified events are flagged for review rather than silently posted

**Notes:** This is the connective tissue of the full accounting vision. Probably implemented as an `accounting_events` table + `accounting_entries` table, with foreign keys back to purchases, sales, adjustments. This is Phase 3 / post-MVP scope.

---

## Code Quality / Refactoring Backlog

These are internal improvements with no user-visible behavior change. Safe to do incrementally.

---

### [ ] DB Connection Dependency Injection

**Priority:** High — resource safety + DRY

**Goal:** Replace the manual `conn = get_conn()` / `conn.close()` pattern (repeated ~22 times across routers) with a FastAPI `Depends()` dependency that auto-closes the connection at end of request, making connection leaks impossible.

**Approach:**
```python
# app/deps.py
def get_db():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()

# In each route:
@router.get("/resale")
def resale_list(request: Request, conn = Depends(get_db)):
    ...  # no manual conn.close() needed
```

**Files:** `app/deps.py`, all files in `app/routers/`

---

### [ ] `clean_str()` Helper for Strip-or-None Pattern

**Priority:** High — DRY

**Goal:** Replace the `value.strip() or None` pattern (43+ occurrences across routers) with a shared helper in `app/utils.py`.

**Approach:**
```python
def clean_str(val: str, default: Optional[str] = None) -> Optional[str]:
    cleaned = (val or "").strip()
    return default if not cleaned else cleaned
```

**Files:** `app/utils.py`, `app/routers/resale.py`, `app/routers/config.py`

---

### [ ] Constants File for Status/Channel/Environment Values

**Priority:** High — single source of truth

**Goal:** Move all hardcoded string enums (`"unlisted"`, `"unassigned"`, `"ebay"`, `"SANDBOX"`, `"resale"`, etc.) into a single `app/constants.py` file so they can be changed in one place and imported everywhere.

**Approach:**
```python
# app/constants.py
RESALE_STATUSES = ["unlisted", "listed", "sold", "donated", "trashed"]
RESALE_CHANNELS = ["unassigned", "ebay", "etsy", "shopify", "local", "mercari", "whatnot"]
EBAY_ENVIRONMENTS = ["SANDBOX", "PRODUCTION"]
ITEM_TYPE_RESALE = "resale"
```

**Files:** new `app/constants.py`, `app/routers/resale.py`, `app/routers/config.py`, `app/routers/ebay.py`, `app/templates/resale_form.html`, `app/templates/resale_list.html`

---

### [ ] Extract Resale JOIN Query Helper

**Priority:** High — eliminates copy-paste SQL

**Goal:** The same `SELECT i.* LEFT JOIN resale_listings` query appears 4+ times across routers with minor variations. Extract into a shared helper in `app/utils.py`.

**Approach:**
```python
def fetch_resale_item_by_id(conn, item_id: int):
    return conn.execute("""
        SELECT i.*, COALESCE(rl.status,'unlisted') AS status, ...
        FROM items i LEFT JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.id=? AND i.item_type='resale'
    """, (item_id,)).fetchone()
```

**Files:** `app/utils.py`, `app/routers/resale.py`

---

### [ ] `upsert_resale_listing()` Helper

**Priority:** Medium — eliminates ~60 lines of duplicate insert/update logic

**Goal:** The insert-or-update pattern for `resale_listings` is copy-pasted in resale create, update, bulk update, and CSV import (~4 locations). Extract into a shared helper.

**Approach:**
```python
def upsert_resale_listing(conn, item_id, channel, status, list_price, url):
    existing = conn.execute("SELECT id FROM resale_listings WHERE item_id=?", (item_id,)).fetchone()
    if existing:
        conn.execute("UPDATE resale_listings SET channel=?, status=?, list_price=?, url=?, updated_at=datetime('now') WHERE item_id=?", ...)
    else:
        conn.execute("INSERT INTO resale_listings (item_id, channel, status, list_price, url) VALUES (?, ?, ?, ?, ?)", ...)
```

**Files:** `app/utils.py`, `app/routers/resale.py`

---

### [ ] Jinja2 Macros for Status/Channel Dropdowns

**Priority:** Medium — template DRY

**Goal:** The status and channel `<select>` dropdowns are hardcoded identically in `resale_form.html` and `resale_list.html`. Extract into reusable Jinja2 macros.

**Approach:** Create `app/templates/macros.html` with `status_dropdown` and `channel_dropdown` macros. Import and call them in both templates.

**Files:** new `app/templates/macros.html`, `app/templates/resale_form.html`, `app/templates/resale_list.html`

---

### [ ] Standardize Float Parsing in CSV Import

**Priority:** Medium — correctness + consistency

**Goal:** The bulk update handler silently ignores invalid float values, while CSV import raises an error. Unify both using a single `safe_parse_float()` helper in `app/utils.py`.

**Files:** `app/utils.py`, `app/routers/resale.py`

---

### [ ] `normalize_code()` Default Parameter

**Priority:** Low — minor cleanup

**Goal:** `normalize_code(value) or "GV"` appears ~20 times. Add a `default` parameter to the function to eliminate the `or` pattern at every call site.

**Approach:**
```python
def normalize_code(s: str, default: str = "") -> str:
    ...
    return result if result else default

# Usage becomes:
company_n = normalize_code(company, "GV")
```

**Files:** `app/utils.py`, `app/routers/resale.py`

---

### [ ] `render()` Template Response Shorthand

**Priority:** Low — minor boilerplate reduction

**Goal:** Replace the repeated `templates.TemplateResponse("name.html", {"request": request, ...})` pattern (~15 occurrences) with a one-liner helper.

**Approach:**
```python
# app/deps.py
def render(template_name: str, request: Request, **context):
    return templates.TemplateResponse(template_name, {"request": request, **context})
```

**Files:** `app/deps.py`, all router files
