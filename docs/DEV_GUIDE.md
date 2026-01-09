
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
```

Git workflow (recommended)
Make changes locally
Test locally
Commit a meaningful checkpoint
Push to GitHub
Useful commands:

```
git status
git diff
git add .
git commit -m "Describe change"
git push
```

Branch workflow for bigger features:

```
git checkout -b feature/<name>
git push -u origin feature/<name>
```

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

```
source .venv/bin/activate
python app/migrate.py
```

Future expansion notes (Umivera)
Do not implement Umivera rules in resale flows yet. Build as separate item types / tables:
materials
recipes
batches
COGS calculations
Keep resale and production logic separated to avoid brittle coupling.
