

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

## CSV import/export (new items + updates)
Use **Export CSV** on the Resale Inventory page to get a template that you can edit in Excel/Sheets.

Import rules:
- **Blank id** → creates a new item (SKU auto-generates if blank).
- **Existing id** → updates that item.
- **Unknown columns** are ignored, and **missing columns** are allowed.
- **qty_on_hand** and **unit_cost**: blank defaults to 0; invalid numbers skip the row.
- **list_price**: blank clears it; invalid numbers skip the row.
- **SKU uniqueness** is enforced on create and update.

Workflow:
1) Export CSV.
2) Add new rows with blank id, or edit existing rows.
3) Import CSV and review the import summary for any skipped rows.
