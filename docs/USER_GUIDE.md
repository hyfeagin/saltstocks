

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

### Option A — Native desktop window (recommended)
Run this from the project directory in Terminal:

```
python3 -m app.cli window
```

SaltStocks opens in its own window — no browser tab, no address bar. Closing the window also stops the server.

**First time only:** install pywebview if you haven't yet:
```
pip install pywebview
```

### Option B — Browser
Run the server and open the app in your default browser automatically:

```
python3 -m app.cli serve
```

Or start the server manually and navigate to `http://127.0.0.1:8000` yourself:

```
python3 -m app.cli serve --no-browser
```

See the Admin Runbook if you need other launch options (custom port, disable reload, etc.).

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

## eBay Integration

### One-time setup (per environment)

Before you can sync eBay orders you need to connect the app to your eBay developer account. You only have to do this once per environment (Sandbox / Production), or again any time your refresh token expires (~18 months).

**Prerequisites — do this in the eBay developer portal first:**
1. Sign in at developer.ebay.com → **Hi [name] → Application Access Keys**
2. Open your app and go to **User Tokens**
3. Under **OAuth**, add `https://localhost:8000/ebay/oauth/callback` as an accepted redirect URL
4. Note the **RuName** eBay generates (looks like `YourName-AppName-PRD-xxxxxxxx`)

**In SaltStocks:**
1. Go to **Config → eBay Settings**
2. Set the **Environment** (SANDBOX or PRODUCTION)
3. Enter your **Client ID** and **Client Secret** from the eBay developer portal
4. Paste the **RuName** into the RuName field
5. Click **Save eBay Settings**

### Getting / refreshing your token

1. From **Config → eBay Settings**, click **Connect to eBay (Get New Token)**
2. Click **Open eBay Authorization Page** — eBay opens in a new tab
3. Sign in with your eBay seller account and click **Agree**
4. Your browser will try to redirect to `https://localhost:8000/...` and show a **connection error** — this is expected
5. **Copy the full URL from your browser's address bar** (it will contain `?code=` in it)
6. Paste it into the form and click **Exchange Code & Save Token**
7. You'll land back on the Config page with a green "Connected!" banner

The refresh token is now saved and will be used for all future eBay syncs.

> **Tip:** eBay auth codes expire after ~5 minutes. If you get a token exchange error, go back to step 1 and generate a fresh code.

### Syncing eBay sold orders

Once connected, go to **eBay → Import Orders**:
1. Set the date range (defaults to last 7 days)
2. Leave **Dry run** checked to preview deductions without applying them
3. Review the preview table — items show as `matched`, `unmatched`, or `already imported`
4. Uncheck **Dry run** and click **Apply** to deduct quantities from inventory

Deductions are idempotent — importing the same order twice will not double-deduct.

---

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
