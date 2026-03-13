# SaltStocks Backlog

This file tracks prioritized features and improvements. Items are ordered by priority within each status bucket.

**Status legend:**
- `[ ]` Backlog — not started
- `[~]` In Progress
- `[x]` Done

---

## [ ] Square API — Inventory Sync on Sale

**Goal:** When a sale is recorded in Square POS, deduct the sold quantity from the matching SaltStocks inventory item.

**Acceptance criteria:**
- Square API key credentials can be configured (similar to the eBay credential profile pattern already in the app)
- A manual "pull from Square" flow fetches recent Square orders — preview-first, idempotent (same UX pattern as eBay import)
- Matched items (by Square catalog item ID → SaltStocks SKU) have quantity decremented in SQLite
- Unmatched items are surfaced in the UI for manual review
- Dry-run mode available before committing changes (consistent with eBay import pattern)

**Open questions:**
- Matching strategy: how does Square catalog item ID map to a SaltStocks SKU? (manual mapping table vs. SKU stored in Square item notes?)
- Square sandbox credentials needed for development

---

## [ ] Fix CSV Export — Remove Non-Existent `brand` Column

**Priority:** High — runtime crash

**Goal:** Prevent the CSV export from crashing due to a missing column reference.

**Context:** The export query selects `i.brand`, but this column does not exist in the `items` table (only `brand_code` does). Any user who triggers a CSV export will hit a runtime error.

**Acceptance criteria:**
- CSV export completes without error
- Either remove `brand` from the export query and header row, or add a `brand` column to the schema and migration if it's intended to be a real field

**Files:** `app/main.py` (line 1259), `app/schema.sql`

---

## [x] Fix Schema Initialization — Fresh DB Works Without Manual Migration

**Priority:** High — architectural

**Goal:** A fresh database initialized from `schema.sql` should have all columns the app requires, without needing to manually run `migrate.py` first.

**Context:** Several columns the app depends on (`company`, `category`, `brand_code`, `ip`) only exist after `migrate.py` runs. If someone sets up the app from scratch without running the migration, the app crashes immediately on item create/edit.

**Acceptance criteria:**
- `schema.sql` includes all required columns, OR
- `migrate.py` is automatically invoked on app startup before the server starts accepting requests
- A fresh install flow is documented in README / DEV_GUIDE

**Files:** `app/schema.sql`, `app/migrate.py`, `app/main.py`

---

## [ ] Disable Non-Functional Status Filter in eBay Import UI

**Priority:** Medium — UX / misleading feature

**Goal:** Remove or visually disable the status filter dropdown in the eBay import screen since it has no effect.

**Context:** The dropdown shows options like PAID, FULFILLED, COMPLETED, but the backend ignores it entirely and always uses date-range-only filtering. A `# NOTE: MVP` comment in the code acknowledges this. Users who rely on this filter get incorrect results silently.

**Acceptance criteria:**
- Dropdown is either removed or rendered as disabled with a tooltip/note explaining it's coming soon
- No backend change needed

**Files:** `app/templates/ebay_import.html` (line 46), `app/main.py` (line 748)

---

## [ ] Make CSV Import Preview-First / Fully Transactional

**Priority:** Medium — data integrity

**Goal:** Give users a preview of what a CSV import will do before committing changes, and ensure the entire import is atomic (all-or-nothing).

**Context:** Currently, if a CSV import fails midway (e.g., row 51 of 100), rows 1–50 are already committed with no rollback. There's no way to undo a partial import without manual database edits. This is inconsistent with the eBay import flow, which is preview-first and transactional.

**Acceptance criteria:**
- CSV import has a dry-run / preview mode that shows what would change without committing
- On actual import, all rows succeed or none are committed (wrap in a single transaction)
- UI shows a summary of changes before confirming

**Files:** `app/main.py` (line 1354–1674), `app/templates/resale_list.html`

---
