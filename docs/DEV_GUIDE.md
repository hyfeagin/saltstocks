
# SaltStocks Developer Guide

## Project layout
- `app/main.py` — FastAPI routes, forms, SKU logic
- `app/db.py` — DB helpers / connection
- `app/schema.sql` — schema for initial DB creation (and reference)
- `app/migrate.py` — safe migration script for adding new columns/tables
- `app/templates/` — HTML templates
- `data/saltstocks.db` — local SQLite database (ignored by git)
- `data/backups/` — timestamped backups (ignored by git)

## Local dev run
```bash
cd ~/Documents/Saltstocks
source .venv/bin/activate
uvicorn app.main:app --reload
```

Git workflow (recommended)
Make changes locally
Test locally
Commit a meaningful checkpoint
Push to GitHub
Useful commands:

```
git status
git diff
git add .
git commit -m "Describe change"
git push
```

Branch workflow for bigger features:

```
git checkout -b feature/<name>
git push -u origin feature/<name>
```

SKU generation rules (current)
SKU is read-only.
SKU is created on item creation if blank/null.
Format:
{COMPANY}-{CODE}-{SEQUENCE}
SEQUENCE increments per (COMPANY + CODE) using sku_counters.
sku_counters
Keyed by:
company (GV/UM)
code (FUNKO/LEGO/NECA/etc.)
Tracks:
next_seq integer
Data safety rules (current)
Database file is not committed to git.
Backups are created via UI and stored locally.
Migrations
If schema changes are needed:
Prefer additive changes (ALTER TABLE ADD COLUMN)
Use app/migrate.py for one-time migrations
Avoid destructive migrations unless you have a clear backup/restore plan
Run migration:

```
source .venv/bin/activate
python app/migrate.py
```

Future expansion notes (Umivera)
Do not implement Umivera rules in resale flows yet. Build as separate item types / tables:
materials
recipes
batches
COGS calculations
Keep resale and production logic separated to avoid brittle coupling.



# Development Guide (DEV_GUIDE)

## Standard Git + Codex Patch Workflow (SOP)

This project uses a **branch-based workflow** for any non-trivial change
(features, schema changes, bulk edits, imports, UI updates, etc.).

**Important mental model**

* The app **always runs from local files** in `~/Documents/Saltstocks`
* GitHub is **source control + backup**, not the runtime
* Nothing changes locally unless **you pull or apply it**

---

## Two Supported Workflows

### Option A — **Local-first (Recommended / Safest)**

**What this is:**
You apply Codex’s patch directly to your local files, test locally, then commit and push.

**What it accomplishes:**

* Safest possible flow
* You *never* merge untested code
* Easy rollback via `git switch main && git pull`

**Use this when:**

* Schema changes
* Migrations
* Anything risky or state-changing
* You want maximum control

---

### Option B — **PR-first (Faster, but requires discipline)**

**What this is:**
Codex creates a branch + Pull Request first, then you pull that branch locally to test.

**What it accomplishes:**

* Faster collaboration
* Cleaner GitHub history
* Better when Codex is doing large multi-file changes

**Use this when:**

* You trust the change shape
* You still plan to test locally before merging
* You want Codex to manage diffs for you

---

---

## OPTION A — Manual Codex Patch (Local-First)

<details>
<summary><strong>1️⃣ Start from a clean <code>main</code></strong></summary>

Ensure you are on the latest stable code.

```bash
cd ~/Documents/Saltstocks
git switch main
git pull
git status
```

You should see:

> working tree clean

If not, commit or restore changes before continuing.

</details>

---

<details>
<summary><strong>2️⃣ Create a feature branch</strong></summary>

Create a new branch for the change.

```bash
git switch -c docs/anchor-toc
```

**Branch naming conventions**

* `docs/*` — documentation only
* `feat/*` — new features
* `fix/*` — bug fixes
* `chore/*` — refactors / cleanup

</details>

---

<details>
<summary><strong>3️⃣ Apply the Codex patch locally</strong></summary>

In Codex:

* Select **Unified diff**
* Click **Copy patch** (or **Copy git apply**)

In Terminal (clipboard → patch file):

```bash
pbpaste > codex.patch
git apply --check codex.patch
git apply codex.patch
```

If `git apply --check` produces **no output**, the patch is safe.

