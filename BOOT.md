# SaltStocks — BOOT.md
> **The MAP.** Read this first every session. Go deeper only if the task requires it.
> Last updated: 2026-06-08 | v2.4.0

---

## File Map

| File | Lines | Purpose |
|------|-------|---------|
| `app/main.py` | 65 | FastAPI app init, router mounts, startup hook (runs migrate) |
| `app/db.py` | 21 | `get_db()` connection helper, `DB_PATH` |
| `app/migrate.py` | 574 | Idempotent schema migrations + seed data — runs on every startup |
| `app/schema.sql` | — | Base DDL (tables, indexes, triggers) |
| `app/auth.py` | 94 | Session auth, `require_auth` dependency |
| `app/deps.py` | — | FastAPI dependency helpers |
| `app/constants.py` | — | App-wide constants |
| `app/utils.py` | — | Shared utilities |
| `app/cli.py` | — | CLI entry point |
| **Routers** | | |
| `app/routers/dashboard.py` | 264 | `GET /`, `POST /backup` |
| `app/routers/resale.py` | 739 | `/resale` CRUD, bulk-update, import, adjust, status |
| `app/routers/materials.py` | 530 | `/materials` CRUD, recipes, production runs |
| `app/routers/ebay.py` | 612 | `/ebay/oauth/*`, `/ebay/import` |
| `app/routers/config.py` | — | `/config` settings UI |
| `app/routers/auth.py` | — | `/login`, `/logout`, `/change-password` |
| **Accounting module** | | |
| `app/accounting/routes.py` | 2875 | All `/accounting/*` and `/entry/*` routes — largest file |
| `app/accounting/questionnaire.py` | 616 | `GLOBAL_FLOW` step list, step logic, `build_answer_set()` |
| `app/accounting/catalog.py` | 382 | `CATALOG` dict, `Template` dataclass, `resolve_lines()` |
| `app/accounting/posting.py` | 248 | `post_entry()`, `post_entries()`, `PostEntryRequest`, `JournalLineInput` |
| `app/accounting/schemas.py` | 121 | `TransactionAnswerSet` Pydantic model |
| `app/accounting/reports.py` | — | Report query functions |
| `app/accounting/models.py` | — | DB-layer models |
| `app/accounting/nlp.py` | — | NLP transaction parser |
| **Integrations** | | |
| `app/integrations/ebay.py` | — | eBay API client, `EbayClient`, OAuth helpers |
| **Templates** | | |
| `app/templates/base.html` | — | Global nav, CSS vars |
| `app/templates/accounting/_step_inventory_picker.html` | — | Multi-item purchase builder + sale item selector |
| `app/templates/materials/form.html` | — | Add/Edit material — has Total Paid → unit cost auto-calc |
| **Static** | | |
| `app/static/css/tokens.css` | — | Design tokens / CSS variables |
| **Docs** | | |
| `docs/CHANGELOG.md` | — | Version history (latest: 2.4.0) |
| `docs/BACKLOG.md` | — | Prioritized feature backlog |
| `docs/DEV_GUIDE.md` | — | Legacy dev guide (superseded by CLAUDE.md) |
| `docs/ADMIN_RUNBOOK.md` | — | Deployment & ops |
| `VPS_BACKLOG.md` | — | VPS migration task list |
| **Data (git-ignored)** | | |
| `data/saltstocks.db` | — | SQLite production database |
| `data/backups/` | — | Timestamped DB backups |
| `data/receipts/` | — | Uploaded receipt files |
| `.session/SCRATCH.md` | — | In-flight session worklog (volatile, git-ignored) |

---

## Route Index

