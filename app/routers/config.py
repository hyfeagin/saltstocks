from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db import get_conn
from ..deps import templates
from ..utils import normalize_code, get_ebay_profile_bundle, missing_ebay_credentials

router = APIRouter()


# =========================
# ANCHOR: CONFIG_CODES_CRUD_BEGIN
# (List/create/update/delete config codes)
# =========================
@router.get("/config", response_class=HTMLResponse)
def config_home(request: Request, saved_ebay: int = 0, oauth: int = 0):
    conn = get_conn()
    codes = conn.execute(
        "SELECT * FROM codes ORDER BY kind ASC, code ASC"
    ).fetchall()
    categories = conn.execute(
        "SELECT * FROM categories ORDER BY name ASC"
    ).fetchall()
    ebay_bundle = get_ebay_profile_bundle(conn)
    conn.close()
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
):
    code_n = normalize_code(code)
    label_n = (label or "").strip()
    kind_n = (kind or "item").strip()

    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO codes(code, label, kind, is_active) VALUES (?,?,?,1)",
            (code_n, label_n, kind_n),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/codes/{code_id}/update")
def config_code_update(
    code_id: int,
    label: str = Form(...),
    kind: str = Form("item"),
    is_active: int = Form(1),
):
    conn = get_conn()
    with conn:
        conn.execute(
            """
            UPDATE codes
            SET label=?, kind=?, is_active=?, updated_at=datetime('now')
            WHERE id=?
            """,
            ((label or "").strip(), (kind or "item").strip(), 1 if int(is_active) == 1 else 0, code_id),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/codes/{code_id}/delete")
def config_code_delete(code_id: int):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM codes WHERE id=?", (code_id,))
    conn.close()
    return RedirectResponse(url="/config", status_code=303)
# =========================
# ANCHOR: CONFIG_CODES_CRUD_END
# =========================


# =========================
# ANCHOR: CONFIG_CATEGORIES_CRUD_BEGIN
# (List/create/update/toggle config categories)
# =========================
@router.post("/config/categories/new")
def config_category_create(name: str = Form(...)):
    name_n = (name or "").strip()
    if not name_n:
        return RedirectResponse(url="/config", status_code=303)

    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO categories(name, is_active) VALUES (?, 1)",
            (name_n,),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/categories/{category_id}/update")
def config_category_update(category_id: int, name: str = Form(...)):
    name_n = (name or "").strip()
    if not name_n:
        return RedirectResponse(url="/config", status_code=303)

    conn = get_conn()
    with conn:
        conn.execute(
            """
            UPDATE categories
            SET name=?, updated_at=datetime('now')
            WHERE id=?
            """,
            (name_n, category_id),
        )
    conn.close()
    return RedirectResponse(url="/config", status_code=303)


@router.post("/config/categories/{category_id}/toggle")
def config_category_toggle(category_id: int):
    conn = get_conn()
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
    conn.close()
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
    environment: str = Form("SANDBOX"),
    refresh_token: str = Form(""),
    ru_name: str = Form(""),
):
    env_value = (environment or "SANDBOX").strip().upper()
    if env_value not in {"PRODUCTION", "SANDBOX"}:
        env_value = "SANDBOX"

    conn = get_conn()
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
                (client_id or "").strip(),
                (client_secret or "").strip(),
                (refresh_token or "").strip(),
                (ru_name or "").strip(),
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
    conn.close()
    return RedirectResponse(url="/config?saved_ebay=1#ebay-settings", status_code=303)
# =========================
# ANCHOR: EBAY_SETTINGS_END
# =========================
