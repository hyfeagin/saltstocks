from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_session_secret
from .db import init_db
from .migrate import migrate
from .deps import BACKUP_DIR
from .routers import dashboard, resale, config, ebay, materials
from .routers import auth as auth_router
from .accounting import routes as accounting_routes
from .mcp.server import create_asgi_app as _create_mcp_app

STATIC_DIR = Path(__file__).resolve().parent / "static"

_mcp_app = _create_mcp_app()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    migrate()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    async with _mcp_app.lifespan(app):
        yield


app = FastAPI(title="Salt Stocks", lifespan=_lifespan)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/mcp", _mcp_app)

app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(resale.router)
app.include_router(config.router)
app.include_router(ebay.router)
app.include_router(materials.router)
app.include_router(accounting_routes.router, prefix="/accounting")

_PUBLIC_PATHS = {"/login", "/setup"}

_SCANNER_PREFIXES = (
    "/.env", "/.git", "/.htaccess", "/.htpasswd", "/.DS_Store",
    "/wp-", "/wordpress", "/phpmyadmin", "/adminer", "/xmlrpc",
    "/cgi-bin", "/shell", "/config.php", "/admin.php",
)


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


class _ScannerBlockMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.lower()
        if any(path.startswith(p) for p in _SCANNER_PREFIXES):
            return Response(status_code=404)
        return await call_next(request)


@app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def root_oauth_metadata():
    """
    RFC 8414 discovery document at the root level.
    claude.ai checks this path before /mcp/.well-known/... so we proxy the
    same metadata that FastMCP serves from the mounted MCP sub-app.
    """
    from app.db import get_conn
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='mcp_base_url'"
        ).fetchone()
        base_url = row["value"].rstrip("/") if row and row["value"] else ""
    finally:
        conn.close()

    if not base_url:
        return JSONResponse({"error": "OAuth not configured"}, status_code=404)

    issuer = f"{base_url}/mcp"
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
        ],
        "code_challenge_methods_supported": ["S256"],
    }


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # /mcp and /.well-known/ handle their own auth
        if (
            path.startswith("/static")
            or path.startswith("/mcp")
            or path.startswith("/.well-known/")
            or path in _PUBLIC_PATHS
        ):
            return await call_next(request)
        if not request.session.get("authenticated"):
            next_url = path
            if request.url.query:
                next_url += "?" + request.url.query
            return RedirectResponse(f"/login?next={next_url}", status_code=303)
        return await call_next(request)


# Middleware is applied in reverse registration order (last added = outermost).
# SessionMiddleware must be outermost so the session is available to _AuthMiddleware.
# _ScannerBlockMiddleware is innermost so it short-circuits before session overhead.
app.add_middleware(_ScannerBlockMiddleware)
app.add_middleware(_AuthMiddleware)
_https_only = os.getenv("SALTSTOCKS_HTTPS_ONLY", "false").lower() == "true"
app.add_middleware(SessionMiddleware, secret_key=get_session_secret(), https_only=_https_only)


