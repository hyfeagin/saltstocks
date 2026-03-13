# Changelog

All notable changes to SaltStocks are documented here. Versions follow [Semantic Versioning](https://semver.org/).

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
