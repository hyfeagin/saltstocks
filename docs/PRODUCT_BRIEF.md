

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
