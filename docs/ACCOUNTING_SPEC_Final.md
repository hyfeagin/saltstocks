# Saltstocks Accounting Add-On — Technical Specification

**Document version:** 0.5 (Draft)
**Author:** Saltstocks team
**Status:** Planning
**Target module:** `app/accounting/`

**Changelog:**
- 0.5 — Added comprehensive UI Design System documentation (Section 14) extracted from Claude Design export: salt-marsh palette, typography (Fraunces/Inter/JetBrains Mono), spacing scale, canonical card recipe, component library, interaction states, iconography (Lucide), content voice guidelines, and responsive behavior. Added complete Implementation To-Do List (Section 19) with checkboxes tracking Phase 1 progress.
- 0.4 — Added inbound freight (shipping-in) handling. Inbound shipping is capitalized into inventory cost and allocated across items by dollar value, per IRS / GAAP convention. New questionnaire step, allocation utility, distinction from outbound shipping clarified, worked example added.
- 0.3 — Restructured around a **questionnaire-first MVP**. The plain-language questionnaire is now the primary entry path and is fully self-sufficient (no LLM required). The LLM is an optional enhancement layer that sits on top and translates free-text into the same questionnaire answer set. MVP ships with zero external API dependencies.
- 0.2 — Added first-class handling of personal funds used for business expenses (Section 4 expansion, new templates, reimbursement flow, dashboard widget, "Owed Back" tracking).
- 0.1 — Initial draft.

---

## 1. Vision & Goals

### 1.1 Product vision

> "TurboTax for small business accounting."

The user (a small business owner) should never need to know what a debit or credit is, or which account to post to. They describe what happened in plain language, the system asks a few clarifying questions if needed, and the correct double-entry bookkeeping is handled automatically behind the scenes.

### 1.2 Goals

1. **Replace Wave entirely** for the user's bookkeeping needs.
2. **Questionnaire-driven plain-language entry (MVP, no AI required)** — user answers a short, friendly multiple-choice + fill-in-the-blank questionnaire. The questionnaire alone is enough to capture every piece of information needed to post a correct journal entry. **The MVP ships with zero AI dependency.**
3. **Optional AI enhancement (Phase 5+)** — once the MVP is solid, add a free-text "talk to AI" entry mode that translates a sentence like *"I bought 12 plushies for $12 on my personal card"* into the same questionnaire answer-set the user would have entered manually. The AI is a shortcut, not a separate system.
4. **Receipt vault** — every transaction can have one or more receipt images attached, browseable and searchable.
5. **Tax-ready reports** — Profit & Loss, Balance Sheet, sales tax summary, expense by category, exportable to CSV/PDF for tax filing.
6. **Sales tax handling** — track sales tax collected on sales, sales tax paid on purchases, and produce remittance-ready reports.
7. **Cash basis** accounting only (no accrual complexity in v1).
8. **Tight integration with inventory** — buying inventory automatically links to existing Saltstocks inventory records.

### 1.3 Non-goals (v1)

- Auto-categorization from receipt OCR (user is comfortable describing transactions).
- Accrual basis accounting (deferred to v2 if ever).
- Bank feed integration / automatic bank import.
- Payroll, 1099 generation, multi-currency, multi-entity.
- Invoicing customers (Saltstocks already handles eBay sales; manual invoices are out of scope for v1).
- **AI/LLM integration is explicitly NOT part of the MVP.** It is deferred to Phase 5 as an optional enhancement.

---

## 2. Background: Why Cash Basis Simplifies Everything

Cash basis means a transaction is recorded **when money moves**, not when an obligation is incurred. This is significantly simpler than accrual:

- No Accounts Receivable or Accounts Payable accounts to manage.
- No need to track invoices separately from payments.
- Income = money received. Expense = money paid out.
- Inventory is the one notable exception: even on cash basis, inventory purchases are typically capitalized to an asset account and expensed (as Cost of Goods Sold) when sold. This spec keeps that convention because it materially affects taxable income for a resale business.

### 2.1 Background: Personal Funds vs. Business Funds (CRITICAL CONCEPT)

This is the area the user struggles with most, so we treat it as a first-class concept throughout the system.

**The core principle:** every transaction is from the *business's* point of view, not the owner's. The business is its own entity, even if it's a sole proprietorship sharing your tax return.

**When you use a business account** (a credit card or bank account that belongs to the business):
- The business directly pays for the thing.
- The credit/debit hits a business asset (Bank) or liability (Credit Card) account.

**When you use personal funds** (your personal credit card, your personal cash, etc.):
- The business does NOT have any liability to Chase / Visa / etc. — *you* do, personally.
- From the business's perspective, **you (the owner) gifted the business some money or an asset.**
- This is recorded as an **Owner Contribution**, which increases the amount the business owes you back.

**Concrete example.** You buy $12 of plushies on your personal Chase card.

❌ **Wrong:**
```
Dr. Inventory $12 / Cr. Credit Card $12     ← This treats your personal card as a business liability. It isn't.
```

✅ **Right:**
```
Dr. Inventory $12 / Cr. Owner Contributions $12     ← The business now owes you $12, regardless of when/whether your personal card gets paid.
```

**Why this matters for the user's actual question — "how do I know how much I owe myself back?"**

