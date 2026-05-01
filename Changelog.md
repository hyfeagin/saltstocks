# Changelog

All notable changes to SaltStocks are documented here. Versions follow [Semantic Versioning](https://semver.org/).

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
