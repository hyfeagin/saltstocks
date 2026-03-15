from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
BACKUP_DIR = BASE_DIR / "data" / "backups"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
