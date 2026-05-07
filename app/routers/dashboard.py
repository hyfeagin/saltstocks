from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..accounting.routes import _owner_reimbursement_balance
from ..db import DB_PATH
from ..deps import render, BACKUP_DIR, BASE_DIR, get_db

router = APIRouter()


# =========================
# ANCHOR: DASHBOARD_VIEW_BEGIN
# (Dashboard counts for resale inventory status)
# =========================
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn: sqlite3.Connection = Depends(get_db)):
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
    owner_reimbursement_balance = _owner_reimbursement_balance(conn)
    return render(
        "dashboard.html",
        request,
        resale_count=resale_count,
        unlisted=unlisted,
        low_qty=low_qty,
        owner_reimbursement_balance=owner_reimbursement_balance,
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
    receipts_src = BASE_DIR / "data" / "receipts"
    if receipts_src.exists():
        shutil.copytree(receipts_src, BACKUP_DIR / f"receipts_{ts}")
    return RedirectResponse(url="/", status_code=303)
# =========================
# ANCHOR: BACKUP_NOW_END
# =========================
