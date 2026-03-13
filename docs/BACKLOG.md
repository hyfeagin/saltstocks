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

### [ ] Fix CSV Export — Remove Non-Existent `brand` Column

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