</details>

---

<details>
<summary><strong>4️⃣ Sanity checks</strong></summary>

Verify nothing is syntactically broken.

```bash
python -m py_compile app/main.py
git diff
```

Review the diff before running the app.

</details>

---

<details>
<summary><strong>5️⃣ Quick smoke test</strong></summary>

Run the app locally.

```bash
uvicorn app.main:app --reload
```

* Load the UI
* Click through normal flows for ~30 seconds
* Stop server with `Ctrl + C`

</details>

---

<details>
<summary><strong>6️⃣ Commit and push</strong></summary>

```bash
git add .
git commit -m "docs: add main.py anchors + TOC"
git push -u origin docs/anchor-toc
```

At this point:

* ✅ Tested locally
* ✅ Backed up on GitHub
* ✅ Still isolated from `main`

</details>

---

<details>
<summary><strong>7️⃣ Merge via GitHub</strong></summary>

* Open the Pull Request
* Review the diff
* Click **Merge**
* Optionally delete the branch

After merging:

```bash
git switch main
git pull
```

Your local app now reflects the merged change.

</details>

---

---

## OPTION B — PR-First (Codex-Generated Branch)

<details>
<summary><strong>1️⃣ Let Codex create the PR</strong></summary>

In Codex:

* Click **Create PR**
* Codex creates a branch like:

```
codex/add-category-management-to/config
```

No local files are changed yet.

</details>

---

<details>
<summary><strong>2️⃣ Pull the Codex branch locally</strong></summary>

Switch to the branch and pull it down.

```bash
git fetch
git switch codex/add-category-management-to/config
git pull
```

Your local files now reflect the Codex changes.

</details>

---

<details>
<summary><strong>3️⃣ Run migrations (if applicable)</strong></summary>

Always run migrations after schema changes.

```bash
source .venv/bin/activate
python app/migrate.py
```

Restart the app after.

</details>

---

<details>
<summary><strong>4️⃣ Test locally</strong></summary>

```bash
uvicorn app.main:app --reload
```

If something is wrong:

```bash
git switch main
git pull
```

Your local app instantly reverts to stable state.

</details>

---

<details>
<summary><strong>5️⃣ Merge when satisfied</strong></summary>

On GitHub:

* Merge the PR into the target branch (e.g. `feat/*`)
* Then merge that branch into `main` when ready

Locally:

```bash
git switch main
git pull
```

</details>

---

## Important Notes (Read This Once)

* The app **always runs from local files**
* `git commit` = local snapshot
* `git push` = GitHub backup
* `git pull` = update local code from GitHub
* Branches are like **library books** — you check one out at a time
* Switching branches never deletes data (SQLite DB is shared)

If a change exists locally, the app sees it immediately.

---

## Migration SOP (Database Changes)

Use this any time a change involves **SQLite tables/columns**, “schema”, “migration”, “seed”, or anything that touches `app/schema.sql`, `app/migrate.py`, or database logic.

### Why migrations matter

Your app code can be correct, but if the **database schema** isn’t updated locally, the UI will look “broken” (missing tables, missing dropdown data, errors, empty lists).
Migrations keep your local `data/saltstocks.db` in sync with the code.

---

### When to run migrations

Run the migration SOP **immediately after** you pull/apply changes that include any of the following:

* New tables (ex: `categories`)
* New columns (ex: add `category` field)
* Any changes to `app/schema.sql`
* Any changes to `app/migrate.py`
* Codex mentions “seed defaults”
* You see errors like:

  * `no such table: ...`
  * `no such column: ...`
  * dropdown/config list is empty when it shouldn’t be

---

## Migration SOP Steps

<details>
<summary><strong>1️⃣ Stop the server if it’s running</strong></summary>

If you have `uvicorn` running, stop it first:

* In Terminal: `Ctrl + C`

This prevents “half-updated” behavior while the DB changes.

</details>

---

<details>
<summary><strong>2️⃣ Go to the project folder</strong></summary>

```bash
cd ~/Documents/Saltstocks
```

</details>

---

<details>
<summary><strong>3️⃣ Activate the virtual environment</strong></summary>

This ensures you’re using the correct Python (and prevents `python: command not found` issues).

```bash
source .venv/bin/activate
```

You should see `(.venv)` in your Terminal prompt after this.

