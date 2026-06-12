# SaltStocks — CLAUDE.md
> **The GUIDE.** Read after BOOT.md when you need to understand *how* the project works, not just *where* things are.

---

## What This App Is

SaltStocks is a personal business management tool for a sole-proprietor resale + handmade goods seller. It runs locally (Mac) and is being migrated to a Namecheap VPS. It handles:

- **Resale inventory** — SKU-tracked items purchased for resale (eBay, in-person)
- **Materials inventory** — raw production inputs (salts, fragrances, packaging)
- **Production runs** — recipes that convert materials into finished goods
- **Double-entry accounting** — journal entries, chart of accounts, P&L, balance sheet
- **eBay integration** — OAuth, order import, and now full sale accounting in one step

The owner is Holly Feagin. The business entity is Geekery Vault.

---

## How to Run

```bash
cd ~/Documents/saltstocks
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open http://localhost:8000. HTTPS available via `certs/localhost.pem` if needed.

**Startup sequence (automatic):**
1. `schema.sql` creates base tables if they don't exist
2. `migrate()` runs idempotently — adds missing columns, tables, seed data
3. App is ready — no manual DB setup needed after a normal pull

---

## Development Rules

1. **Migrations are additive only.** Never `DROP` or `RENAME`. Use `ensure_column()` and `if not table_exists()`.
2. **All journal writes go through `post_entry()` / `post_entries()`.** Never INSERT into `journal_entries` or `journal_lines` directly.
3. **Atomic multi-step transactions use `after_insert` callback.** See production run in `materials.py` and COGS posting in `accounting/routes.py` for the pattern.
4. **item_type controls routing.** `'resale'` items appear in resale pickers; `'material'` items appear in material pickers. The same `items` table serves both.
5. **Questionnaire steps use `shown_when` lambdas.** Never hardcode template checks in templates — put all branching logic in `questionnaire.py`.
6. **Weighted average cost.** On every purchase, recalculate `unit_cost = (old_qty × old_cost + new_qty × new_cost) / total_qty`.
7. **The scratchpad is volatile.** `.session/SCRATCH.md` tracks in-flight work within a session. Fold it into BOOT.md via `/end-session` before closing. Never commit it.

---

## Quick Commit

```bash
bash scripts/done.sh "feat: describe what changed"
```

Stages everything, commits, pushes to current branch.

---

## VPS Deploy Workflow

**Normal deploy** (code changes only): double-click `shortcuts/Deploy to Server.command`.
That runs `git pull && systemctl restart saltstocks` — no pip, fast.

**Adding a new Python dependency:**
1. Add it to `requirements.txt` locally (pinned version)
2. Commit and push
3. SSH into the server and install manually:
   ```bash
   ssh holly@162.0.222.94
   cd ~/saltstocks && source .venv/bin/activate
   pip install <package>==<version>
   ```
4. The nightly cron job (2am server time) will keep deps in sync going forward.

**Never add macOS-only packages to requirements.txt** (e.g. `pyobjc`, `pywebview`).
The server is Linux — those will break the nightly sync.

**bcrypt must stay pinned to 3.2.2.** passlib 1.7.4 is incompatible with bcrypt 4+/5+.

---

## Session Worklog Protocol

At the start of each session, read `.session/SCRATCH.md` to recover any in-flight work from the previous session.

During a session, append one line per completed task to `.session/SCRATCH.md`:
```
[2026-06-08] Added Total Paid field to materials/form.html — auto-computes unit_cost
```

At end of session, run `/end-session` to:
1. Fold worklog outcomes into the BOOT.md Session Log
2. Reset `.session/SCRATCH.md`
3. Update BOOT.md file map if new files were created
4. Propose a git commit

---

## Architecture Decisions

**Why SQLite?** Single-user local app. No concurrency needs. Zero ops overhead. Backups are a file copy.

**Why FastAPI + Jinja2 over SPA?** Simpler state management, no API/frontend split, direct DB access, easy to run locally.

**Why a questionnaire engine instead of one big form?** Progressive disclosure — only ask what's needed for the chosen transaction type. Keeps each screen focused and handles 25+ transaction templates without a 40-field monstrosity.

**Why double-entry?** Tax readiness. Owner wants a real P&L and balance sheet, not just a transaction log.

**Why `item_type` on the `items` table instead of separate tables?** Materials and resale items share most columns (name, unit, qty, cost, sku). Shared table means shared weighted-average cost logic, shared search/picker components, and fewer joins.

---

## Known Complexity Zones

| Zone | Why it's complex |
|------|-----------------|
| `accounting/routes.py` (2875 lines) | Orchestrates questionnaire state machine, inventory deductions, COGS posting, eBay fee splitting, receipt management — all in one file |
| `questionnaire.py` — `shown_when` lambdas | Each step's visibility depends on template_id + prior answers. Easy to break when adding new templates. Always run tests after changes. |
| Production run accounting | DR 1200 / CR 1200 × N with last-line rounding absorption — unusual entry that confuses "why does this balance?" readers |
| eBay sale resolve_lines | `gross = item_price + shipping_charged`; `net_deposit = gross - fees` — three amounts, two debits, one credit |

---

## Improvement Opportunities

- `accounting/routes.py` is too large — candidate for splitting into `questionnaire_routes.py`, `entry_routes.py`, `report_routes.py`
- No automated tests for materials module or eBay accounting integration yet
- VPS deployment: eBay OAuth redirect URI must point to VPS hostname — see `VPS_BACKLOG.md`
- Receipt OCR / AI parsing exists (`/entry/ai-start`) but is underused
