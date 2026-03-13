# SaltStocks Developer Guide

## Project Layout

| Path | Purpose |
|------|---------|
| `app/main.py` | FastAPI routes, forms, SKU logic |
| `app/db.py` | DB connection helper, `init_db()` |
| `app/schema.sql` | Base schema (tables, indexes, triggers) |
| `app/migrate.py` | Idempotent migration — adds columns, tables, seed data |
| `app/integrations/ebay.py` | eBay API client |
| `app/templates/` | Jinja2 HTML templates |
| `data/saltstocks.db` | Local SQLite database (git-ignored) |
| `data/backups/` | Timestamped DB backups (git-ignored) |
| `docs/BACKLOG.md` | Prioritized feature backlog |
| `scripts/done.sh` | One-shot commit + push helper |

---

## Running Locally

```bash
cd ~/Documents/saltstocks
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

**On startup the app automatically:**
1. Runs `schema.sql` (creates base tables if they don't exist)
2. Runs `migrate()` (adds any missing columns, tables, and seed data)

You do **not** need to run `python app/migrate.py` manually on a normal restart.

---

## Git Workflow

### Quick commit and push

```bash
bash scripts/done.sh "feat: describe what changed"
```

This stages everything, commits, and pushes to the current branch.

### Branch-based workflow (for features / schema changes)

```bash
git switch -c feat/my-feature
# make changes, test locally
bash scripts/done.sh "feat: my feature description"
# open PR on GitHub, review diff, merge
git switch main && git pull
```

**Branch naming conventions:**

| Prefix | Use for |
|--------|---------|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `chore/` | Refactors, cleanup, deps |
| `docs/` | Documentation only |

### Useful git commands

```bash
git status
git diff
git log --oneline -10
git switch main && git pull   # revert to stable state
```

---

## Migrations

Migrations run automatically on app startup — `migrate()` is called from the FastAPI `startup` event in `main.py`.

`migrate.py` is fully idempotent: it uses `column_exists` / `table_exists` checks and `IF NOT EXISTS` guards, so running it repeatedly is safe and never overwrites existing data.

### When to manually run migrate.py

Only needed if you are applying a schema change **without** restarting the app (rare):

```bash
source .venv/bin/activate
python app/migrate.py
```

### Adding a new migration

1. Add an `ensure_column()` call or `if not table_exists()` block in `migrate.py`
2. Use additive changes only (`ALTER TABLE ADD COLUMN`, new tables) — never drop or rename
3. Add seed data inside an `IF COUNT(*) = 0` guard so existing data is preserved
4. Restart the app to apply

---

## SKU Generation

- SKUs are **read-only** and auto-generated on item creation
- Format: `{COMPANY}-{CODE}-{SEQUENCE}` (e.g. `GV-FUNKO-000001`)
- `SEQUENCE` increments per `(company, code)` pair using the `sku_counters` table
- `codes` table controls valid brand/item codes (managed under `/config`)

---

## eBay Integration

- **Settings:** `/config` → eBay Settings. Stores `client_id`, `client_secret`, `environment`, and `refresh_token` per environment profile (`SANDBOX` / `PRODUCTION`) in the `ebay_credentials` table.
- **Import:** `/ebay/import` is manual pull, preview-first. Fetches orders via `getOrders`, matches by SKU, applies deductions only on explicit confirmation.
- **Idempotency:** `ebay_import_log` stores `(order_id, line_item_id)` to prevent double-deductions.
- **Deduction rule:** `new_qty = max(0, qty_on_hand - qty_sold)` — oversold rows are clamped and flagged.
- **Auth:** Refresh token is manually obtained via eBay Authorization Code Grant. This app does not host the full OAuth redirect flow.
- **SKU matching:** The SaltStocks SKU must match the eBay listing's Custom Label field.

---

## Data Safety

- `data/saltstocks.db` is **never committed to git**
- Use the **Backup** button in the UI to create a timestamped copy in `data/backups/`
- To restore: stop the app, copy the backup file to `data/saltstocks.db`, restart

---

## Troubleshooting

**`no such column` or `no such table` error**
The DB is out of sync. Restart the app — migration runs automatically on startup.

**`python: command not found`**
Virtual environment isn't active:
```bash
source .venv/bin/activate
```

**Dropdown or config list is empty**
Either the seed data didn't run (restart the app) or you need to add entries manually under `/config`.

**App won't start / import error**
Check for syntax errors:
```bash
.venv/bin/python -m py_compile app/main.py
```

**Want to revert to last stable state**
```bash
git switch main && git pull
```
The SQLite DB is not affected by branch switches.

---

## Adding a New Integration

Follow the eBay integration as a pattern:

1. Create `app/integrations/<name>.py` with an API client class and a settings dataclass
2. Add credential tables to `migrate.py` (additive)
3. Add settings UI to `/config` (see `ebay_settings` routes in `main.py`)
4. Add import route + template following the preview-first, dry-run, idempotency pattern
5. Update `docs/BACKLOG.md` to mark the item done
6. Add a changelog entry in `Changelog.md`
