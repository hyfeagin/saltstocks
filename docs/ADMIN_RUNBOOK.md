# SaltStocks Admin Runbook (Keep It Running)

This doc is for anyone who needs to run or restore SaltStocks without being the original developer.

## Quick start (Mac)
Open Terminal:

```bash
cd ~/Documents/saltstocks
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open in browser:
http://127.0.0.1:8000
Stop server:
Ctrl + C
If the server won’t start
1) “command not found: uvicorn”
Activate venv first:

```bash
cd ~/Documents/saltstocks
source .venv/bin/activate
pip install -r requirements.txt
```

Then run uvicorn again.

2) “address already in use” / port 8000 busy
Either stop the other server, or run on a different port:

```
uvicorn app.main:app --reload --port 8001
```

Then open:
http://127.0.0.1:8001
3) Python errors after code changes
A bad edit can break startup. Use git to revert to last working state:

```
cd ~/Documents/saltstocks
git status
git restore .
```

Or revert to a previous commit (developer assistance may be needed).
Backups and Restore
Where data lives
Primary database:
data/saltstocks.db

Backups:
data/backups/
Restore from a backup
Stop server (Ctrl + C)
Make a safety copy of the current DB (optional):
copy data/saltstocks.db somewhere safe
Choose a backup file from data/backups/
Replace the main DB with the backup:
rename/copy the backup file to data/saltstocks.db
Start server again

What not to commit to GitHub
These should stay local:
data/saltstocks.db
data/backups/
.venv/
They are ignored by .gitignore.
Emergency “it’s all broken” checklist
Restore last known good database backup
Revert code changes with git restore
Start server
Confirm dashboard loads


