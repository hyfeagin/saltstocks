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


---

## `docs/USER_GUIDE.md`

```md
# SaltStocks User Guide (Operator)

## What SaltStocks is for
SaltStocks is a simple local inventory app for resale items (Geekery Vault / liquidation workflow). It helps track:
- What you have
- Where it is
- What it cost
- Listing status/channel
- Bulk updates
- Backups

## How to open the app
1) Start the server (see Admin Runbook if needed)
2) Open http://127.0.0.1:8000 in your browser

## Key concepts

### SKU (auto-generated, read-only)
- SKUs are created automatically when you add an item.
- You do not edit SKUs.
- Format:
  `{COMPANY}-{CODE}-{000001}`
  - COMPANY: GV or UM (currently GV focus)
  - CODE: brand/material code (e.g., FUNKO, LEGO, NECA, SALT)
  - Sequence: increments per (COMPANY + CODE)

### Location
- Location is a human label (e.g., “Tote A3”, “Shelf 2”).
- Location can change freely and does NOT affect SKU.

### Tags
- Tags are free-form text to help search and filter.
- Bulk edit supports APPEND tags (adds to the end).

## Resale Inventory page
This is the main working screen.

### Add a new item
Use the “Add Item” button and fill out:
- Name (required)
- Company (default GV)
- Brand/Code (used for SKU generation)
- Qty on hand
- Unit cost
- Condition
- Location
- Tags/Notes
- Listing fields (status/channel/list price/url)

On save, a SKU will be generated automatically.

### Edit an item
Use the “Edit” link on the row.
- SKU will display but not be editable.

### Adjust quantity
Use the “Adjust” box:
- `+1` adds one
- `-1` subtracts one
This is for operational counting adjustments (selling, moving, correcting counts).

## Bulk Edit
Bulk edit applies changes to selected items.

Bulk edit supports:
- Status (listed/unlisted/sold/etc.)
- Channel (ebay/etsy/etc.)
- Location
- Append tags
- Unit cost (applies same cost to all selected)

Qty is NOT bulk edited.

How to use:
1) Check the boxes next to items
2) Choose changes in Bulk Edit panel
3) Click “Apply bulk changes”

## Backup Now
Use “Backup Now” any time you complete a big session (intake, listing, bulk edit).

Backups are stored in:
- `data/backups/`

Recommended habit:
- Backup at the start of a work session
- Backup at the end of a work session
