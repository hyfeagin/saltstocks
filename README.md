# SaltStocks

SaltStocks is a local inventory web app for a small business. It currently supports:
- Auto-generated SKUs (read-only)
- Resale inventory management
- Bulk edit (status/channel/location/tags append/unit_cost)
- One-click database backup

## Current scope (as of now)
✅ Auto SKU generation
✅ Bulk edit
✅ Backup
✅ eBay pull import (preview + apply deductions, idempotent per order line)
🚧 Future: Umivera materials + recipes + batches + COGS logic

## Tech stack
- Python + FastAPI (local web server)
- SQLite database stored locally
- HTML templates (Jinja2)

## Where it lives
Project folder (local):
- `~/Documents/Saltstocks`  *(adjust case if your folder name differs)*

Database:
- `data/saltstocks.db`

Backups:
- `data/backups/`

## Run the app (Mac)
From Terminal:

```bash
cd ~/Documents/Saltstocks
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open in browser:
http://127.0.0.1:8000
Stop the server:
In Terminal: Ctrl + C
Backups
Use the Backup Now button in the UI.
This creates a timestamped copy of the database in data/backups/.
Git / GitHub notes
This repo tracks the application code, not the database.
data/saltstocks.db is ignored (not committed)
data/backups/ is ignored
.venv/ is ignored
Recommended workflow:
Edit locally → test locally → commit → push
Status
This is currently a private internal tool. It may later be expanded for Umivera (materials + production).

## eBay import (MVP) notes
- Configure credentials in UI: `/settings/ebay`.
- You must manually obtain an eBay OAuth `refresh_token` via eBay Authorization Code Grant flow; this app only performs `refresh_token -> access_token` refresh.
- Root assumption: Saltstocks `sku` must match eBay listing SKU/Custom Label for automatic matching and deductions.