| Method | Path | Handler file |
|--------|------|-------------|
| GET | `/` | dashboard.py |
| POST | `/backup` | dashboard.py |
| GET/POST | `/resale`, `/resale/new`, `/resale/{id}/edit` | resale.py |
| POST | `/resale/bulk-update`, `/resale/import` | resale.py |
| POST | `/resale/{id}/adjust`, `/resale/{id}/status` | resale.py |
| GET | `/resale/export` | resale.py |
| GET/POST | `/materials`, `/materials/new`, `/materials/{id}/edit` | materials.py |
| POST | `/materials/{id}/adjust` | materials.py |
| GET/POST | `/materials/recipes`, `/materials/recipes/new` | materials.py |
| GET/POST | `/materials/recipes/{id}` | materials.py |
| GET/POST | `/materials/produce` | materials.py |
| GET | `/materials?_json=1` | materials.py — JSON list for JS pickers |
| GET/POST | `/ebay/oauth/start`, `/ebay/oauth/exchange`, `/ebay/oauth/callback` | ebay.py |
| GET/POST | `/ebay/import` | ebay.py |
| GET | `/accounting/` | accounting/routes.py |
| GET/POST | `/accounting/accounts` | accounting/routes.py |
| GET/POST | `/accounting/settings/sales-tax` | accounting/routes.py |
| GET/POST | `/accounting/settings/ai` | accounting/routes.py |
| GET/POST | `/accounting/entry/manual` | accounting/routes.py |
| GET | `/accounting/entries`, `/accounting/entries/{id}` | accounting/routes.py |
| POST | `/accounting/entries/{id}/void`, `/accounting/entries/{id}/receipts` | accounting/routes.py |
| GET | `/accounting/receipts`, `/accounting/receipts/{id}/file` | accounting/routes.py |
| GET | `/accounting/lots/{id}` | accounting/routes.py |
| GET | `/accounting/owner-balance` | accounting/routes.py |
| GET | `/accounting/reports/pnl`, `/balance-sheet`, `/expenses-by-category`, `/sales-tax`, `/ledger/{id}` | accounting/routes.py |
| GET | `/accounting/reports/year-end-export/{year}` | accounting/routes.py |
| GET/POST | `/accounting/entry/start`, `/entry/ai-start`, `/entry/parse` | accounting/routes.py |
| GET/POST | `/accounting/entry/step/{session_id}` | accounting/routes.py |
| POST | `/accounting/entry/answer`, `/entry/back`, `/entry/preview`, `/entry/confirm` | accounting/routes.py |

---

## Data Schema (key tables)

| Table | Key Columns |
|-------|-------------|
| `items` | id, item_type ('resale'\|'material'), name, sku, unit, qty_on_hand REAL, unit_cost REAL, reorder_point REAL, company, category, brand, brand_code, ip, notes, status |
| `lots` | id, item_id→items, purchase_date, qty_purchased, qty_remaining, unit_cost, source |
| `accounts` | id, code (e.g. '1200'), name, account_type |
| `journal_entries` | id, entry_date, description, template_id, total_amount, created_by_method, notes |
| `journal_lines` | id, entry_id→journal_entries, account_id→accounts, debit, credit, memo, inventory_item_id |
| `recipes` | id, name, yield_qty REAL, yield_unit, notes |
| `recipe_lines` | id, recipe_id→recipes, material_id→items, qty_per_batch REAL, unit |
| `production_runs` | id, entry_date, recipe_id, batches REAL, finished_item_id, notes |
| `production_run_materials` | id, run_id, material_id, material_name, qty_used, unit, unit_cost, total_cost |
| `ebay_credentials` | id, profile ('SANDBOX'\|'PRODUCTION'), client_id, client_secret, refresh_token, environment |
| `ebay_import_log` | id, order_id, line_item_id — idempotency guard |
| `sku_counters` | (company, code) → next_seq |
| `codes` | company, code, label — allowed SKU brand/item codes |
| `settings` | key, value — app-level config (sales tax rate, AI settings, etc.) |

---

## Key Architectural Patterns

**Double-entry accounting:**  `post_entry()` / `post_entries()` in `posting.py` — always call these, never raw INSERT into journal tables. Accept `PostEntryRequest` + optional `after_insert` callback for atomic multi-step transactions (e.g. sale deducts inventory inside the same SQLite transaction).

