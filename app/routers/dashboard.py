from __future__ import annotations

import shutil
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db import get_conn, DB_PATH
from ..deps import templates, BACKUP_DIR

router = APIRouter()


# =========================
# ANCHOR: DASHBOARD_VIEW_BEGIN
# (Dashboard counts for resale inventory status)
# =========================
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = get_conn()
    resale_count = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE item_type='resale'"
    ).fetchone()["c"]
    unlisted = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.item_type='resale'
          AND COALESCE(rl.status,'unlisted') = 'unlisted'
        """
    ).fetchone()["c"]
    low_qty = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM items
        WHERE item_type='resale' AND qty_on_hand <= 0
        """
    ).fetchone()["c"]
    conn.close()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "resale_count": resale_count, "unlisted": unlisted, "low_qty": low_qty},
    )
# =========================
# ANCHOR: DASHBOARD_VIEW_END
# =========================


# =========================
# ANCHOR: BACKUP_NOW_BEGIN
# (Create a timestamped database backup)
# =========================
@router.post("/backup")
def backup_now():
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"saltstocks_{ts}.db"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, dest)
    return RedirectResponse(url="/", status_code=303)
# =========================
# ANCHOR: BACKUP_NOW_END
# =========================
