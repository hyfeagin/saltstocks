from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..constants import EBAY_ENVIRONMENTS, DEFAULT_ENVIRONMENT
from ..deps import templates, get_db
from ..utils import clean_str, normalize_code, get_ebay_profile_bundle, missing_ebay_credentials

router = APIRouter()


# =========================
# ANCHOR: CONFIG_CODES_CRUD_BEGIN
# (List/create/update/delete config codes)
# =========================
@router.get("/config", response_class=HTMLResponse)
def config_home(request: Request, saved_ebay: int = 0, oauth: int = 0, conn: sqlite3.Connection = Depends(get_db)):
    codes = conn.execute(
        "SELECT * FROM codes ORDER BY kind ASC, code ASC"
    ).fetchall()
    categories = conn.execute(
        "SELECT * FROM categories ORDER BY name ASC"
    ).fetchall()
    ebay_bundle = get_ebay_profile_bundle(conn)
    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "codes": codes,
            "categories": categories,
            "ebay_active_environment": ebay_bundle["active_environment"],
            "ebay_profiles": ebay_bundle["profiles"],
            "ebay_settings": ebay_bundle["active_profile"],
            "ebay_missing": missing_ebay_credentials(ebay_bundle["active_profile"]),
            "saved_ebay": saved_ebay == 1,
            "oauth_success": oauth == 1,
        },
    )


@router.post("/config/codes/new")
def config_code_create(
    code: str = Form(...),
    label: str = Form(...),
    kind: str = Form("item"),
    conn: sqlite3.Connection = Depends(get_db),
):
    code_n = normalize_code(code)
    label_n = clean_str(label, "")
    kind_n = clean_str(kind, "item")

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO codes(code, label, kind, is_active) VALUES (?,?,?,1)",
            (code_n, label_n, kind_n),
        )
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/codes/{code_id}/update")
def config_code_update(
    code_id: int,
    label: str = Form(...),
    kind: str = Form("item"),
    is_active: int = Form(1),
    conn: sqlite3.Connection = Depends(get_db),
):
    with conn:
        conn.execute(
            """
            UPDATE codes
            SET label=?, kind=?, is_active=?, updated_at=datetime('now')
            WHERE id=?
            """,
            (clean_str(label, ""), clean_str(kind, "item"), 1 if int(is_active) == 1 else 0, code_id),
        )
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/codes/{code_id}/delete")
def config_code_delete(code_id: int, conn: sqlite3.Connection = Depends(get_db)):
    with conn:
        conn.execute("DELETE FROM codes WHERE id=?", (code_id,))
    return RedirectResponse(url="/config", status_code=303)
# =========================
# ANCHOR: CONFIG_CODES_CRUD_END
# =========================


# =========================
# ANCHOR: CONFIG_CATEGORIES_CRUD_BEGIN
# (List/create/update/toggle config categories)
# =========================
@router.post("/config/categories/new")
def config_category_create(name: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    name_n = clean_str(name, "")
    if not name_n:
        return RedirectResponse(url="/config", status_code=303)

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO categories(name, is_active) VALUES (?, 1)",
            (name_n,),
        )
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/categories/{category_id}/update")
def config_category_update(category_id: int, name: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    name_n = clean_str(name, "")
    if not name_n:
        return RedirectResponse(url="/config", status_code=303)

    with conn:
        conn.execute(
            """
            UPDATE categories
            SET name=?, updated_at=datetime('now')
            WHERE id=?
            """,
            (name_n, category_id),
        )
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/categories/{category_id}/toggle")
def config_category_toggle(category_id: int, conn: sqlite3.Connection = Depends(get_db)):
    with conn:
        conn.execute(
            """
            UPDATE categories
            SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END,
                updated_at=datetime('now')
            WHERE id=?
            """,
            (category_id,),
        )
    return RedirectResponse(url="/config", status_code=303)
# =========================
# ANCHOR: CONFIG_CATEGORIES_CRUD_END
# =========================


# =========================
# ANCHOR: EBAY_SETTINGS_BEGIN
# (Configure local eBay OAuth credentials)
# =========================
@router.get("/settings/ebay", response_class=HTMLResponse)
def ebay_settings_page():
    return RedirectResponse(url="/config#ebay-settings", status_code=303)


@router.post("/settings/ebay")
def ebay_settings_save(
    client_id: str = Form(""),
    client_secret: str = Form(""),
    environment: str = Form(DEFAULT_ENVIRONMENT),
    refresh_token: str = Form(""),
    ru_name: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    env_value = (environment or DEFAULT_ENVIRONMENT).strip().upper()
    if env_value not in EBAY_ENVIRONMENTS:
        env_value = DEFAULT_ENVIRONMENT

    with conn:
        conn.execute(
            """
            INSERT INTO ebay_credentials (environment, client_id, client_secret, refresh_token, ru_name, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(environment) DO UPDATE SET
              client_id=excluded.client_id,
              client_secret=excluded.client_secret,
              refresh_token=excluded.refresh_token,
              ru_name=excluded.ru_name,
              updated_at=datetime('now')
            """,
            (
                env_value,
                clean_str(client_id, ""),
                clean_str(client_secret, ""),
                clean_str(refresh_token, ""),
                clean_str(ru_name, ""),
            ),
        )
        conn.execute(
            """
            INSERT INTO ebay_state (id, active_environment, updated_at)
            VALUES (1, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
              active_environment=excluded.active_environment,
              updated_at=datetime('now')
            """,
            (env_value,),
        )
    return RedirectResponse(url="/config?saved_ebay=1#ebay-settings", status_code=303)
# =========================
# ANCHOR: EBAY_SETTINGS_END
# =========================