**Questionnaire engine:** `GLOBAL_FLOW` in `questionnaire.py` — list of `Step` objects with `shown_when` lambdas. Session state in `QuestionnaireSession`. Build final answer set with `build_answer_set()`. Template IDs control which steps are shown via `_TEMPLATES_WITH_INVENTORY_LINK`, `_TEMPLATES_WITH_MATERIAL_LINK`, `_SYSTEM_TEMPLATES`, etc.

**Transaction templates:** `CATALOG` in `catalog.py` — `resolve_lines()` converts a `TransactionAnswerSet` into `JournalLineInput` list. Special branches for `SELL_INVENTORY_CASH` (tax split), `SELL_INVENTORY_EBAY` (fees + shipping), `PRODUCTION_RUN` (system-only reclassification).

**Inventory cost:** Weighted average cost. Unit cost recalculated on every purchase via `(old_qty × old_cost + new_qty × new_cost) / total_qty`.

**item_type field:** `'resale'` for sellable inventory; `'material'` for production inputs. Same `items` table, different routes and pickers.

---

## Transaction Template Catalog

| Template ID | Description | Debit → Credit |
|-------------|-------------|----------------|
| `BUY_INVENTORY` | Resale inventory, business funds | 1200 → payment acct |
| `BUY_INVENTORY_PERSONAL` | Resale inventory, personal funds | 1200 → 3100 |
| `BUY_MATERIALS` | Production materials, business funds | 1200 → payment acct |
| `BUY_MATERIALS_PERSONAL` | Production materials, personal funds | 1200 → 3100 |
| `SELL_INVENTORY_CASH` | Cash/card sale | payment acct → 4000 (+ 2100 if tax) |
| `SELL_INVENTORY_EBAY` | eBay sale | payment acct + 6060 → 4000 gross |
| `COGS_RECOGNITION` | Auto-posted on every sale | 5000 → 1200 |
| `PRODUCTION_RUN` | System-only; materials → finished goods | 1200 → 1200 (reclassify) |
| `OWNER_CONTRIBUTION` | Owner puts money in | payment acct → 3000 |
| `OWNER_DRAW` | Owner takes money out | 3000 → payment acct |

---

## Change Cookbook

| Task | Where to look |
|------|--------------|
| Add a new transaction type | `catalog.py` (Template + resolve_lines branch), `questionnaire.py` (steps + shown_when), `accounting/routes.py` (_build_summary_text, inventory/COGS hooks) |
| Add a questionnaire step | `questionnaire.py` — add `Step` to `GLOBAL_FLOW`, update `shown_when` lambdas |
| Add a DB column or table | `migrate.py` — additive only, use `ensure_column()` / `if not table_exists()` |
| Add a new page | New router file, add `include_router` in `main.py`, new template, nav link in `base.html` |
| Change accounting logic | `posting.py` (core), `catalog.py` (line generation), `accounting/routes.py` (orchestration) |
| Add an inventory field | `items` table via migrate, `resale.py` form handling, `resale_form.html` template |

---

## Session Log *(rolling 20)*

| Date | Change |
|------|--------|
| 2026-06-08 | `form.html` — Total Paid field auto-computes unit cost; no backend change needed |
| 2026-06-05 | Materials module: `/materials` CRUD, recipes, production runs, `PRODUCTION_RUN` accounting, `BUY_MATERIALS` templates |
| 2026-06-05 | `SELL_INVENTORY_EBAY` template: eBay fees + shipping → atomic journal entry + inventory deduction |
| 2026-05-08 | Split purchase flow: `ExpenseSplit` model, mixed inventory+expense on one receipt |
| 2026-01-28 | eBay import: OAuth, `getOrders`, idempotency guard, preview-first flow |
| 2026-01-08 | Accounting module v1: double-entry engine, questionnaire, chart of accounts, reports |
| 2026-01-03 | Initial app: FastAPI + SQLite, resale inventory CRUD, SKU generation, dashboard |
