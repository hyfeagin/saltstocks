from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import date
from typing import Any, Generator

from fastapi import Request
from fastapi.responses import HTMLResponse
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


def render(template_name: str, request: Request, **context: Any) -> HTMLResponse:
    """Shorthand for templates.TemplateResponse — injects request automatically."""
    return templates.TemplateResponse(
        template_name,
        {"request": request, "current_year": date.today().year, **context},
    )


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency — yields an open DB connection and closes it after the request."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()
