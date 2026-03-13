# Salt Stocks Backlog

## Sprint 1 – Receipt Vault + Purchase Intake

### Sprint Goal
Create an Accounting → New Purchase flow that:

- Uploads receipt
- Captures expense or inventory purchase
- Tracks funding source
- Tracks reimbursable status
- Stores receipt locally
- Saves transaction to SQLite
- Generates Wave-ready export CSV
- Generates reimbursement report

Immediate payoff:
Eliminate spreadsheet bookkeeping chaos.

---

# Architecture Decisions

Business Units:
- Geekery Vault
- Umivera
- Yvessa
- Personal/Other

Database Path:
~/Documents/saltstocks/data/saltstocks.db

Menu Location:
Top Nav → Accounting → New Purchase

Classification Branching:
If "Inventory" selected → prompt for:
- SKU (existing or new)
- Quantity
- Cost per unit

---

# Database Changes

## Add Tables

### receipts
- id (pk)
- original_filename
- stored_path
- sha256
- vendor_guess
- date_guess
- total_guess
- created_at

### purchases
- id (pk)
- purchase_date
- vendor_name
- total_amount
- sales_tax_amount
- classification (inventory | expense)
- category
- business_unit
- paid_from (biz_checking | biz_savings | personal_cc | cash)
- reimbursable (bool)
- notes
- created_at

### purchase_receipts
- purchase_id
- receipt_id

---

# File Storage System

Create folder:

~/Documents/saltstocks/receipts/

Structure:

receipts/YYYY/MM/vendor/

Filename format:

YYYY-MM-DD_vendor_total_flag.jpg

Example:

2026-02-20_amazon_18.97_inventory_r.jpg

---

# UI Tasks

## Accounting Menu

- [ ] Add "Accounting" to top nav
- [ ] Add submenu "New Purchase"
- [ ] Add submenu "Purchase Log"
- [ ] Add submenu "Exports"

---

## New Purchase Page

### Base Form Fields

- [ ] Receipt Upload
- [ ] Purchase Date
- [ ] Vendor Name
- [ ] Total Amount
- [ ] Sales Tax (optional)
- [ ] Classification dropdown (Inventory | Expense)
- [ ] Category dropdown
- [ ] Business Unit dropdown
- [ ] Paid From dropdown
- [ ] Reimbursable checkbox
- [ ] Notes field

---

## Conditional Inventory Section

When Classification = Inventory:

Show:
- [ ] Existing SKU dropdown
- [ ] OR Create New SKU field
- [ ] Quantity
- [ ] Cost per unit

On Save:
- Insert purchase record
- Insert inventory adjustment
- Increase inventory quantity
- Store cost

---

# Backend Tasks (FastAPI)

## Core Endpoints

- [ ] POST /accounting/purchase
- [ ] GET /accounting/purchases
- [ ] POST /accounting/upload_receipt
- [ ] GET /accounting/export/journal
- [ ] GET /accounting/export/reimbursements

---

# Receipt Handling

- [ ] Compute SHA256 hash
- [ ] Check duplicate receipt
- [ ] Save receipt file to structured path
- [ ] Store record in receipts table
- [ ] Link receipt to purchase

---

# Export Logic

## Journal Export (Wave Compatible)

Rules:

If paid_from = personal_cc:
Debit: Expense or Inventory  
Credit: Owner Investment/Drawings

If paid_from = biz_checking:
Debit: Expense  
Credit: Business Checking

If classification = inventory:
Debit: Inventory Asset  
Credit: Owner Investment OR Business Checking

CSV Columns:

- Date
- Description
- Debit Account
- Credit Account
- Amount
- Business Unit
- Category
- Receipt Path

---

## Reimbursement Report

Filter:
- paid_from = personal_cc
- reimbursable = true

Output:

- Date
- Vendor
- Amount
- Category
- Business Unit
- Receipt Path

Add summary:

TOTAL OWED TO HOLLY

---

# Testing Tasks

- [ ] Upload receipt test
- [ ] Duplicate receipt detection
- [ ] Inventory branch test
- [ ] Expense branch test
- [ ] CSV export correctness
- [ ] Reimbursement total correctness
- [ ] Database write verification

---

# Suggested Sprint Timeline

Days 1–2  
Database schema + migrations

Days 3–4  
Receipt upload + storage

Days 5–6  
New Purchase UI

Days 7–8  
Inventory branching

Days 9–10  
Export logic

Days 11–12  
Testing + cleanup

Days 13–14  
Polish + edge cases

---

# Scope Guardrails

Not allowed this sprint:

- Bank feed automation
- AI chat interface
- COGS per sale
- Dashboard metrics
- Tax estimation
- API integrations
- Rewriting inventory engine