The **Owner Contributions** account *is* that running tally. Every time personal funds are used for the business, it goes up. Every time the business reimburses the owner (an "Owner Draw"), it goes down (well — Draws is a separate account; the *net* is what's owed).

> **Net amount the business owes you back = Owner Contributions − Owner Draws**

This number is shown prominently on the dashboard (see §12.2) so the user always knows their reimbursement ledger at a glance.

### 2.2 Important: COGS is NOT the same as "what I'm owed back"

A common confusion worth calling out explicitly in the system's UX copy:

| Concept | What it tells you | When it changes |
|---|---|---|
| **Owner Contributions** | How much personal money/assets you've put into the business (cumulative, never decreases on its own) | Goes up every time you use personal funds for a business purchase |
| **Owner Draws** | How much the business has paid you back (cumulative) | Goes up every time the business reimburses you or you take a draw |
| **Cost of Goods Sold (COGS)** | The cost-basis of inventory that has *already been sold*, used to calculate profit/taxes | Goes up only when inventory sells, not when it's purchased |

**Worked illustration.** You spend $1,000 of personal money on inventory. Sell zero of it.
- Owner Contributions: **$1,000** (business owes you $1,000)
- COGS: **$0** (nothing sold yet — no expense recognized)
- Inventory asset: **$1,000**

Six months later, you've sold half for $800 cash to the business bank.
- Owner Contributions: still **$1,000** (the original obligation hasn't changed)
- Owner Draws: still **$0** (you haven't reimbursed yourself yet)
- COGS: **$500** (recognized as items left the inventory)
- Inventory asset: **$500**
- Bank: **$800**

The business now has $800 cash. You could pay yourself back any portion of the $1,000 you're owed. That payment is an **Owner Draw**, which has nothing to do with COGS.

This conceptual separation is enforced in the UI: the dashboard shows "Owed back to you" (Contributions − Draws) as a separate, prominent number from any P&L figure.

### 2.3 Background: Inbound Shipping (Freight-In) is NOT a Shipping Expense

This is another concept that trips up resale businesses. The treatment depends entirely on **direction**:

| Type of shipping | Where it goes in the books | When it hits the P&L |
|---|---|---|
| **Inbound** (wholesaler → you) | Capitalized into Inventory (added to unit cost) | Only when items sell, as part of COGS |
| **Outbound** (you → customer) | Shipping Expense | Immediately, when paid |

**Why inbound shipping is part of inventory cost:** under both GAAP and IRS rules, the cost of getting inventory ready for sale — including freight to your warehouse — is part of what that inventory cost you. Expensing it immediately overstates current-year deductions and understates inventory value, which the IRS notices in an audit.

**Concrete example.** You bought $1,000 of inventory + $100 inbound shipping = $1,100 total. You sell half this year.

❌ **Wrong** (expense the $100 immediately):
```
Dr. Inventory                      $1,000.00
Dr. Shipping Expense               $  100.00
    Cr. Bank/Card                          $1,100.00
```
This year's COGS = $500 + Shipping Expense $100 = **$600 deducted.**

✅ **Right** (capitalize the $100 into inventory):
```
Dr. Inventory                      $1,100.00
    Cr. Bank/Card                          $1,100.00
```
This year's COGS = half of $1,100 = **$550 deducted.** The other $550 stays on the books as inventory until those items sell. You eventually deduct the full $1,100 — just timed correctly.

**Saltstocks always uses the right way.** When the questionnaire asks about a shipping charge on an inventory purchase, it capitalizes the cost into inventory automatically. The user never has to think about which year a deduction belongs in.

### 2.4 Allocating Shipping Across Multiple Items

When a single shipment contains multiple items (or multiple SKUs), the shipping cost is allocated across them so each item's per-unit cost is correct. The allocation rule:

> **Each item's share of shipping = (item's pre-shipping cost ÷ shipment's pre-shipping subtotal) × shipping cost**

This is the **dollar-weighted** method — items that cost more carry a proportionally larger share of the freight.

**Example A — single SKU.** 12 plushies for $12.00 + $3.00 shipping.
- All shipping goes to the plushies (single SKU): unit cost = ($12.00 + $3.00) ÷ 12 = **$1.25 each.**

**Example B — multiple SKUs.** 10 plushies @ $1.00 ($10.00) + 5 keychains @ $2.00 ($10.00) + $4.00 shipping.
- Plushies' share of shipping: ($10.00 / $20.00) × $4.00 = **$2.00** → plushie unit cost = ($10.00 + $2.00) ÷ 10 = **$1.20**
- Keychains' share of shipping: ($10.00 / $20.00) × $4.00 = **$2.00** → keychain unit cost = ($10.00 + $2.00) ÷ 5 = **$2.40**

The total inventory recorded is exactly $24.00 (the full amount paid). No money is lost in allocation.

**Edge case — sales tax on the shipment.** Sales tax paid to a wholesaler on inventory purchases follows the same rule as the items themselves: it's part of inventory cost and gets allocated alongside shipping. The questionnaire treats "shipping" and "tax/other charges" as a combined "freight-and-fees" line for simplicity, then allocates the total proportionally. (For simple cases, the user can lump everything that isn't the line-item subtotal into the shipping field.)

---

## 3. Core Concept: Plain-Language Transaction Templates

The user never writes journal entries. Instead, the system has a **catalog of transaction templates** — each template is a recipe that knows which accounts to debit and credit. The user picks a scenario via a friendly questionnaire (or, eventually, describes it to AI); the system fills in the accounts.

### 3.1 The two entry paths (MVP and beyond)

```
                  "Add Entry" button
                         │
                         ▼
              ┌────────────────────┐
              │  How do you want   │
              │     to enter?      │
              └────────────────────┘
                  │            │
       ┌──────────┘            └──────────┐
       ▼                                  ▼
  ┌────────────┐                   ┌────────────┐
  │ Questionnaire│  (MVP - default) │  Talk to   │ (Phase 5+)
  │   (always   │                   │    AI      │
  │  available) │                   │ (optional) │
  └────────────┘                   └────────────┘
       │                                  │
       │   Both paths produce the         │
       │   SAME structured answer set     │
       └─────────────┬────────────────────┘
                     ▼
           Template engine + Posting engine
                     ▼
              Journal entry posted
```

**Critical design principle:** the questionnaire is the canonical entry path. The AI option, when added, is just an alternate UI that produces the same JSON structure the questionnaire produces. This means:
- The MVP works completely without any AI.
- The AI layer can be added, removed, replaced, or upgraded without touching the posting engine.
- If the AI is uncertain about anything, it falls back to the questionnaire to fill gaps.
- Every transaction the AI handles can also be created (or audited) via the questionnaire — there's no AI-only path.

### 3.2 Example flow — The questionnaire (MVP, no AI)

User clicks **Add Entry** → picks **Answer questions** → sees this flow:

**Question 1 — What kind of transaction?** (multiple choice, the most common options as big buttons)
- 🛍️ I bought inventory to resell
- 💰 I made a sale (manual — most sales come from eBay import)
- ✈️ I paid for a business expense (travel, meals, supplies, etc.)
- 🔄 I paid down a credit card
- 💵 I'm paying myself back for personal funds I used
- ➕ I'm putting personal money into the business
- 💳 The business paid sales tax to the state
- ❓ Something else

**Question 2 (if "I bought inventory") — Whose money paid for it?** (two big buttons)
- 🏦 Business funds (a business bank account or business credit card)
- 👤 Personal funds (my personal card, personal cash, etc.)

**Question 3 — How much did it cost in total?** (single number input, e.g. "12.00")

**Question 4 — When?** (date picker, defaults to today; quick buttons for "Today" / "Yesterday")

**Question 5 — From whom did you buy it?** (free text, optional, e.g. "Amazon" or "Plushie Wholesale Co.")

**Question 6 — Link to inventory:**
- ☑️ Create a new SKU for this (auto-generated)
- ☑️ Link to an existing SKU (search/dropdown)
- (If "create new"): Brief item name + quantity

**Question 7 — Inbound shipping?** *Did the wholesaler charge you anything for shipping or handling?* (number input, defaults to $0, optional)
- *Helper text: "If yes, enter the total. We'll add it to your inventory cost so it's spread correctly across the items."*

**Question 8 — Receipt:** "Got a photo? Drag/drop or skip."

**Question 9 — Confirm:** Plain-English summary card:
> *You bought 12 of "plushies" for $12.00 + $3.00 shipping = $15.00 total today using personal funds. The business will record this as $15.00 of inventory (each plushie costs $1.25 with shipping included) and will owe you $15.00 back.*
>
> [← Back to edit] [✓ Looks right — record it]

That's the entire questionnaire. Seven to nine questions, mostly tappable. No accounting jargon. The user could answer this in 30–60 seconds on their phone.

### 3.3 Example flow — AI mode (Phase 5+ enhancement)

Same user, same transaction, but they pick **Talk to AI** instead:

User types: *"bought 12 plushies for $12 on my personal card today"*

The AI layer:
1. Calls the Anthropic API with the user's text + the list of templates + the list of payment accounts.
2. Returns a structured JSON object that **fills in the same fields the questionnaire would have collected** (template, funding source, amount, date, vendor, etc.).
3. Hands that JSON to the same downstream code path the questionnaire uses.
4. Any field the AI couldn't confidently fill becomes a follow-up question — using the **same questionnaire components** as the manual path.
5. User sees the same Confirm screen as in 3.2.

The AI mode is, fundamentally, a faster way to fill out the questionnaire. Nothing else.

### 3.4 Template catalog (v1)

Each template has: a unique ID, plain-English name, required fields, default account mappings, and the questionnaire branch that leads to it.

| Template ID | Plain-English name | Debit account | Credit account |
|---|---|---|---|
| `BUY_INVENTORY` | Bought inventory for resale (business funds) | Inventory | Cash / Card / Bank (per payment method) |
| `BUY_INVENTORY_PERSONAL` | Bought inventory for resale (personal funds) | Inventory | Owner Contributions |
| `BUY_EXPENSE_PERSONAL` | Paid a business expense with personal funds | (relevant expense account) | Owner Contributions |
| `REIMBURSE_OWNER` | Business pays owner back for personal funds used | Owner Draws | Bank |
| `SELL_INVENTORY_CASH` | Sold inventory (cash/card payment received) | Cash / Bank | Sales Revenue (+ split to Sales Tax Payable if taxable) |
| `COGS_RECOGNITION` | Cost of goods sold (auto when sale recorded) | Cost of Goods Sold | Inventory |
| `BUSINESS_MEAL` | Business meal | Meals & Entertainment Expense | Cash / Card / Bank |
| `TRAVEL_HOTEL` | Hotel for business travel | Travel Expense | Cash / Card / Bank |
| `TRAVEL_TRANSPORT` | Flight / train / Uber for business | Travel Expense | Cash / Card / Bank |
| `OFFICE_SUPPLIES` | Office supplies | Office Supplies Expense | Cash / Card / Bank |
| `SOFTWARE_SUBSCRIPTION` | Software subscription | Software Expense | Cash / Card / Bank |
| `SHIPPING_OUTBOUND` | Shipping label for customer order (you → customer) | Shipping Expense | Cash / Card / Bank |
| `EBAY_FEES` | eBay or marketplace fees | Marketplace Fees Expense | Cash / Card / Bank |
| `PAYMENT_PROCESSING_FEE` | Stripe/PayPal/Square fee | Payment Processing Expense | Cash / Bank |
| `UTILITIES` | Internet, phone, electric (business %) | Utilities Expense | Cash / Card / Bank |
| `RENT` | Business rent / coworking | Rent Expense | Cash / Card / Bank |
| `PROFESSIONAL_SERVICES` | Accountant, lawyer, consultant | Professional Services Expense | Cash / Card / Bank |
| `BANK_FEE` | Bank service fee | Bank Fees Expense | Bank |
| `OWNER_CONTRIBUTION` | Owner put personal money into business | Cash / Bank | Owner's Equity |
| `OWNER_DRAW` | Owner took money out of business | Owner's Equity | Cash / Bank |
| `PAY_CREDIT_CARD` | Paid down credit card from bank | Credit Card (liability) | Bank |
| `SALES_TAX_REMITTED` | Paid sales tax to state | Sales Tax Payable | Bank |
| `OTHER_EXPENSE` | Catch-all expense (with category prompt) | (user-selected expense account) | Cash / Card / Bank |
| `OTHER_INCOME` | Non-sales income (refund, rebate, etc.) | Cash / Card / Bank | Other Income |

Templates are stored in code (Python module) for v1, not in the database. Adding a new template = a code change. This is fine for an internal tool.

**Note on inbound shipping (freight-in):** there is intentionally no `SHIPPING_INBOUND` template. Inbound shipping is captured as an extra field on the `BUY_INVENTORY` and `BUY_INVENTORY_PERSONAL` templates and is allocated into the inventory cost itself (see §2.3 and §2.4). The user is never asked to "categorize" inbound shipping — it's just one more number on the inventory purchase form.

### 3.5 Why this approach works

- **The user's mental model is the transaction, not the journal entry.** Templates encode the accountant's knowledge so the user doesn't need it.
- **Audit trail is preserved.** Every posted entry has a real double-entry journal record — a CPA can review the books and they'll be valid.
- **Extensible.** New scenarios are just new templates.
- **AI is a feature, not a dependency.** The MVP is fully functional without it. The AI layer can be added/removed/swapped without disrupting the user.

---

## 4. Architecture

### 4.1 Module structure

New code lives in `app/accounting/` parallel to existing inventory code:

```
app/
├── main.py                  (existing)
├── inventory/               (existing inventory logic)
└── accounting/              (NEW)
    ├── __init__.py
    ├── models.py            SQLAlchemy ORM models
    ├── schemas.py           Pydantic schemas (incl. canonical answer-set)
    ├── templates.py         Transaction template catalog
    ├── questionnaire.py     Questionnaire flow definitions (MVP entry path)
    ├── posting.py           Double-entry posting engine
    ├── reports.py           P&L, balance sheet, sales tax report
    ├── receipts.py          Receipt file storage + retrieval
    ├── routes.py            FastAPI endpoints
    ├── nlp.py               (PHASE 5+) Optional LLM enhancement layer
    └── templates_html/      Jinja2 templates for the UI
        ├── questionnaire/   Per-step questionnaire partials
        └── ...
```

`nlp.py` does not exist in the MVP. It's added later as a thin module that translates free-text → canonical answer-set, then hands off to the same posting flow the questionnaire uses.

### 4.2 High-level data flow

```
                    User clicks "Add Entry"
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       QUESTIONNAIRE PATH              AI PATH (Phase 5+)
        (MVP, default)                  (optional)
              │                               │
              ▼                               ▼
    /accounting/entry/questionnaire   /accounting/entry/parse
    ─ User answers 6–8 questions      ─ User types free-text
    ─ Branching logic: each answer    ─ LLM produces a draft
      reveals the next question         answer-set in same
    ─ All answers collected into        canonical schema
      the canonical answer-set        ─ Any low-confidence
                                        fields fall through to
                                        questionnaire to fill
              │                               │
              └───────────────┬───────────────┘
                              ▼
                    Canonical answer-set
                    (single JSON schema)
                    ─ template_id
                    ─ funding_source
                    ─ amount, date, vendor
                    ─ payment_account_id
                    ─ inventory linkage
                    ─ sales tax info
                    ─ memo, notes
                              │
                              ▼
                    Template engine validates
                    ─ all required fields present?
                    ─ accounts exist & active?
                    ─ amounts are positive Decimals?
                              │
                              ▼
                    /accounting/entry/preview
                    ─ produces proposed JournalEntry
                      with debit/credit lines
                    ─ generates plain-English summary
                              │
                              ▼
                    User sees Confirm screen,
                    optionally attaches receipt photo
                              │
                              ▼
                    /accounting/entry/confirm
                    ─ writes JournalEntry + lines
                      atomically, stores receipt
                              │
                              ▼
                    Books updated. Reports reflect
                    new entry immediately.
```

The two paths converge at the canonical answer-set. This is the central architectural decision: there is exactly **one** schema that downstream code consumes, and exactly **one** posting engine. The entry mode is a UI choice, not a different code path.

### 4.3 Why FastAPI + SQLite is still the right stack

The existing Saltstocks stack (FastAPI + SQLite + Jinja2) is a great fit for this:

- SQLite handles double-entry bookkeeping perfectly at this scale (single user, thousands of entries per year max).
- FastAPI's Pydantic validation is ideal for ensuring entries balance (debits == credits) before they hit the DB.
- Jinja2 templates keep the UI consistent with the rest of Saltstocks.

### 4.4 Personal vs. Business Funds — Entry Flow Handling

Because this is the user's biggest pain point, the system **always knows** whether a payment came from personal or business funds before posting an entry. There are three paths:

**Path A — User explicitly says "personal" or "business":**
- "I bought 12 plushies for $12 on **my personal card**" → NLP layer flags `funding_source = personal`, picks the `BUY_INVENTORY_PERSONAL` template variant.
- "I bought 12 plushies for $12 on **the business Chase**" → NLP picks `BUY_INVENTORY` and resolves the payment account.

**Path B — User names a specific account that's tagged in the system:**
- Each account in the Chart of Accounts has an `is_personal_funds` flag (only true for the synthetic "Owner Contributions" route — see §5.1) or a clear name like "Personal Card — Chase". When the user says "my Chase card," the system disambiguates: if there are both a personal and business "Chase" account, it asks.

**Path C — User is ambiguous ("paid with my card"):**
- The questionnaire fires a single tappable question: *"Did you pay with business funds or personal funds?"* with two buttons. This is the **default behavior whenever funding source isn't unambiguous.**

After this is resolved, the template selection follows automatically:

| User intent | Funding source | Template chosen |
|---|---|---|
| Bought inventory | Business | `BUY_INVENTORY` |
| Bought inventory | Personal | `BUY_INVENTORY_PERSONAL` |
| Paid for hotel/meal/supplies/etc. | Business | matching expense template |
| Paid for hotel/meal/supplies/etc. | Personal | `BUY_EXPENSE_PERSONAL` (with expense category sub-prompt) |

**Why this is a separate concern from the regular template selection:** because the same real-world activity ("bought hotel for business trip") posts to *different* credit accounts depending on whose money was used. Splitting funding-source detection out of template detection makes the NLP layer simpler and the UX more reliable.

**Reimbursement flow.** When the business has cash and the user wants to pay themselves back, they hit the **"Reimburse myself"** button on the dashboard (or use the `REIMBURSE_OWNER` template directly). The system:
1. Shows the current "Owed back to you" balance (Owner Contributions − Owner Draws).
2. Lets the user enter any amount up to that balance (or "pay full balance").
3. Lets the user pick the bank account the reimbursement comes from.
4. Posts: Dr. Owner Draws / Cr. Bank.
5. Dashboard updates the "Owed back" number immediately.

This means the user never has to do mental math to figure out what they're owed — it's always one number on screen.

---

## 5. The Canonical Answer-Set & Questionnaire Engine

This section defines the contract that both the questionnaire and (later) the AI layer must satisfy. It's the single most important piece of the accounting module's design.

### 5.1 The canonical answer-set schema

Every entry — whether collected via questionnaire or AI — produces a single Pydantic object before posting:

```python
class TransactionAnswerSet(BaseModel):
    # Identification
    template_id: str                       # One of the catalog IDs (BUY_INVENTORY, etc.)
    funding_source: Literal["business", "personal"] | None  # Required for some templates

    # Money & timing
    total_amount: Decimal                  # Headline amount
    entry_date: date                       # Defaults to today

    # Counterparty
    vendor: str | None                     # Free-text, optional
    payment_account_id: int | None         # FK to accounts (the bank/card used). None for personal-funds templates.
    expense_category_account_id: int | None  # Used for OTHER_EXPENSE / BUY_EXPENSE_PERSONAL

    # Inventory linkage (only for inventory templates)
    inventory_link: InventoryLink | None
    # InventoryLink = { mode: "create_new" | "link_existing", sku?: str, name?: str, quantity?: int }
    # Multi-item shipments use a list of InventoryLink objects; see §6.2

    # Inbound freight (only for BUY_INVENTORY / BUY_INVENTORY_PERSONAL)
    freight_in_amount: Decimal | None       # Wholesaler-charged shipping; capitalized into inventory cost
    # The total recorded inventory = sum of line-item costs + freight_in_amount.
    # Freight is allocated across line items by dollar weight (see §2.4).

    # Sales tax (only for SELL_INVENTORY_CASH and similar)
    sales_tax_amount: Decimal | None
    sales_tax_jurisdiction_id: int | None

    # Free-form
    memo: str | None
    receipt_files: list[UploadedFile]      # Empty list = no receipt
```

**Both entry paths must produce a valid `TransactionAnswerSet`.** The posting engine consumes only this object — it doesn't know or care whether the user clicked through buttons or typed a sentence.

### 5.2 The questionnaire engine

The questionnaire is defined as a **state machine** in `questionnaire.py`. Each template has its own questionnaire flow — a list of steps, where each step:

- Has an ID, a question text, an input type (`multi_choice`, `text`, `number`, `date`, `account_picker`, `inventory_picker`, `file_upload`).
- Lists its options (for multi-choice) or validation rules (for text/number).
- Specifies a `next_step` function that picks the next step based on the answer so far.
- Maps its answer into a field on the `TransactionAnswerSet`.

A questionnaire flow is, in pseudo-code:

```python
QUESTIONNAIRE_BUY_INVENTORY = [
    Step("kind",
         "What kind of transaction is this?",
         type="multi_choice",
         options=[
             ("BUY_INVENTORY", "🛍️ I bought inventory to resell"),
             ("BUY_EXPENSE_*", "✈️ I paid for a business expense"),
             # ...
         ],
         maps_to="template_id_or_branch"),

    Step("funding_source",
         "Whose money paid for it?",
         type="multi_choice",
         options=[
             ("business", "🏦 Business funds"),
             ("personal", "👤 Personal funds"),
         ],
         maps_to="funding_source",
         shown_when=lambda answers: answers.template_id in TEMPLATES_THAT_NEED_FUNDING_SOURCE),

    Step("amount",
         "How much did it cost in total?",
         type="number",
         maps_to="total_amount"),

    Step("date", "When?", type="date", default="today", maps_to="entry_date"),

    Step("vendor", "From whom did you buy it? (Optional)", type="text", maps_to="vendor"),

    Step("payment_account",
         "Which account did you pay from?",
         type="account_picker",
         filter=lambda accounts: [a for a in accounts if a.subtype in ("bank", "credit_card")],
         maps_to="payment_account_id",
         shown_when=lambda answers: answers.funding_source == "business"),

    Step("inventory_link",
         "Link to inventory:",
         type="inventory_picker",
         maps_to="inventory_link"),

    Step("freight_in",
         "Did the wholesaler charge you anything for shipping or handling on this order?",
         type="number",
         optional=True,
         default=Decimal("0.00"),
         help_text="If yes, enter the total. We'll add it to your inventory cost so it's allocated correctly across the items.",
         maps_to="freight_in_amount",
         shown_when=lambda answers: answers.template_id in ("BUY_INVENTORY", "BUY_INVENTORY_PERSONAL")),

    Step("receipt", "Got a receipt? Drag/drop or skip.",
         type="file_upload",
         optional=True,
         maps_to="receipt_files"),
]
```

This is a sketch — the actual implementation will be more polished — but it shows the shape. Each template has a flow; flows can share steps (e.g. "amount", "date", "receipt" are the same across all templates).

### 5.3 Why a state machine instead of a wizard library

We're rolling our own rather than using something like a wizard library because:
- Steps need conditional visibility (`shown_when`) based on prior answers.
- Steps need to map to nested fields in the answer-set.
- We want to keep the questionnaire flow co-located with the template definitions for clarity.
- The total complexity is small: ~25 templates × ~6 steps each, with heavy step reuse.

### 5.4 Questionnaire UI implementation

- One question per screen (or section) on mobile; multiple visible on desktop.
- Big tappable buttons for multi-choice — finger-friendly.
- Always-visible "Back" button to revise an earlier answer (which may invalidate later answers — system warns and lets user re-answer).
- Progress indicator ("Step 3 of 6") gives the user a sense of how close they are to done.
- All questions phrased in plain language. **No accounting terms anywhere in the UI** — no "debit," "credit," "account," "ledger," "journal," etc. The word "account" is OK only when referring to a bank/card account in the everyday sense.

### 5.5 How the AI layer (Phase 5+) integrates

When the AI layer is added later, it follows this contract:

1. AI receives the user's free text + the list of templates + the list of available accounts.
2. AI returns a partial `TransactionAnswerSet` JSON object, with a `confidence` score per field.
3. The system validates the AI's output against the same rules the questionnaire enforces.
4. Any field that's missing or has confidence below threshold triggers the **same questionnaire step** that the manual path would have used — there's no separate AI follow-up UI.
5. Once all fields are filled, the user sees the same Confirm screen as the questionnaire path.

This means the AI integration doesn't introduce any new UI components, validation rules, or posting logic. It's purely a new entrypoint into an existing flow.

---

## 6. Data Model

### 6.1 New tables

#### `accounts`
Chart of Accounts. Pre-seeded on first run.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `code` | TEXT UNIQUE | e.g. `1010`, `4000` (numeric account codes) |
| `name` | TEXT | e.g. "Chase Checking", "Sales Revenue" |
| `type` | TEXT | One of: `asset`, `liability`, `equity`, `income`, `expense` |
| `subtype` | TEXT NULL | e.g. `bank`, `credit_card`, `inventory`, `cogs` |
| `is_active` | BOOLEAN | Soft-delete flag |
| `is_personal_funds_proxy` | BOOLEAN | True only for accounts that represent personal-funds-used (Owner Contributions). Used by the questionnaire and AI layers to disambiguate when funding source is "personal". |
| `is_system_protected` | BOOLEAN | True for accounts the user can't delete (Inventory, COGS, Sales Tax Payable, Owner Contributions, Owner Draws) |
| `created_at` | TIMESTAMP | |

Default seeded chart of accounts (subset shown):

```
1010  Cash on Hand                  asset / cash
1020  Bank — Primary Checking       asset / bank
1030  Bank — Business Savings       asset / bank
1200  Inventory                     asset / inventory
1500  Receipt Vault Holding         asset / clearing  (rarely used)
2010  Credit Card — Primary         liability / credit_card
2100  Sales Tax Payable             liability / tax
3000  Owner's Equity                equity
3100  Owner Contributions           equity
3200  Owner Draws                   equity (contra)
4000  Sales Revenue                 income
4100  Other Income                  income
5000  Cost of Goods Sold            expense / cogs
6010  Meals & Entertainment         expense
6020  Travel                        expense
6030  Office Supplies               expense
6040  Software & Subscriptions      expense
6050  Shipping                      expense
6060  Marketplace Fees              expense
6070  Payment Processing Fees       expense
6080  Utilities                     expense
6090  Rent                          expense
6100  Professional Services         expense
6110  Bank Fees                     expense
6900  Other Expenses                expense
```

The user can rename, add, or deactivate accounts in `/accounting/accounts`. Some accounts (Inventory, COGS, Sales Tax Payable) are system-protected and cannot be deleted.

#### `journal_entries`
One row per logical transaction.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entry_date` | DATE | Date the transaction occurred |
| `description` | TEXT | User's plain-language description |
| `template_id` | TEXT | Which template was used (e.g. `BUY_INVENTORY`) |
| `vendor` | TEXT NULL | e.g. "Marriott", "Amazon" |
| `total_amount` | DECIMAL(12,2) | The headline amount (for display) |
| `created_at` | TIMESTAMP | |
| `created_by_method` | TEXT | `nlp`, `manual`, `import_ebay`, `system_auto` |
| `notes` | TEXT NULL | Free-form |
| `is_void` | BOOLEAN | Soft-delete (entries can be voided but never hard-deleted, for audit integrity) |
| `void_reason` | TEXT NULL | |
| `void_at` | TIMESTAMP NULL | |

#### `journal_lines`
The double-entry detail. Two or more rows per entry. Sum of debits MUST equal sum of credits.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entry_id` | INTEGER FK → journal_entries.id | |
| `account_id` | INTEGER FK → accounts.id | |
| `debit` | DECIMAL(12,2) DEFAULT 0 | |
| `credit` | DECIMAL(12,2) DEFAULT 0 | |
| `memo` | TEXT NULL | |
| `inventory_item_id` | INTEGER FK → inventory NULL | Links to existing Saltstocks inventory if applicable |

CHECK constraint: `(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)` — a line is one or the other.

#### `receipts`
Receipt vault. One or more per journal entry.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entry_id` | INTEGER FK → journal_entries.id | |
| `original_filename` | TEXT | |
| `stored_path` | TEXT | Relative path under `data/receipts/YYYY/MM/` |
| `mime_type` | TEXT | |
| `file_size_bytes` | INTEGER | |
| `sha256` | TEXT | For deduplication and integrity |
| `uploaded_at` | TIMESTAMP | |

Files are stored on disk under `data/receipts/YYYY/MM/{uuid}.{ext}` — not in the database. The `data/receipts/` directory is gitignored just like `data/saltstocks.db`.

#### `sales_tax_rates`
For sales tax handling.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `jurisdiction` | TEXT | e.g. "Washington State" |
| `rate` | DECIMAL(6,4) | e.g. 0.0950 for 9.5% |
| `is_default` | BOOLEAN | Used when no explicit rate given |
| `effective_from` | DATE | |
| `effective_to` | DATE NULL | |

### 6.2 Integration with existing inventory tables

When a `BUY_INVENTORY` transaction is recorded, the user can either:
- Link it to an existing SKU (so the unit cost gets updated and quantity-on-hand increases), or
- Create a new inventory record on the fly (using existing auto-SKU logic).

The link is stored in `journal_lines.inventory_item_id`. This lets the existing inventory table remain the source of truth for stock levels, while accounting maintains the financial picture.

**Freight-in allocation flows into inventory unit costs.** If the purchase included inbound shipping (`freight_in_amount > 0`), the posting engine allocates it across line items by dollar weight (see §8.5) **before** updating the inventory records. The inventory module sees one number per SKU: the freight-inclusive unit cost. From its perspective, freight allocation is invisible — it's just told that 12 plushies cost $1.25 each instead of $1.00 each.

This is intentional: the inventory module already supports weighted-average cost updates, and feeding it freight-inclusive costs means the existing logic handles everything correctly without any new branching.

When inventory is sold (via the existing eBay import flow or a manual sale entry), two journal entries are created automatically:
1. **Sale**: Dr. Bank/Cash, Cr. Sales Revenue (and Cr. Sales Tax Payable if applicable)
2. **COGS**: Dr. Cost of Goods Sold, Cr. Inventory — calculated using weighted average cost from the inventory tables. Because freight is already baked into unit cost, COGS automatically reflects the allocated freight without any special handling.

---

## 7. Plain-Language AI Layer (PHASE 5+ — NOT IN MVP)

> ⚠️ **This section describes a post-MVP enhancement.** The MVP ships without any AI. This is the design for when AI is added later.

### 7.1 Approach

Use the Anthropic Claude API to convert a user's free-text description into a partial `TransactionAnswerSet` (see §5.1). The LLM is given:

- The user's description.
- The list of available templates with descriptions and required fields.
- The list of payment-method accounts (so it can match "my Chase card" to the correct account).
- The current date (for "today", "yesterday").
- **The same canonical schema the questionnaire produces.**

The LLM is instructed to return strict JSON conforming to the `TransactionAnswerSet` schema, with a `confidence` score per field:

```json
{
  "template_id": "BUY_INVENTORY_PERSONAL",
  "confidence_overall": 0.95,
  "field_confidences": {
    "template_id": 0.97,
    "funding_source": 0.99,
    "total_amount": 0.99,
    "entry_date": 0.95,
    "vendor": 0.40,
    "inventory_link": 0.60
  },
  "answer_set": {
    "template_id": "BUY_INVENTORY_PERSONAL",
    "funding_source": "personal",
    "total_amount": "12.00",
    "entry_date": "2026-05-03",
    "vendor": null,
    "payment_account_id": null,
    "inventory_link": { "mode": "create_new", "name": "plushies", "quantity": 12 }
  }
}
```

### 7.2 Hand-off to the questionnaire for low-confidence fields

This is the key integration point: **the AI does not have its own follow-up UI.** Any field with confidence below threshold (e.g. 0.7), or any required field the AI failed to fill, is collected by jumping into the existing questionnaire at exactly the right step.

For the example above:
- `vendor` has confidence 0.40 → questionnaire step "From whom did you buy it?" appears.
- `inventory_link` has confidence 0.60 → questionnaire step "Link to inventory" appears.
- All other fields are pre-filled from the AI's answer.

The user sees a partially-filled questionnaire instead of a confirm screen. This is identical to the experience of someone who paused mid-questionnaire and came back.

### 7.3 Validation guardrails

The LLM's output is **never trusted directly.** Before any AI-produced answer-set proceeds to posting:

1. Validate `template_id` is in the known catalog.
2. Validate `payment_account_id` (if set) refers to a real, active account. Fuzzy match `payment_account_hint` against account names if needed.
3. Validate dates and amounts (positive Decimals; reasonable date range).
4. If `confidence_overall < 0.7`, force the user through the full questionnaire even if all fields appear filled.
5. Always show the user the proposed entry in plain English on the Confirm screen before posting (same as questionnaire path).

### 7.4 Failure modes

- **LLM unreachable / API error** → fall back transparently to the questionnaire. The user sees a small notice: *"AI is unavailable, let's go through this step by step."*
- **LLM hallucinates an account** → guardrail rejects and user picks from a real list (same UI as questionnaire's account picker step).
- **LLM misclassifies template** → user can override on the Confirm screen, which restarts the questionnaire from the new template's first step (preserving any answers that still apply).
- **User opts out** → there's a setting to disable the AI entry mode entirely, hiding the "Talk to AI" button from the entry chooser.

### 7.5 Cost & privacy considerations

- Each AI entry call costs ~$0.001–$0.01 in API fees. For a small business posting 50 entries/month, this is negligible (cents/month).
- Receipt images are NOT sent to the AI in this layer (we said no auto-categorization). Only the user's typed description is sent.
- The user can disable AI mode at any time without affecting the rest of the app.

---

## 8. The Posting Engine

### 8.1 Atomicity

Every posted entry must be atomic: either the journal entry, all its lines, and any receipt records are written together, or nothing is. Wrap in a SQLAlchemy transaction.

### 8.2 Balance enforcement

Before a journal entry is committed:

```python
total_debits = sum(line.debit for line in lines)
total_credits = sum(line.credit for line in lines)
if total_debits != total_credits:
    raise UnbalancedEntryError(...)
if total_debits == 0:
    raise EmptyEntryError(...)
```

Use `Decimal` throughout — never float — to avoid penny-rounding bugs.

### 8.3 Voiding entries

Entries are **never deleted**. To "delete" an entry, the system creates a reversing entry (swapping debits and credits) and marks both as voided, with a void_reason. This preserves an audit trail.

### 8.4 Period locking (v1.5)

Eventually, after the user files taxes for a year, that year's entries should be locked. v1 doesn't enforce this, but the schema supports adding a `closed_periods` table later.

### 8.5 Freight-in allocation

Inbound shipping (§2.3, §2.4) is folded into inventory cost rather than recorded as a separate journal line. The posting engine includes a small utility for this:

```python
def allocate_freight_in(
    line_items: list[LineItem],   # each has subtotal_cost and quantity
    freight_amount: Decimal
) -> list[LineItem]:
    """
    Distribute freight_amount across line items by dollar weight.
    Returns line items with adjusted total_cost (subtotal + freight share).
    Per-unit cost is then total_cost / quantity.
    """
    if freight_amount == Decimal("0"):
        return line_items

    subtotal = sum(li.subtotal_cost for li in line_items)
    if subtotal == Decimal("0"):
        # Edge case: free items with paid shipping. Distribute by quantity instead.
        total_qty = sum(li.quantity for li in line_items)
        for li in line_items:
            li.total_cost = freight_amount * (li.quantity / total_qty)
        return line_items

    # Normal case: dollar-weighted allocation
    allocated_so_far = Decimal("0")
    for i, li in enumerate(line_items):
        if i == len(line_items) - 1:
            # Last item gets the remainder, to ensure the sum is exact
            li.total_cost = li.subtotal_cost + (freight_amount - allocated_so_far)
        else:
            share = (li.subtotal_cost / subtotal * freight_amount).quantize(Decimal("0.01"))
            li.total_cost = li.subtotal_cost + share
            allocated_so_far += share

    return line_items
```

**Penny-rounding rule:** the last item absorbs any rounding remainder so the total exactly equals `subtotal + freight_amount`. No money goes missing.

The resulting journal entry is the same shape as a normal inventory purchase — just a single `Inventory` debit equal to the post-allocation total:

```
Dr. 1200 Inventory                 $15.00     (= $12 items + $3 freight)
    Cr. 3100 Owner Contributions           $15.00
```

The allocation appears in the **inventory** records (each SKU's unit cost reflects its share of freight), not in additional journal lines. This keeps the books clean and the COGS-on-sale logic unchanged.

---

## 9. Receipt Vault

### 9.1 Storage

- Files live under `data/receipts/YYYY/MM/{uuid}.{ext}`.
- Database stores metadata only.
- SHA-256 hash on upload — if a duplicate is detected, the system warns the user but allows the upload anyway (could be intentional re-attachment).

### 9.2 UI

A dedicated `/accounting/receipts` page lets the user browse all receipts:

- Thumbnail grid view (lazy-loaded).
- Filters: date range, vendor, template, expense category, has-receipt vs missing-receipt.
- Click a thumbnail → full-size view + linked journal entry.
- Search by filename or memo text.

### 9.3 Backup integration

The existing one-click backup feature should be extended to **also archive** the `data/receipts/` directory alongside the SQLite file. Receipts are at least as important as the database for tax purposes.

---

## 10. Sales Tax Handling

### 10.1 On the sales side

When recording a `SELL_INVENTORY_CASH` transaction, the user is asked:

- Was this taxable? (yes/no)
- What was the sales tax amount? (or: which jurisdiction, and the system computes it)

The journal entry splits:

```
Dr. Bank                    $108.50
    Cr. Sales Revenue                   $100.00
    Cr. Sales Tax Payable               $  8.50
```

### 10.2 On the purchase side

For most purchases on cash basis, sales tax paid is just folded into the expense — the user paid $108.50 for office supplies, the whole $108.50 is the expense. The system doesn't try to break this out.

(Exception: inventory purchases, where some states allow resale-exempt purchases. v1 just records the full amount paid.)

### 10.3 Sales tax report

`/accounting/reports/sales-tax` produces, for any date range:

- Total taxable sales
- Total sales tax collected
- Total non-taxable sales
- Sales tax remitted to date in the period
- Net liability remaining

When the user pays the tax authority, they record a `SALES_TAX_REMITTED` entry, which clears the Sales Tax Payable balance.

---

## 11. Reports

All reports are HTML pages with a "Download CSV" and "Download PDF" button.

### 11.1 Profit & Loss (Income Statement)

For a date range:

```
Revenue
  Sales Revenue                     $X,XXX.XX
  Other Income                      $   XX.XX
  Total Revenue                     $X,XXX.XX

Cost of Goods Sold                 ($  XXX.XX)
Gross Profit                        $X,XXX.XX

Operating Expenses
  Meals & Entertainment             $   XX.XX
  Travel                            $  XXX.XX
  ...
  Total Expenses                    $X,XXX.XX

Net Income                          $X,XXX.XX
```

### 11.2 Balance Sheet

As of a specific date — assets, liabilities, equity. Useful but secondary on cash basis.

### 11.3 Expense by Category

A bar chart + table grouped by expense account, for any date range. This is the report most useful at tax time.

### 11.4 Sales Tax Summary

See section 10.3.

### 11.5 General Ledger / Account Activity

For each account: chronological list of every entry that touched it, with running balance. Lets the user (or their CPA) drill into anything.

### 11.6 Year-end tax export

A single button at year-end produces a ZIP containing:

- P&L for the year (PDF + CSV)
- Expense by Category (PDF + CSV)
- Sales tax summary (PDF + CSV)
- General Ledger (CSV)
- A copy of every receipt, organized by month
- The SQLite database snapshot

This is the package the user hands to their accountant.

---

## 12. API Endpoints

All endpoints are namespaced under `/accounting/`.

### 12.1 Entry creation

| Method | Path | Purpose |
|---|---|---|
| GET | `/accounting/entry/start` | Returns the first questionnaire step (the "What kind of transaction?" picker). |
| POST | `/accounting/entry/answer` | Body: `{session_id, step_id, answer}`. Returns next step OR final answer-set. |
| POST | `/accounting/entry/preview` | Body: completed answer-set. Returns the proposed journal entry (debits/credits + plain-English summary). |
| POST | `/accounting/entry/confirm` | Body: confirmed answer-set + receipt files. Creates the entry atomically. |
| POST | `/accounting/entry/parse` | **(Phase 5+)** Body: `{description}`. Returns partial answer-set + list of remaining questionnaire steps. |

The questionnaire is server-driven: client sends each answer, server returns the next step (or "done" + the final answer-set). This keeps the branching logic in one place and lets the UI stay dumb.

### 12.2 Entry management

| Method | Path | Purpose |
|---|---|---|
| GET | `/accounting/entries` | List with filters (date, template, vendor, account). |
| GET | `/accounting/entries/{id}` | Detail view. |
| POST | `/accounting/entries/{id}/void` | Void with reason. |
| POST | `/accounting/entries/{id}/receipts` | Attach additional receipts. |

### 12.3 Accounts

| Method | Path | Purpose |
|---|---|---|
| GET | `/accounting/accounts` | Chart of accounts. |
| POST | `/accounting/accounts` | Add custom account. |
| PATCH | `/accounting/accounts/{id}` | Rename / deactivate. |

### 12.4 Receipts

| Method | Path | Purpose |
|---|---|---|
| GET | `/accounting/receipts` | Vault browse + filter. |
| GET | `/accounting/receipts/{id}/file` | Stream the file. |

### 12.5 Reports

| Method | Path | Purpose |
|---|---|---|
| GET | `/accounting/reports/pnl?from=&to=` | P&L. |
| GET | `/accounting/reports/balance-sheet?asof=` | Balance sheet. |
| GET | `/accounting/reports/expenses-by-category?from=&to=` | Expense breakdown. |
| GET | `/accounting/reports/sales-tax?from=&to=` | Sales tax summary. |
| GET | `/accounting/reports/ledger/{account_id}?from=&to=` | Account activity. |
| GET | `/accounting/reports/year-end-export/{year}` | ZIP bundle. |

---

## 13. UI / UX Sketch

### 13.1 The "Add Entry" page (the heart of the product)

The Add Entry flow has three screens.

**Screen 1 — The Chooser** (only shown if AI is enabled in Phase 5+; otherwise skipped and the questionnaire opens directly):

> **How would you like to record this?**
>
> ┌─────────────────────────────┐
> │  📋 Answer a few questions  │  ← always visible, the default
> │  Recommended — takes ~30s   │
> └─────────────────────────────┘
>
> ┌─────────────────────────────┐
> │  💬 Talk to AI              │  ← Phase 5+ only; hidden in MVP
> │  Describe it in your words  │
> └─────────────────────────────┘

In the MVP, this screen does not exist — clicking "Add Entry" goes straight to Screen 2.

**Screen 2 — The Questionnaire** (one step at a time):

Each step is a single question with big tappable answers (when applicable). Examples of what the user actually sees:

> **Step 1 of ~6**
> *What kind of transaction is this?*
>
> [🛍️ I bought inventory to resell]
> [💰 I made a sale]
> [✈️ I paid for a business expense]
> [🔄 I paid down a credit card]
> [💵 I'm paying myself back for personal funds]
> [➕ I'm putting personal money into the business]
> [💳 The business paid sales tax to the state]
> [❓ Something else]

> **Step 2 of ~6**
> *Whose money paid for it?*
>
> [🏦 Business funds (business bank or business card)]
> [👤 Personal funds (my personal card or cash)]
>
> [← Back]

> **Step 3 of ~6**
> *How much did it cost in total?*
>
> [    $______    ]
>
> [← Back]   [Continue →]

…and so on through date, vendor, inventory link, and receipt upload. The user can always go back to revise an earlier answer; the system warns if a change invalidates later answers.

**Screen 3 — The Confirm Card** (plain English, no accounting jargon):

> ✓ Ready to record
>
> *You bought 12 of "plushies" for $12.00 today using personal funds. The business will record this as $12.00 of inventory and will owe you $12.00 back.*
>
> 📎 Receipt attached: IMG_3201.jpg
>
> [← Edit any answer]   [✓ Record it]

If the user came in via AI mode (Phase 5+), the Confirm screen looks identical — the user shouldn't be able to tell which entry path produced it.

**Camera icon / receipt upload** is integrated into the questionnaire as its own step (typically the last step before confirmation), not jammed into a corner of the page. This makes it impossible to forget — every entry passes through that step.

### 13.2 The dashboard

Top of the dashboard, in this order of prominence:

1. **"Owed back to you"** (BIG number, top-left) — `Owner Contributions − Owner Draws`. Click it to see the full history of contributions and draws, plus a "Reimburse myself" button.
2. **This month's Net Income** (big number).
3. **Bank balance(s) and Credit Card balance(s).**
4. **Sales Tax Payable balance** (so the user knows what they owe the state).
5. **Recent entries** (last 10).
6. **"Add Entry"** button always visible.

The "Owed back to you" placement is deliberate: this is the number the user has historically had to chase through Wave manually, and putting it front-and-center is one of the main wins of the new system.

#### "Owed back to you" detail page

Clicking the widget opens a page with:

- **Big total at top:** "$X.XX is currently owed back to you."
- **Two columns side-by-side:**
  - *Money you've put in* (Owner Contributions running list, with date, amount, what it was for, and link to entry/receipt).
  - *Reimbursements paid out* (Owner Draws running list).
- **"Reimburse myself" button** that posts a `REIMBURSE_OWNER` entry. User picks the source bank account and the amount (defaulting to the full balance owed).
- **CSV export** for the running ledger.

### 13.3 Entry list / search

A standard table view with filters, but every row links back to its receipt(s) and shows the plain-English description rather than the journal entry detail by default.

---

## 14. Migration & Onboarding from Wave

### 14.1 Initial setup wizard (one-time)

On first run of the accounting module, the user is walked through:

1. **Confirm chart of accounts.** Show the seeded list; let them rename their bank/card accounts to match real ones (e.g. "Chase Checking ...4521").
2. **Set opening balances.** As of a chosen start date, what's in each bank/card/inventory account? These get posted as a single equity entry (Dr each asset / Cr Owner's Equity, or vice versa for liabilities).
3. **Set sales tax jurisdiction(s) and rate(s).**

### 14.2 Wave data import (optional, v1.5)

Wave can export transaction data as CSV. A future utility could parse the export and create journal entries for each row. Not in scope for v1 — user starts fresh on a chosen cutover date and keeps Wave as a historical reference.

---

## 14. UI Design System

This section documents the **SaltStocks Design System** — the visual language, design tokens, and component patterns that unified the app's interface. The design was created in Claude Design and is being integrated into the accounting module alongside the existing resale inventory interface.

### 14.1 Design Philosophy

**Voice:** Operator-first, calm, plainspoken. No marketing copy, no exclamation points. Help text reads like a friend explaining a workflow over a desk.

**Visual identity:** The "salt-marsh palette" — a quiet, herbal green that reads as "calm spreadsheet" rather than "consumer SaaS." The brand is text-and-token-first, with no photography, illustration, or pattern fill.

**Key principles:**
- **Local-first, founder-run tool** — designed for someone running their own machine, wearing all the hats
- **One column at a time** — even complex screens stack vertically; multi-column only appears inside cards
- **Quiet interactions** — no bouncing, no springs, no glass effects; 220ms ease for all state changes
- **Text and data over decoration** — status communicated by named pills, not icons; buttons are text-only except where an icon truly replaces a label

### 14.2 Color Palette

The salt-marsh palette is defined via CSS custom properties in `colors_and_type.css`. All colors use the `--ss-*` prefix.

#### Surfaces & backgrounds
- **Body background:** `#f4faf8` with a soft radial highlight (`rgba(47,143,115,0.08)` at top-right) and vertical gradient `#f8fcfa → #eef6f2`
- **Primary surface (cards):** `#ffffff` white
- **Secondary surface (tinted):** `#edf7f3`
- **Deep emphasis:** `#dff0e8` (used in pills, SKU chips, highlighted panels)
- **Input fields:** `#fcfffd` (barely perceptible tint over white)

#### Borders
- **Standard:** `#c7ddd3` (1px solid) for resting cards and inputs
- **Strong:** `#8eb9a7` for buttons, bulk-edit frames, emphasized containers
- **Dashed:** `#c7ddd3` for empty-state placeholders

#### Text (ink)
- **Primary:** `#18322d` (near-black with green undertone)
- **Muted/help text:** `#5e7d73`
- **Accent:** `#2f8f73` (the resale-green — primary buttons, focus rings, active tabs)
- **Accent strong:** `#236d57` (headings within cards, primary links)

#### Semantic colors
- **Accent soft (12% alpha):** `rgba(47,143,115,0.12)` — active tab background
- **Accent glow (18% alpha):** `rgba(47,143,115,0.18)` — focus ring
- **Warning:** `#8c6d1f` text on `rgba(140,109,31,0.08)` background (warm sand tone)
- **Danger:** `#8b3f49` (used for "trashed"/"donated" pills, import errors)
- **Success:** uses `--ss-accent` (the green)
- **Info:** `#356f61`

#### Status pill colors (resale lexicon)
Each status has a paired background (alpha-tinted) and foreground (solid):
- **Listed:** `rgba(47,143,115,0.15)` bg / `#236d57` fg
- **Unlisted:** `rgba(94,125,115,0.12)` bg / `#5e7d73` fg
- **Sold:** `rgba(47,143,115,0.08)` bg / `#356f61` fg
- **Donated/Trashed:** `rgba(139,63,73,0.10)` bg / `#8b3f49` fg

### 14.3 Typography

**Font families:**
- **Display (Fraunces, 600):** Page H1s only. Serif with personality. `-0.03em` letter-spacing.
- **UI (Inter, 400/500/600/700):** Everything else. Clean, readable, professional.
- **Monospace (JetBrains Mono, 400/500):** SKUs, codes, tag lists, anything copy-pasteable. `ui-monospace` fallback.

**Type scale (16px base):**
- **Display:** `2.5rem` (40px) — marketing/hero only
- **H1:** `2rem` (32px) — page titles
- **H2:** `1.5rem` (24px) — card section headers
- **H3:** `1.1rem` (17.6px) — list-card titles
- **Body:** `1rem` (16px) — default
- **Help text:** `0.92rem` (14.7px) — muted color
- **Label:** `0.82rem` (13.1px) — field labels, weight 600, muted color, sentence case
- **Pill:** `0.82rem` (13.1px) — status pills
- **Tiny meta (eyebrow):** `0.78rem` (12.5px) — weight 700, letter-spacing `0.08em`, **UPPERCASE** (the only uppercase text in the system)

**Line heights:**
- **Tight:** `1.1` (headings)
- **Snug:** `1.3` (H2/H3)
- **Body:** `1.5` (paragraph text)

**Special typography features:**
- **SKU chip:** monospace on `--ss-surface-deep` pill background, `0.9rem` size, weight 500, `0.01em` tracking
- **Font feature settings:** `'cv11', 'ss01'` enabled globally for Inter (slightly more geometric)

### 14.4 Spacing & Layout

**Spacing scale (4px-derived):**
```
--ss-s-1:  4px
--ss-s-2:  6px
--ss-s-3:  8px
--ss-s-4:  10px
--ss-s-5:  12px
--ss-s-6:  14px
--ss-s-7:  16px
--ss-s-8:  18px
--ss-s-9:  22px
--ss-s-10: 24px
--ss-s-12: 32px
--ss-s-16: 48px
```

**Layout constraints:**
- **Page max-width:** `1380px` (resale list)
- **Form max-width:** `1120px` (forms, settings)
- **Narrow max-width:** `720px` (single-column content)
- **Mobile breakpoint:** `<960px` — everything collapses to single column

**Card padding:** 16–22px (varies by content density)
**Field grid gaps:** 12–14px

### 14.5 Border Radius & Shadows

**Radii:**
- **Input fields:** `12px`
- **Inline panels/notes:** `14px`
- **Primary cards:** `18px` (`--ss-r-card`) — **this is the system signature**
- **Pills:** `999px` (buttons, status pills, filter badges, view-mode toggles)

**Shadows:**
- **Primary card shadow:** `0 12px 24px rgba(24,50,45,0.08)` — soft, low-contrast, deep-green-tinted
- **Kanban card shadow:** `0 8px 16px rgba(24,50,45,0.06)` — smaller variant
- **Focus ring:** `0 0 0 2px rgba(47,143,115,0.18)` — 2px outline, not a shadow
- **No inner shadows** anywhere in the system

**Border + shadow pairing:** Cards always carry **both** a 1px border and the soft shadow. The border holds shape on the gradient background; the shadow lifts it. Removing either flattens the surface.

### 14.6 Component Library

#### Buttons

**Primary button:**
```css
background: var(--ss-accent);
color: white;
border: none;
border-radius: var(--ss-r-pill);
padding: 10px 20px;
font-weight: 600;
transition: background var(--ss-dur-base) var(--ss-ease);

/* Hover */
background: var(--ss-accent-strong);

/* Active/pressed */
transform: translateY(1px);
```

**Ghost/secondary button:**
```css
background: transparent;
color: var(--ss-accent-strong);
border: 1px solid var(--ss-border);
border-radius: var(--ss-r-pill);

/* Hover */
background: var(--ss-surface-alt);
```

**Danger button:**
```css
background: var(--ss-danger);
color: white;
/* Same structure as primary */
```

#### Status pills
```css
display: inline-flex;
align-items: center;
padding: 4px 12px;
border-radius: var(--ss-r-pill);
font-size: var(--ss-fs-pill);
font-weight: 500;
letter-spacing: var(--ss-tracking-pill);
text-transform: lowercase;

/* Example: listed */
background: var(--ss-status-listed-bg);
color: var(--ss-status-listed-fg);
```

#### Cards

**Canonical card recipe (the foundation of the entire system):**
```css
background: var(--ss-surface);
border: 1px solid var(--ss-border);
border-radius: var(--ss-r-card);
box-shadow: var(--ss-shadow-card);
padding: 16px–22px;
```

**Variants:**
- **Tinted card:** `background: var(--ss-surface-alt);` (secondary surfaces)
- **Kanban card:** smaller shadow `var(--ss-shadow-kanban)`, `cursor: grab`, drag state `opacity: 0.55`

#### Form inputs

```css
background: var(--ss-surface-input);
border: 1px solid var(--ss-border);
border-radius: var(--ss-r-input);
padding: 10px 14px;
font-family: var(--ss-font-sans);
font-size: var(--ss-fs-body);
color: var(--ss-text);

/* Focus */
outline: 2px solid var(--ss-accent-glow);
border-color: var(--ss-accent);

/* Disabled */
background: var(--ss-surface-alt);
color: var(--ss-muted);
cursor: not-allowed;
```

**Help text below input:**
```css
font-size: var(--ss-fs-help);
color: var(--ss-muted);
margin-top: 6px;
```

#### Tabs / view toggles

**Active tab:**
```css
background: var(--ss-accent-soft); /* 12% alpha pill */
color: var(--ss-accent-strong);
border-radius: var(--ss-r-pill);
padding: 8px 16px;
font-weight: 600;
```

**Inactive tab:**
```css
background: transparent;
color: var(--ss-muted);
/* Same structure, no background */
```

#### Empty states

```css
border: 2px dashed var(--ss-border-dashed);
border-radius: var(--ss-r-panel);
padding: var(--ss-s-12);
text-align: center;
color: var(--ss-muted);

/* Text */
"No items in this column." (neutral, never cheerful)
```

#### Banners / notes

**Info banner:**
```css
background: var(--ss-warn-soft);
border: 1px solid var(--ss-warn);
border-radius: var(--ss-r-panel);
padding: 12px 16px;
color: var(--ss-warn);
```

**Error banner:**
```css
background: var(--ss-danger-soft);
border: 1px solid var(--ss-danger);
/* Same structure */
```

#### Navigation (top bar)

```css
position: fixed;
top: 0;
left: 0;
right: 0;
background: var(--ss-surface);
border-bottom: 1px solid var(--ss-border);
padding: 12px 24px;
z-index: 100;

/* Logo at 24px */
/* Nav links: accent-strong, weight 600, no underline */
/* Active: slightly darker, no other visual change */
```

### 14.7 Interaction States

**Hover:**
- **Buttons:** Primary darkens to `--ss-accent-strong`; ghost gains `--ss-surface-alt` fill
- **Links:** Primary links (accent-strong, weight 600) gain underline only if they're inline help-text links; nav links rely on color alone
- **Kanban cards:** `cursor: grab`

**Active/pressed:**
- **Buttons:** `transform: translateY(1px)` — 1px inset translation
- **No click-ripple effects**

**Focus:**
- **Inputs:** `outline: 2px solid var(--ss-accent-glow)` + border tightens to `--ss-accent`
- **Buttons:** same outline treatment
- **No drop shadows on focus** — outline only

**Dragging (Kanban):**
- **Card being dragged:** `opacity: 0.55`
- **Drop zone hovered:** `background: var(--ss-accent-soft)` (8% alpha fill)

**Loading:**
- **Inline text only:** `"Updating GV-FUNKO-000001 to listed..."` — no spinners, no skeleton screens
- Page navigation is full reload (no SPA shell)

### 14.8 Animation & Motion

**Timing:**
- **Fast:** `140ms` (icon state changes)
- **Base:** `220ms` (all other transitions — buttons, panels, state changes)
- **Easing:** `cubic-bezier(0.4, 0.0, 0.2, 1)` (Material Design "standard" — smooth deceleration)

**What animates:**
- Bulk-edit bar collapse: `opacity 0.22s ease, transform 0.22s ease` with 10px Y-translate
- Button hover: `background 220ms ease`
- Focus rings: instant (no transition)
- **No bouncing, no springs, no micro-interactions**

**What doesn't animate:**
- Page transitions (full reload)
- Card appearance (instant)
- Modal overlays (not present in system — everything is inline)

### 14.9 Iconography

**Icon set:** [Lucide](https://lucide.dev)
- **Style:** Stroke-based, 1.5px stroke, 24×24 viewbox
- **Color:** Inherits from text color; accent green only for active state on icon-only nav buttons
- **Usage rule:** An icon must replace, not decorate, a label
- **Acceptable:** Tab-bar icons, hamburger menu on mobile, drag handles on Kanban cards
- **Unacceptable:** Random decoration on stat cards, icons next to every form label

**Logo:**
- **File:** `assets/saltstocks-logo.svg`
- **Style:** Custom monoline salt-crystal mark (stacked-cube isometric)
- **Usage:** 24px in top nav, 64px on dashboard hero, tinted `--ss-accent-strong`

**Emoji policy:** None in product UI. (Roadmap in GitHub README uses ✅/🚧 — that's documentation, not product.)

**Unicode separators:** `→` and `·` used as text separators, not as iconography.

### 14.10 Content Voice & Lexicon

**Writing rules:**
- **Second person, imperative for instructions:** "Use the **Backup Now** button" / "Set the date range."
- **Lowercase sentence case** for buttons, headers, table headers. Title Case reserved for brand name "SaltStocks" and page H1s.
- **Short sentences. One idea per line.**
- **Plain English over jargon:** "Backups are stored in `data/backups/`" — not "persistent snapshots."
- **No emoji in UI.** No exclamation points.
- **Code voice for code things:** File paths, env vars, SKU formats, shell commands always in `code spans`.

**Status lexicon (do not paraphrase):**
- Statuses: `unlisted`, `listed`, `sold`, `donated`, `trashed`
- Channels: `ebay`, `etsy`, `direct` (lowercase, configurable)
- Match states (eBay import): `matched`, `unmatched`, `already imported`, `clamped`

**Empty/zero states:**
- "No items in this column." (neutral)
- "No unmatched line items in this preview." (factual)
- Never: "Looks like you're all caught up! 🎉"

**Errors:**
- "Missing required fields: `client_id`, `client_secret`." (factual)
- "eBay auth codes expire after ~5 minutes." (helpful)

**Numbers and money:**
- Currency: `$` prefix, two decimals: `$12.50`
- Quantities: two decimals (`1.00`) for Umivera fractional qty support
- SKUs: uppercase, hyphenated, zero-padded: `GV-FUNKO-000001`

### 14.11 Page Layouts

The design system defines layouts for the five primary app surfaces. Each uses the unified salt-marsh tokens.

#### Dashboard
- **Hero section:** Logo (64px), H1 "SaltStocks", stat cards in grid (3 columns → 1 column mobile)
- **Quick actions:** "Backup Now" button, "Add Entry" button
- **Recent entries widget:** Last 10 entries, table format
- **"Owed back to you" widget:** Big number, detail link, "Reimburse myself" button (accounting-specific)

#### Resale Inventory List
- **Search shell:** Card with input, filters, status dropdown
- **Tabs shell:** Card with tabs (Card / Kanban / Bulk Edit / Import)
- **Tab panels:** Each panel in own card
- **Card view:** Grid of item cards (`repeat(auto-fill, minmax(280px, 1fr))`)
- **Kanban view:** 5 fixed columns (unlisted / listed / sold / donated / trashed)

#### Add/Edit Resale Item Form
- **Single card shell** with form grid (3 columns → 1 column mobile)
- **SKU display:** Read-only SKU chip at top
- **Field groupings:** Visual separation with subtle borders, no heavy section headers
- **Action buttons:** Bottom-right, "Save" primary + "Cancel" ghost

#### Configurator (Settings)
- **Tabbed sections:** Categories / Brand Codes / eBay Credentials
- **Table + form pattern:** Existing items in table, "Add new" form below
- **Danger zone:** Separate card at bottom for destructive actions (if any)

#### eBay Import
- **Date range picker** at top
- **Preview mode:** Table of matched/unmatched line items with status pills
- **Apply mode:** Confirmation summary, "Apply deductions" primary button
- **Idempotent indicator:** "Already imported" status shown clearly

#### Accounting (new surface, to be built per this spec)
Follows the same patterns:
- **Questionnaire steps:** One card per step, big tappable buttons for multi-choice
- **Confirm screen:** Summary card with plain-English description
- **Entry list:** Table in card, same structure as resale list
- **Reports:** HTML table in card + "Download CSV" button

### 14.12 Responsive Behavior

**Breakpoint:** `960px`

**Above 960px (desktop):**
- Multi-column grids active (form: 3 col, bulk: auto-fit, Kanban: 5 col)
- Top nav horizontal
- Search + filters side-by-side

**Below 960px (mobile):**
- **Everything single column**
- Form grid collapses
- Kanban scrolls horizontally (5 columns preserved, container scrolls)
- Top nav: hamburger menu (not yet implemented — use simple stacked nav)
- Touch targets: minimum 44px tap area
- Card padding reduces to 16px

**No tablet-specific breakpoint** — just desktop and mobile.

### 14.13 Implementation Notes

**CSS architecture:**
- All tokens defined in `colors_and_type.css` (or promoted into `app/static/css/resale.css`)
- Current `base.html` 24-line default stylesheet should be **retired** — replace with salt-marsh tokens globally
- Jinja2 templates can reference classes like `.ss-card`, `.ss-btn-primary`, `.ss-pill-listed`

**Component reuse:**
- The card recipe is the foundation — search shell, tabs shell, panels, forms, list items all use it
- Status pills are data-driven — template macro takes `status` string, applies correct `--ss-status-{status}-bg/fg`
- Buttons styled via utility classes (`.ss-btn-primary`, `.ss-btn-ghost`, `.ss-btn-danger`)

**Asset integration:**
- Logo SVG at `app/static/assets/saltstocks-logo.svg`
- Lucide icons loaded via CDN: `<script src="https://unpkg.com/lucide@latest"></script>`

**Font loading:**
- Google Fonts import in CSS: `@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,600&display=swap');`
- System font fallback if fonts fail to load

---

## 15. Phased Build Plan

The build plan is structured so that **the MVP (Phases 1–4) is fully usable without any AI**. The user can replace Wave entirely after Phase 4. AI is added as a Phase 5+ enhancement that doesn't change anything underneath it.

### Phase 1 — Foundation (1–2 weeks)
- New `app/accounting/` module skeleton, models, migrations.
- Chart of Accounts with seed data + admin UI.
- Journal entry + lines + receipts schema.
- Posting engine with balance enforcement (Decimal math, atomic transactions).
- Basic entry list view.
- Receipt upload + on-disk storage.
- **Manual entry form** (admin-style, pick template from dropdown, fill fields directly) — this is the developer's harness for testing the posting engine before the questionnaire is built.

**Exit criteria:** can record any transaction manually with a receipt, see it in a list, and the books balance.

### Phase 2 — Questionnaire (1–2 weeks) — THE CORE MVP FEATURE
- Define `TransactionAnswerSet` schema (the canonical contract).
- Build the questionnaire engine: step definitions, branching, server-driven flow.
- Build questionnaire UI: per-step Jinja2 partials, **big tappable buttons** (see §14.6 Component Library), mobile-friendly.
- **Apply the SaltStocks design system** (§14) to all accounting templates — use the salt-marsh palette tokens, canonical card recipe, status pills, and typography scale.
- Wire all v1 templates (BUY_INVENTORY, BUY_INVENTORY_PERSONAL, BUY_EXPENSE_PERSONAL, REIMBURSE_OWNER, all expense templates, etc.) to questionnaire flows.
- **Freight-in handling:** questionnaire step + allocation utility + multi-line inventory support on the inventory step.
- Confirm screen with plain-English summary (including freight-allocated unit cost shown clearly).
- Replace the manual form from Phase 1 as the user-facing entry path.

**Exit criteria:** user can record any v1 transaction by clicking through the questionnaire, including multi-SKU inventory purchases with inbound shipping. The accounting UI is visually unified with the rest of Saltstocks.

### Phase 3 — Reports (1 week)
- P&L, Balance Sheet, Expense by Category.
- CSV export per report.
- Year-end ZIP export bundle.

**Exit criteria:** can produce tax-ready outputs from the questionnaire-entered data.

### Phase 4 — Sales tax + receipt vault polish (1 week)
- Sales tax rates table + admin UI.
- Sales tax handling on sales entries (questionnaire steps for taxable/non-taxable + amount).
- Sales tax remittance report.
- Inventory linkage on `BUY_INVENTORY` (link to existing SKU or create new).
- Auto-COGS posting when sales hit (manual or eBay-imported).
- Receipt vault browse view (thumbnail grid, filters, search).
- "Owed back to you" dashboard widget + Reimburse Myself flow.
- Backup integration extended to include `data/receipts/`.

**🎯 EXIT CRITERIA: WAVE CAN BE TURNED OFF.** This is the MVP completion line. The user has a fully functional, AI-free accounting system that replaces Wave.

---

### Phase 5 — AI enhancement (1–2 weeks, optional)
- Add `app/accounting/nlp.py`.
- Anthropic API integration with the `TransactionAnswerSet` schema.
- "Talk to AI" entry path on the Add Entry chooser screen.
- Hand-off to questionnaire for any low-confidence fields.
- Confidence thresholds, validation guardrails, fallback on API errors.
- Setting to disable AI mode entirely.

**Exit criteria:** user can type a sentence and get a pre-filled questionnaire that they confirm.

### Phase 6 — Quality of life (ongoing)
- Better fuzzy matching on payment accounts.
- Receipt OCR (if user changes mind on auto-categorization).
- Recurring entry templates (rent, subscriptions auto-post monthly).
- Period locking after tax filing.
- Wave CSV import.
- Mixed personal/business expense splits (e.g., 80/20 phone bill).

---

## 17. Risks & Open Questions

| Risk | Mitigation |
|---|---|
| User picks the wrong template in the questionnaire | Confirm screen shows plain-English summary before posting; user can void any entry and re-record (creates a reversing entry). |
| User forgets to attach receipt | Dashboard shows "entries missing receipts" count as a nag; receipt vault filter for "missing receipt." |
| Cash basis vs. accrual confusion at tax time | Reports clearly labeled "Cash Basis"; CPA-friendly export. |
| Receipt files lost (disk failure) | Backup includes receipts; consider optional cloud sync (S3) in v1.5. |
| Inventory quantity drift between accounting and inventory module | Inventory module remains source of truth for quantity; accounting only mirrors costs. Periodic reconciliation report. |
| Questionnaire feels too long for repeat users | Phase 6: add "Quick repeat" — clone an existing entry as a starting point. Also: AI mode (Phase 5) skips most steps for confident users. |
| **(Phase 5+)** AI mis-classifies transactions in subtle ways | Always hand off low-confidence fields to questionnaire; user always sees Confirm screen before posting; AI is opt-in and can be disabled. |
| **(Phase 5+)** Anthropic API cost / latency / unreachable | Questionnaire is always available as fallback; AI failures are transparent and silent (just shows the questionnaire instead). |

### Open questions for the user
1. Will you be the only user, or will anyone else (accountant, partner) need access?
2. What state(s) collect sales tax from you? Single jurisdiction or multiple?
3. Do you currently take an "owner's draw" regularly, or run payroll? (v1 assumes owner's draw only.)
4. How far back do you want to import historical data, if at all?
5. Do you want the year-end export to land somewhere specific (Dropbox, etc.) automatically?
6. **Do you currently have a dedicated business bank account and/or business credit card, or is most spending on personal cards today?** This affects how prominently the personal-funds flow is featured (it's already featured heavily, but if 100% of purchases are personal, we may want to make it the default rather than a prompt).
7. Are there any expense categories you commonly mix personal/business (e.g., a phone bill that's 80% business / 20% personal)? That's a separate flow we may want to design.
8. **For wholesaler invoices: do they typically itemize shipping as a separate line, or is it sometimes bundled into the per-unit price?** If sometimes bundled, the questionnaire's freight field is just $0 in those cases — no special handling needed. But if you frequently get invoices with separate handling fees, fuel surcharges, etc., we may want to support a few line items in the freight field rather than one combined number.

---

## 18. Appendix: Worked Examples

### Example A — "I bought 12 plushies for inventory today for $12, paid Chase card"

**Parsed:** template `BUY_INVENTORY`, amount $12.00, payment Chase Card, qty 12, item plushies.
**Questionnaire:** "Link to existing SKU or create new?" → user picks "create new." Existing auto-SKU logic generates the SKU.
**Journal entry:**
```
Dr. 1200 Inventory                 $12.00
    Cr. 2010 Credit Card — Chase           $12.00
```
**Inventory side-effect:** new inventory row, qty=12, unit_cost=$1.00.

### Example B — "Stayed at a hotel for business for $60, paid with debit card"

**Parsed:** template `TRAVEL_HOTEL`, amount $60.00, payment Bank — Primary Checking.
**Questionnaire:** "Vendor name?" (optional, can skip) "Trip purpose for memo?" (optional).
**Journal entry:**
```
Dr. 6020 Travel                    $60.00
    Cr. 1020 Bank — Primary Checking       $60.00
```

### Example C — "Sold a plushie on eBay for $25, eBay deducted $3 in fees, customer paid $2.50 sales tax"

(This usually flows from the existing eBay import, not plain-language entry, but for completeness:)

**Composite entry produced automatically:**
```
Dr. 1020 Bank                      $24.50    (the $27.50 less the $3 fee)
Dr. 6060 Marketplace Fees          $ 3.00
    Cr. 4000 Sales Revenue                 $25.00
    Cr. 2100 Sales Tax Payable             $ 2.50

(plus auto-COGS entry)
Dr. 5000 Cost of Goods Sold        $ 1.00    (weighted-avg cost)
    Cr. 1200 Inventory                     $ 1.00
```

### Example D — "I bought 12 plushies for inventory for $12 on my PERSONAL Chase card"

**Parsed:** template `BUY_INVENTORY_PERSONAL`, amount $12.00, funding source = personal, qty 12.
**Questionnaire:** "Link to existing SKU or create new?" → user picks "create new."
**Journal entry:**
```
Dr. 1200 Inventory                 $12.00
    Cr. 3100 Owner Contributions           $12.00
```
**Inventory side-effect:** new inventory row, qty=12, unit_cost=$1.00.
**Dashboard impact:** "Owed back to you" goes up by $12.00.

### Example E — Selling one of those plushies later for $5 (business bank deposit)

This is two automatic entries:

**Sale entry:**
```
Dr. 1020 Bank                      $5.00
    Cr. 4000 Sales Revenue                 $5.00
```

**Auto-generated COGS entry (separate journal entry, same timestamp):**
```
Dr. 5000 Cost of Goods Sold        $1.00
    Cr. 1200 Inventory                     $1.00
```

**Notice:** "Owed back to you" did **NOT** change. You're still owed the original $12 you put in. The $4 of profit ($5 sale − $1 COGS) is now sitting in the business bank account — separate from the $12 reimbursement obligation.

### Example F — User clicks "Reimburse myself" for the full balance ($12) after some plushies have sold

**Parsed:** template `REIMBURSE_OWNER`, amount $12.00, source Bank.
**Journal entry:**
```
Dr. 3200 Owner Draws               $12.00
    Cr. 1020 Bank                          $12.00
```
**Dashboard impact:** "Owed back to you" goes from $12.00 → $0.00. The $12 leaves the business bank account and lands in your personal account (which the system doesn't track — that's outside the business).

### Example G — "Stayed at a hotel for business for $60, paid with my personal card"

**Parsed:** template `BUY_EXPENSE_PERSONAL`, amount $60.00, expense category = Travel.
**Questionnaire:** "Which expense category?" → user picks "Travel" (or NLP infers it from "hotel").
**Journal entry:**
```
Dr. 6020 Travel                    $60.00
    Cr. 3100 Owner Contributions           $60.00
```
**Dashboard impact:** "Owed back to you" goes up by $60.00. The hotel still hits the P&L as a Travel expense (lowering net income for tax purposes), AND the business now owes you $60 to reimburse.

This is the answer to the original question: **the system tracks both things separately and automatically.** The expense gets recognized for taxes (Travel), and your reimbursement ledger goes up (Owner Contributions). When the business pays you back, that's a separate Owner Draw entry — it doesn't double-count anything.

### Example H — Inbound shipping on an inventory purchase, single SKU

User answers in questionnaire:
- *What kind?* → Bought inventory
- *Whose money?* → Personal funds
- *Cost?* → $12.00
- *When?* → Today
- *Vendor?* → "Plushie Wholesale Co."
- *Inventory link?* → Create new SKU, "plushies", quantity 12
- *Inbound shipping?* → **$3.00**

**Allocation:** $3.00 entirely to the plushies (single SKU). Per-unit cost = ($12.00 + $3.00) ÷ 12 = **$1.25 each.**

**Journal entry:**
```
Dr. 1200 Inventory                 $15.00
    Cr. 3100 Owner Contributions           $15.00
```

**Inventory side-effect:** new SKU "plushies", qty=12, unit_cost=**$1.25**.
**Dashboard impact:** "Owed back to you" goes up by **$15.00** (not $12 — they paid $15 of personal money for it).

**Plain-English summary on Confirm screen:**
> *You bought 12 of "plushies" for $12.00 + $3.00 shipping = $15.00 total today using personal funds. The business will record this as $15.00 of inventory (each plushie costs $1.25 with shipping included) and will owe you $15.00 back.*

### Example I — Inbound shipping on an inventory purchase, MULTIPLE SKUs

User bought a mixed shipment from a wholesaler:
- 10 plushies @ $1.00 = $10.00
- 5 keychains @ $2.00 = $10.00
- Subtotal: $20.00
- Shipping: $4.00
- **Total paid: $24.00 on personal card**

Questionnaire flow lets the user add multiple inventory lines on the inventory step (a "+ Add another item" button). User enters both lines, then $4.00 in the shipping field.

**Allocation:**
- Plushies' share: ($10.00 / $20.00) × $4.00 = $2.00 → unit cost = ($10.00 + $2.00) ÷ 10 = **$1.20 each**
- Keychains' share: ($10.00 / $20.00) × $4.00 = $2.00 → unit cost = ($10.00 + $2.00) ÷ 5 = **$2.40 each**

**Journal entry (single combined entry, the $4 freight is invisible in the books):**
```
Dr. 1200 Inventory                 $24.00
    Cr. 3100 Owner Contributions           $24.00
```

**Inventory side-effects:**
- "plushies" SKU, qty=10, unit_cost=$1.20
- "keychains" SKU, qty=5, unit_cost=$2.40

**Dashboard impact:** "Owed back to you" goes up by **$24.00**.

### Example J — Sale of an item that had freight allocated

Six months later, user sells one of the plushies from Example H for $5.00 cash, paid into the business bank.

**Sale entry:**
```
Dr. 1020 Bank                      $5.00
    Cr. 4000 Sales Revenue                 $5.00
```

**Auto-COGS entry** (uses the freight-inclusive unit cost):
```
Dr. 5000 Cost of Goods Sold        $1.25
    Cr. 1200 Inventory                     $1.25
```

Note that COGS is **$1.25, not $1.00.** The 25¢ of freight is now flowing through the P&L exactly as the IRS expects — when the related item sells, not in the year it was purchased. No special logic was needed at sale time; the inventory module just used the unit cost it had on file.

---

*End of specification.*

---

## 19. Implementation To-Do List

Check items off as they are completed. Items are ordered so each one can be done independently in a single session. Pick up at the first unchecked item after any interruption.

---

### Phase 1 — Foundation

**Module scaffold**
- [x] Create `app/accounting/` directory with `__init__.py`
- [x] Create `app/accounting/models.py` — Python dataclass or namedtuple definitions for `Account`, `JournalEntry`, `JournalLine`, `Receipt`, `SalesTaxRate` (used internally; tables defined in migrate)
- [x] Create `app/accounting/exceptions.py` — `UnbalancedEntryError`, `EmptyEntryError`, `VoidedEntryError`

**Database migrations**
- [x] Add `accounts` table to `app/migrate.py` with all columns from §6.1; seed the full default chart of accounts on first run
- [x] Add `journal_entries` table to `app/migrate.py`
- [ ] Add `journal_lines` table to `app/migrate.py` with CHECK constraint (debit XOR credit)
- [ ] Add `receipts` table to `app/migrate.py`
- [ ] Add `sales_tax_rates` table to `app/migrate.py`; seed North Carolina rate

**Posting engine**
- [ ] Create `app/accounting/posting.py` — `post_entry(conn, answer_set)`: validates balance (Decimal, debits == credits), writes `journal_entries` + `journal_lines` atomically, raises on imbalance or zero
- [ ] Add `void_entry(conn, entry_id, reason)` to `posting.py` — writes a reversing entry, marks both rows `is_void=True`
- [ ] Add `allocate_freight_in(line_items, freight_amount)` utility to `posting.py` — dollar-weighted allocation, last item absorbs rounding remainder (see §8.5)

**Template catalog**
- [ ] Create `app/accounting/catalog.py` — define all ~24 templates from §3.4 as Python dicts/dataclasses: `id`, `name`, `debit_account_code`, `credit_account_code`, `required_fields`

**Router & nav**
- [ ] Create `app/accounting/routes.py` — FastAPI `APIRouter`, register in `app/main.py` with prefix `/accounting`
- [ ] Add "Accounting" link to `app/templates/base.html` nav

**Chart of accounts UI**
- [ ] `GET /accounting/accounts` — list all accounts, grouped by type; HTML table
- [ ] `POST /accounting/accounts` — add a custom account (name, type, subtype)
- [ ] `POST /accounting/accounts/{id}` — rename or toggle `is_active`; block delete on `is_system_protected` accounts

**Manual entry form (developer harness — replaced in Phase 2)**
- [ ] `GET /accounting/entry/manual` — form: template dropdown, date, amount, vendor, memo, optional payment account
- [ ] `POST /accounting/entry/manual` — validates, calls `post_entry()`, redirects to entry list
- [ ] Create `app/templates/accounting/entry_manual.html`

**Entry list & detail**
- [ ] `GET /accounting/entries` — table: date, template, vendor, amount, void status; filters: date range, template
- [ ] `GET /accounting/entries/{id}` — detail: journal lines (account name, debit, credit), receipt thumbnails, void button
- [ ] Create `app/templates/accounting/entry_list.html` and `entry_detail.html`

**Receipt storage**
- [ ] `POST /accounting/entries/{id}/receipts` — accept file upload, compute SHA-256, store under `data/receipts/YYYY/MM/{uuid}.ext`, insert `receipts` row
- [ ] `GET /accounting/receipts/{id}/file` — stream file from disk

**Exit criteria checkpoint:** can record any transaction manually, see it in the list with journal lines, and attach a receipt.

---

### Phase 2 — Questionnaire (Core MVP)

**Canonical schema**
- [ ] Create `app/accounting/schemas.py` — `TransactionAnswerSet` Pydantic model (all fields from §5.1), `InventoryLink` model, `LineItem` model (for multi-SKU purchases)

**Questionnaire engine**
- [ ] Create `app/accounting/questionnaire.py` — `Step` class (id, question, input_type, options, maps_to, shown_when, optional, default, help_text); `QuestionnaireSession` that holds partial answer-set and current step
- [ ] Add session storage for in-progress questionnaires (SQLite `questionnaire_sessions` table OR server-side dict keyed by session token — SQLite preferred for persistence)
- [ ] `GET /accounting/entry/start` — creates session, returns first step (transaction type picker)
- [ ] `POST /accounting/entry/answer` — body: `{session_id, step_id, answer}`; advances session, returns next step or `{done: true, answer_set: ...}`
- [ ] `POST /accounting/entry/preview` — body: completed answer-set; calls template engine to produce proposed `JournalEntry` with lines + plain-English summary string; does NOT write to DB
- [ ] `POST /accounting/entry/confirm` — body: confirmed answer-set + receipt files; calls `post_entry()` atomically, stores receipts, redirects to entry detail

**Questionnaire UI templates**
- [ ] Create `app/templates/accounting/questionnaire_step.html` — shell with progress indicator ("Step N of ~M"), Back button, renders the current step partial
- [ ] Create step partial: `_step_multi_choice.html` — big tappable buttons (see §14.6 for button styling)
- [ ] Create step partial: `_step_number.html` — number input with optional help text
- [ ] Create step partial: `_step_date.html` — date picker with Today / Yesterday quick buttons
- [ ] Create step partial: `_step_text.html` — free-text input, optional flag
- [ ] Create step partial: `_step_account_picker.html` — dropdown filtered by subtype
- [ ] Create step partial: `_step_inventory_picker.html` — "create new" vs "link existing SKU" with search; "+ Add another item" button for multi-SKU purchases
- [ ] Create step partial: `_step_file_upload.html` — drag/drop or skip
- [ ] Create `app/templates/accounting/entry_confirm.html` — plain-English summary card (use canonical card recipe from §14.6), Edit / Record It buttons

**Apply design system**
- [ ] Import `colors_and_type.css` tokens into `app/static/css/` (or merge into existing `resale.css`)
- [ ] Apply salt-marsh palette to all accounting templates — use `--ss-*` CSS variables throughout
- [ ] Use canonical card recipe (§14.6) for all shells (search, tabs, panels, forms)
- [ ] Use status pill pattern for any status display
- [ ] Apply typography scale — H1s use Fraunces display font, body uses Inter
- [ ] Ensure all buttons use pill radius (`--ss-r-pill`) and appropriate variants (primary/ghost/danger)

**Wire templates to questionnaire flows**
- [ ] Wire `BUY_INVENTORY` — business funds inventory purchase (questions: type → amount → date → vendor → payment account → inventory link → freight-in → receipt)
- [ ] Wire `BUY_INVENTORY_PERSONAL` — personal funds inventory purchase (same flow, skips payment account, credit = Owner Contributions)
- [ ] Wire `BUY_EXPENSE_PERSONAL` — personal funds business expense (questions: type → expense category → amount → date → vendor → receipt)
- [ ] Wire `REIMBURSE_OWNER` — business pays owner back (questions: amount ≤ current balance → source bank account → date → memo)
- [ ] Wire `SELL_INVENTORY_CASH` + auto `COGS_RECOGNITION` — manual sale entry (deferred; most sales come from eBay import)
- [ ] Wire expense templates: `BUSINESS_MEAL`, `TRAVEL_HOTEL`, `TRAVEL_TRANSPORT`, `OFFICE_SUPPLIES`, `SOFTWARE_SUBSCRIPTION`, `SHIPPING_OUTBOUND`, `EBAY_FEES`, `PAYMENT_PROCESSING_FEE`, `UTILITIES`, `RENT`, `PROFESSIONAL_SERVICES`, `BANK_FEE`
- [ ] Wire `OWNER_CONTRIBUTION`, `OWNER_DRAW`, `PAY_CREDIT_CARD`, `SALES_TAX_REMITTED`, `OTHER_EXPENSE`, `OTHER_INCOME`

**Freight-in**
- [ ] Add freight-in step to `BUY_INVENTORY` and `BUY_INVENTORY_PERSONAL` flows — shown after inventory link step; calls `allocate_freight_in()` before posting; updates per-unit cost on each inventory line
- [ ] Show freight-allocated unit cost on Confirm screen summary ("each plushie costs $1.25 with shipping included")

**Replace manual form**
- [ ] Update "Add Entry" button to link to `/accounting/entry/start` (questionnaire) instead of manual form; keep `/accounting/entry/manual` accessible for dev/admin use

**Exit criteria checkpoint:** user can record any v1 transaction by clicking through the questionnaire, including multi-SKU inventory purchases with inbound shipping. The accounting UI is visually unified with the rest of Saltstocks.

---

### Phase 3 — Reports

- [ ] Create `app/accounting/reports.py` — SQL queries for P&L, Balance Sheet, Expense by Category, General Ledger
- [ ] `GET /accounting/reports/pnl?from=&to=` — HTML report + "Download CSV" button; format matches §11.1
- [ ] `GET /accounting/reports/balance-sheet?asof=` — HTML + CSV; §11.2
- [ ] `GET /accounting/reports/expenses-by-category?from=&to=` — HTML table + CSV; §11.3
- [ ] `GET /accounting/reports/ledger/{account_id}?from=&to=` — chronological entry list with running balance; §11.5
- [ ] `GET /accounting/reports/year-end-export/{year}` — ZIP bundle: P&L CSV, Expense CSV, General Ledger CSV, all receipts organized by month, SQLite snapshot; §11.6
- [ ] Add Reports nav section to accounting area (links to each report)
- [ ] Apply design system to all report templates (canonical card for report container, table styling)

**Exit criteria checkpoint:** can produce tax-ready CSV outputs from questionnaire-entered data.

---

### Phase 4 — Sales Tax, Receipt Vault, Dashboard Widget

**Sales tax**
- [ ] Sales tax rates admin UI — `GET/POST /accounting/settings/sales-tax`; add/edit jurisdictions and rates; default = North Carolina
- [ ] Add sales tax step to `SELL_INVENTORY_CASH` questionnaire flow (taxable? yes/no → amount or auto-calc from rate)
- [ ] `GET /accounting/reports/sales-tax?from=&to=` — collected, remitted, net liability; §10.3
- [ ] Wire `SALES_TAX_REMITTED` to clear Sales Tax Payable balance

**Inventory linkage on buy**
- [ ] On `BUY_INVENTORY` / `BUY_INVENTORY_PERSONAL` confirm: if "create new SKU" → call existing `get_next_sku()` and insert into `items` table; if "link existing" → increment `qty_on_hand` and update `unit_cost` (weighted average)

**Auto-COGS on eBay import**
- [ ] Hook into `app/routers/ebay.py` `ebay_import_run` apply path — after each `UPDATE items SET qty_on_hand=...`, post a `COGS_RECOGNITION` journal entry: Dr. COGS / Cr. Inventory, amount = qty_sold × unit_cost

**Receipt vault browse**
- [ ] `GET /accounting/receipts` — thumbnail grid (or icon list for non-images), filters: date range, has-receipt vs missing-receipt, template; click → entry detail; §9.2

**"Owed back to you" widget**
- [ ] Add widget to main dashboard (`app/templates/dashboard.html`) — queries Owner Contributions balance − Owner Draws balance; shows big number + "Reimburse myself" link
- [ ] `GET /accounting/owner-balance` — detail page: two columns (contributions list, draws list), running totals, "Reimburse myself" button that pre-fills `REIMBURSE_OWNER` questionnaire with full balance

**Backup extension**
- [ ] Extend `app/routers/dashboard.py` `backup_now()` — after copying the SQLite file, also `shutil.copytree` `data/receipts/` into the backup archive

**Exit criteria checkpoint: Wave can be turned off.** Full accounting system operational without AI.

---

### Phase 5 — AI Enhancement (Optional, Post-MVP)

- [ ] Create `app/accounting/nlp.py` — Anthropic API call: sends user text + template catalog + account list, receives partial `TransactionAnswerSet` JSON with per-field confidence scores
- [ ] Add "Talk to AI" option on entry chooser screen (`GET /accounting/entry/start`) — shown only when AI mode is enabled in settings
- [ ] `POST /accounting/entry/parse` — body: `{description}`; calls `nlp.py`, validates output, returns partial answer-set + list of remaining low-confidence questionnaire steps
- [ ] Hand-off: any field with confidence < 0.7 or missing → injects corresponding questionnaire step before Confirm screen
- [ ] Add AI mode enable/disable setting to accounting settings page
- [ ] Fallback: if Anthropic API unreachable, show notice and open questionnaire directly

---

### Phase 6 — Quality of Life (Ongoing)

- [ ] "Quick repeat" — clone an existing entry as a questionnaire starting point
- [ ] Recurring entry templates — rent and subscriptions auto-post on a schedule
- [ ] Period locking — add `closed_periods` table; block new entries in locked years
- [ ] Wave CSV import utility — parse Wave export, create journal entries for each row
- [ ] Mixed personal/business expense splits — e.g., 80/20 phone bill split across expense + personal draw
- [ ] Mileage/gas tracking — simplified mileage log with IRS rate auto-calculation
