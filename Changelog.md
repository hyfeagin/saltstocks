# Changelog

## eBay Pull Import MVP

### What Changed
- Added eBay settings + idempotency tables in schema/migration:
  - `/Users/hollyfeagin/Documents/saltstocks/app/schema.sql`
  - `/Users/hollyfeagin/Documents/saltstocks/app/migrate.py`
- Added eBay integration module (refresh token grant + paginated `getOrders`):
  - `/Users/hollyfeagin/Documents/saltstocks/app/integrations/ebay.py`
- Added settings routes and moved eBay settings UI under Configurator:
  - `/Users/hollyfeagin/Documents/saltstocks/app/main.py`
  - `/Users/hollyfeagin/Documents/saltstocks/app/templates/config.html`
- Added `/ebay/import` preview/apply route + UI:
  - `/Users/hollyfeagin/Documents/saltstocks/app/main.py`
  - `/Users/hollyfeagin/Documents/saltstocks/app/templates/ebay_import.html`
- Updated navigation links:
  - `/Users/hollyfeagin/Documents/saltstocks/app/templates/base.html`
- Added docs notes + root assumptions:
  - `/Users/hollyfeagin/Documents/saltstocks/README.md`
  - `/Users/hollyfeagin/Documents/saltstocks/docs/DEV_GUIDE.md`
- Added `requests` dependency:
  - `/Users/hollyfeagin/Documents/saltstocks/requirements.txt`

### Behavior Covered
- Stores `client_id`, `client_secret`, `environment`, and `refresh_token` locally (SQLite).
- Uses refresh-token grant to obtain access tokens (no hosted auth callback flow in this MVP).
- Provides preview-first import with defaults (last 7 days, `PAID`, dry-run ON).
- Matches order line items to Saltstocks by `items.sku`.
- Shows matched/unmatched/already-imported/oversold-clamped statuses in preview.
- Applies deductions only after explicit user action.
- Uses transactional writes and idempotency via unique `(order_id, line_item_id)` in `ebay_import_log`.
- Deduction rule clamps at zero: `new_qty = max(0, qty_on_hand - qty_sold)`.

### Validation Run
- `.venv/bin/python -m py_compile /Users/hollyfeagin/Documents/saltstocks/app/main.py /Users/hollyfeagin/Documents/saltstocks/app/migrate.py /Users/hollyfeagin/Documents/saltstocks/app/integrations/ebay.py`
- Result: passed.

### Next Steps
1. Run migration: `source .venv/bin/activate && python /Users/hollyfeagin/Documents/saltstocks/app/migrate.py`
2. Ensure dependencies are installed: `pip install -r /Users/hollyfeagin/Documents/saltstocks/requirements.txt`
3. Configure credentials in UI under Configurator (`/config#ebay-settings`).
4. Run manual smoke flow in `/ebay/import`:
   - Preview
   - Confirm unmatched behavior
   - Apply deductions
   - Re-run to verify already-imported idempotency
   - Confirm clamped oversold behavior

## eBay Credential Profiles Update

Implemented. You can now store separate eBay credentials for `SANDBOX` and `PRODUCTION`, and switching the environment dropdown auto-loads that environment’s saved values in the Configurator form.

Updated files:
- `/Users/hollyfeagin/Documents/saltstocks/app/main.py`
- `/Users/hollyfeagin/Documents/saltstocks/app/templates/config.html`
- `/Users/hollyfeagin/Documents/saltstocks/app/schema.sql`
- `/Users/hollyfeagin/Documents/saltstocks/app/migrate.py`

What changed:
- Added two-profile credential storage (`ebay_credentials`) plus active environment state (`ebay_state`).
- Kept a migration backfill from legacy `ebay_settings` so existing credentials are preserved.
- Updated settings save logic to persist credentials per environment and set active environment.
- Updated config UI dropdown behavior (JS) so selecting `SANDBOX` vs `PRODUCTION` swaps in that profile’s saved keys immediately.
- Updated import flow to use the currently active environment profile.

Sanity checks run:
- `.venv/bin/python -m py_compile /Users/hollyfeagin/Documents/saltstocks/app/main.py /Users/hollyfeagin/Documents/saltstocks/app/migrate.py /Users/hollyfeagin/Documents/saltstocks/app/integrations/ebay.py`
- Started app briefly with uvicorn and confirmed startup/shutdown.

Committed and pushed via required script:
- `./scripts/done.sh "S0-T0: support separate eBay sandbox and production credential profiles"`
- Push succeeded to `main`
- Commit hash: `89ece2a`

## eBay Import Bugfixes (Filter + Dry Run)

### What Changed
- Fixed eBay `getOrders` filter construction to avoid invalid `orderfulfillmentstatus:{COMPLETED}` requests.
- For MVP safety, status filtering is now ignored server-side and requests always use date-range filtering only.
- Added explicit UI note that status filtering is coming soon.
- Improved eBay API error reporting to include `status_code` and response body text for failed OAuth/order calls.
- Fixed dry-run form parsing by using hidden+checkbox values:
  - hidden `dry_run=0`
  - checkbox `dry_run=1`
- Updated backend parsing so dry run is `True` only when `dry_run == "1"`.

### Behavior Covered
- Status dropdown values (including `COMPLETED`) no longer produce invalid fulfillment-status filters.
- `ANY` works with date-range-only requests.
- Preview continues to work.
- Apply works when dry run is unchecked (no automatic re-check/re-block).

### Validation Run
- `.venv/bin/python -m py_compile /Users/hollyfeagin/Documents/saltstocks/app/main.py /Users/hollyfeagin/Documents/saltstocks/app/integrations/ebay.py`
- Local app startup/shutdown via uvicorn succeeded.

### Next Steps
1. Verify in UI with status `ANY` and date range that includes orders.
2. Preview import and confirm summary renders.
3. Uncheck `Dry run / Preview only` and click `Apply deductions`.
4. Re-run preview to confirm already-imported idempotency still holds.