</details>

---

<details>
<summary><strong>4️⃣ Run the migration script</strong></summary>

```bash
python app/migrate.py
```

If it prints any messages, read them — they often confirm what changed (created table, seeded defaults, etc.).

</details>

---

<details>
<summary><strong>5️⃣ Restart the app</strong></summary>

```bash
uvicorn app.main:app --reload
```

</details>

---

<details>
<summary><strong>6️⃣ Verify the change worked</strong></summary>

Do the smallest possible “proof test” in the UI:

* If you added a config list: open `/config` and confirm rows exist
* If you added a dropdown: open the create/edit form and confirm choices exist
* If you added a new table: confirm you can add one row without errors

</details>

---

## Quick Troubleshooting

<details>
<summary><strong>If Terminal says: <code>-bash: python: command not found</code></strong></summary>

You forgot to activate the venv, or your system doesn’t have `python` aliased.

Do:

```bash
cd ~/Documents/Saltstocks
source .venv/bin/activate
python app/migrate.py
```

If `python` still fails, try:

```bash
python3 app/migrate.py
```

</details>

---

<details>
<summary><strong>If the table exists but seed data is missing</strong></summary>

This usually means one of these is true:

* The seed logic only runs when the table is empty
* You added a test row before seeding
* The seed list wasn’t included in the migration

Your options:

* Manually add the defaults in `/config`, OR
* Ask Codex to change seeding to `INSERT OR IGNORE` so it backfills missing rows safely

</details>

---

## One-Line Reminder (Put this near the top of DEV_GUIDE)

If you pull/apply schema changes, do this before testing:

```bash
source .venv/bin/activate && python app/migrate.py
```

---

😂 Correct. Future Holly absolutely will forget.
So we leave **breadcrumbs for her**.

Here’s a clean, copy-pasteable **Migration Checklist** you can use in **PR descriptions**, **commit messages**, or your **DEV_GUIDE**.

---

## Migration Checklist (PR / Feature Guardrail)

**Required for any change touching schema, tables, or seed data**

☐ I pulled the latest branch locally
☐ I activated the virtual environment
☐ I ran the migration script
☐ I restarted the server
☐ I verified the UI reflects the schema change

### Commands run

```bash
cd ~/Documents/Saltstocks
source .venv/bin/activate
python app/migrate.py
uvicorn app.main:app --reload
```

### Verification performed

* ☐ Config page loads without errors
* ☐ New tables/fields appear as expected
* ☐ Dropdowns/config lists are populated
* ☐ Able to create/edit at least one item successfully

---

## Optional: PR Description Template (Highly Recommended)

````md
### What changed
- Added/updated database schema (tables/columns/seed data)

### Migration required
Yes

### Migration run locally
```bash
source .venv/bin/activate
python app/migrate.py
````

### UI verification

* [ ] Config page loads
* [ ] New fields visible
* [ ] No runtime errors



## Example of a Prompt to provide to Codex
You are working in my repo. Implement this feature: [FEATURE DESCRIPTION].

CONTEXT
- This is a local FastAPI + SQLite app.
- main.py contains anchor markers like:
  ANCHOR: RESALE_CREATE_DB_WRITE_BEGIN/END
  ANCHOR: RESALE_UPDATE_DB_WRITE_BEGIN/END
  ANCHOR: RESALE_BULK_UPDATE_BEGIN/END
  ANCHOR: RESALE_EXPORT_CSV_BEGIN/END
  ANCHOR: RESALE_IMPORT_CSV_BEGIN/END
  (use the most relevant anchors)

REQUIREMENTS / ACCEPTANCE CRITERIA
- [bullet list of what must work]
- [any “must NOT change” rules]

IMPLEMENTATION CONSTRAINTS (VERY IMPORTANT)
- Only modify code inside the relevant ANCHOR BEGIN/END blocks unless absolutely necessary.
- Do not reformat unrelated code.
- Do not rename routes, functions, or variables unless required.
- Preserve existing behavior unless explicitly stated.
- Prefer additive changes and safe migrations.
- If a schema change is required, update migrate.py (additive) and note it.

OUTPUT FORMAT
- Provide a unified diff/patch for the exact files changed.
- After the diff, include:
  1) a short explanation of what changed and why
  2) a manual test checklist (click steps + expected results)

