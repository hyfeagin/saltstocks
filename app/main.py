from __future__ import annotations

from fastapi import FastAPI

from .db import init_db
from .migrate import migrate
from .deps import BACKUP_DIR
from .routers import dashboard, resale, config, ebay

app = FastAPI(title="Salt Stocks")

app.include_router(dashboard.router)
app.include_router(resale.router)
app.include_router(config.router)
app.include_router(ebay.router)


# =========================
# ANCHOR: STARTUP_INIT_DB_BEGIN
# (Initialize database and backup directory on startup)
# =========================
@app.on_event("startup")
def _startup():
    init_db()
    migrate()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
# =========================
# ANCHOR: STARTUP_INIT_DB_END
# =========================
