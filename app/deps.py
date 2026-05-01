from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

from fastapi.templating import Jinja2Templates

from .db import get_conn
from .constants import RESALE_STATUSES, RESALE_CHANNELS, EBAY_ENVIRONMENTS

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
BACKUP_DIR = BASE_DIR / "data" / "backups"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Make enum constants available in every template without passing them per-route.
templates.env.globals["RESALE_STATUSES"] = RESALE_STATUSES
templates.env.globals["RESALE_CHANNELS"] = RESALE_CHANNELS
templates.env.globals["EBAY_ENVIRONMENTS"] = EBAY_ENVIRONMENTS


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency — yields an open DB connection and closes it after the request."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()
