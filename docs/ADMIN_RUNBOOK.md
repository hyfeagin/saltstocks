# SaltStocks Admin Runbook (Keep It Running)

This doc is for anyone who needs to run or restore SaltStocks without being the original developer.

## Quick start (Mac)
Open Terminal:

```bash
cd ~/Documents/Saltstocks
source .venv/bin/activate
uvicorn app.main:app --reload

Open in browser:
http://127.0.0.1:8000
Stop server:
Ctrl + C
If the server won’t start
1) “command not found: uvicorn”
Activate venv first:

cd ~/Documents/Saltstocks
source .venv/bin/activate
pip install -r requirements.txt
Then run uvicorn again.
2) “address already in use” / port 8000 busy
Either stop the other server, or run on a different port:

uvicorn app.main:app --reload --port 8001

Then open:
http://127.0.0.1:8001
3) Python errors after code changes
A bad edit can break startup. Use git to revert to last working state:

cd ~/Documents/Saltstocks
git status
git restore .

Or revert to a previous commit (developer assistance may be needed).
Backups and Restore
Where data lives
Primary database:
data/saltstocks.db
Backups:
data/backups/
Restore from a backup
Stop server (Ctrl + C)
Make a safety copy of the current DB (optional):
copy data/saltstocks.db somewhere safe
Choose a backup file from data/backups/
Replace the main DB with the backup:
rename/copy the backup file to data/saltstocks.db
Start server again
What not to commit to GitHub
These should stay local:
data/saltstocks.db
data/backups/
.venv/
They are ignored by .gitignore.
Emergency “it’s all broken” checklist
Restore last known good database backup
Revert code changes with git restore
Start server
Confirm dashboard loads


---

## `docs/DEV_GUIDE.md`

```md
# SaltStocks Developer Guide

## Project layout
- `app/main.py` — FastAPI routes, forms, SKU logic
- `app/db.py` — DB helpers / connection
- `app/schema.sql` — schema for initial DB creation (and reference)
- `app/migrate.py` — safe migration script for adding new columns/tables
- `app/templates/` — HTML templates
- `data/saltstocks.db` — local SQLite database (ignored by git)
- `data/backups/` — timestamped backups (ignored by git)

## Local dev run
```bash
cd ~/Documents/Saltstocks
source .venv/bin/activate
uvicorn app.main:app --reload


Git workflow (recommended)
Make changes locally
Test locally
Commit a meaningful checkpoint
Push to GitHub
Useful commands:

git status
git diff
git add .
git commit -m "Describe change"
git push

Branch workflow for bigger features:

git checkout -b feature/<name>
git push -u origin feature/<name>

SKU generation rules (current)
SKU is read-only.
SKU is created on item creation if blank/null.
Format:
{COMPANY}-{CODE}-{SEQUENCE}
SEQUENCE increments per (COMPANY + CODE) using sku_counters.
sku_counters
Keyed by:
company (GV/UM)
code (FUNKO/LEGO/NECA/etc.)
Tracks:
next_seq integer
Data safety rules (current)
Database file is not committed to git.
Backups are created via UI and stored locally.
Migrations
If schema changes are needed:
Prefer additive changes (ALTER TABLE ADD COLUMN)
Use app/migrate.py for one-time migrations
Avoid destructive migrations unless you have a clear backup/restore plan
Run migration:

source .venv/bin/activate
python app/migrate.py

Future expansion notes (Umivera)
Do not implement Umivera rules in resale flows yet. Build as separate item types / tables:
materials
recipes
batches
COGS calculations
Keep resale and production logic separated to avoid brittle coupling.


---

## `docs/PRODUCT_BRIEF.md`

```md
# SaltStocks Product Brief

## What it is
SaltStocks is a lightweight local inventory web app for a small founder-run business. It is designed for:
- resale inventory (collectibles/toys/etc.)
- fast intake and listing workflows
- minimal admin burden

## Problem it solves
Off-the-shelf tools often fail for this use case because:
- founder needs custom fields and flexible workflows
- SKU requirements differ across businesses (GV vs UM)
- founder wants local-first control, low cost, and simple backups

## Current features (v1)
- Auto-generated SKUs (read-only, stable)
- Resale inventory tracking
- Bulk edit:
  - status/channel/location
  - append tags
  - unit_cost updates
- One-click database backup

## What makes it different
- Designed for founder workflows (speed + simplicity)
- Local-first: no hosting required
- Flexible tagging + location strategy
- SKU system supports multiple businesses via prefix (GV/UM)

## Intended users
- Solo founder
- Small team assisting with intake/listing/shipping
- Potential future buyer/maintainer

## Roadmap ideas (not implemented yet)
- CSV create-import for new item intake
- Listing template exports (eBay/other)
- Materials + recipes + batches (Umivera)
- COGS per batch and per unit
- Barcode label printing support
- Role-based access if hosted

## Risks / constraints
- Local app requires starting the server to use it
- SQLite file must be backed up regularly
- No automated tests yet; rely on “commit checkpoints + manual test + backups”


