"""
SaltStocks MCP server.

Exposes live inventory and accounting data to Claude via six read-only tools.
Mounted inside the FastAPI app at /mcp — no separate process needed.

Auth strategy:
  - If app_settings.mcp_base_url is set to a public HTTPS URL (e.g. when
    running on the VPS), OAuth 2.1 with PKCE is used so claude.ai's connector
    registration works out of the box.
  - If mcp_base_url is empty (local dev), a simple bearer-token middleware
    guards the endpoint using the auto-generated app_settings.mcp_bearer_token.

OAuth state (registered clients + tokens) is persisted to SQLite so it
survives server restarts.
"""
from __future__ import annotations

import time
from typing import Any

from fastmcp import FastMCP
from mcp.server.auth.provider import AccessToken as _SDKAccessToken
from mcp.server.auth.provider import RefreshToken as _SDKRefreshToken
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.mcp import tools as _tools

mcp = FastMCP(
    "SaltStocks Financial Data",
    instructions=(
        "Read-only access to SaltStocks inventory and accounting data for Geekery Vault. "
        "Use get_pnl_summary for profitability questions, get_inventory_snapshot for stock "
        "questions, get_sale_history for trend analysis, simulate_ebay_sale for pricing "
        "decisions, get_item_detail for a specific SKU, and get_recent_journal_entries "
        "to review the ledger."
    ),
)

_tools.register(mcp)


def _get_setting(key: str) -> str | None:
    """Read a single value from app_settings; returns None if missing."""
    from app.db import get_conn
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Persistent OAuth 2.1 provider
# ---------------------------------------------------------------------------

class SaltStocksOAuthProvider:
    """
    Minimal OAuth 2.1 provider backed by SQLite.

    Wraps InMemoryOAuthProvider so all protocol logic lives in FastMCP; this
    class only adds DB read/write so registered clients and issued tokens
    survive server restarts.

    Dynamic client registration is open (required by claude.ai's connector
    registration flow). Authorization is auto-approved — acceptable for a
    personal single-user app where the URL is private.
    """

    def __init__(self, base_url: str):
        from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
        from mcp.server.auth.settings import ClientRegistrationOptions
        self._inner = InMemoryOAuthProvider(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self._load_from_db()

    # Delegate attribute access to the inner provider so FastMCP can call
    # all the expected OAuthProvider interface methods.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    def _load_from_db(self) -> None:
        from app.db import get_conn
        conn = get_conn()
        try:
            # Load registered clients
            for row in conn.execute("SELECT data FROM mcp_oauth_clients").fetchall():
                client = OAuthClientInformationFull.model_validate_json(row["data"])
                if client.client_id:
                    self._inner.clients[client.client_id] = client

            now = time.time()

            # Load access tokens that haven't expired
            for row in conn.execute(
                "SELECT data FROM mcp_oauth_tokens WHERE token_type='access'"
            ).fetchall():
                token = _SDKAccessToken.model_validate_json(row["data"])
                if token.expires_at is None or token.expires_at > now:
                    self._inner.access_tokens[token.token] = token

            # Load refresh tokens that haven't expired
            for row in conn.execute(
                "SELECT data FROM mcp_oauth_tokens WHERE token_type='refresh'"
            ).fetchall():
                token = _SDKRefreshToken.model_validate_json(row["data"])
                if token.expires_at is None or token.expires_at > now:
                    self._inner.refresh_tokens[token.token] = token

        except Exception:
            # Tables don't exist yet on first run — silent; migrate() will create them
            pass
        finally:
            conn.close()

    def _save_client(self, client: OAuthClientInformationFull) -> None:
        from app.db import get_conn
        conn = get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO mcp_oauth_clients (client_id, data) VALUES (?, ?)",
                (client.client_id, client.model_dump_json()),
            )
            conn.commit()
        finally:
            conn.close()

    def _save_token(
        self,
        token: _SDKAccessToken | _SDKRefreshToken,
        token_type: str,
    ) -> None:
        from app.db import get_conn
        conn = get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO mcp_oauth_tokens (token, token_type, data) "
                "VALUES (?, ?, ?)",
                (token.token, token_type, token.model_dump_json()),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_token(self, token_str: str) -> None:
        from app.db import get_conn
        conn = get_conn()
        try:
            conn.execute("DELETE FROM mcp_oauth_tokens WHERE token = ?", (token_str,))
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # OAuthProvider interface overrides (intercept → persist → delegate)
    # ------------------------------------------------------------------

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._inner.register_client(client_info)
        self._save_client(client_info)

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: Any,
    ) -> OAuthToken:
        result: OAuthToken = await self._inner.exchange_authorization_code(
            client, authorization_code
        )
        access = self._inner.access_tokens.get(result.access_token)
        if access:
            self._save_token(access, "access")
        if result.refresh_token:
            refresh = self._inner.refresh_tokens.get(result.refresh_token)
            if refresh:
                self._save_token(refresh, "refresh")
        return result

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: Any,
        scopes: list[str],
    ) -> OAuthToken:
        old_token_str = refresh_token.token
        result: OAuthToken = await self._inner.exchange_refresh_token(
            client, refresh_token, scopes
        )
        # Remove old refresh token from DB (InMemoryOAuthProvider rotates it)
        self._delete_token(old_token_str)
        new_access = self._inner.access_tokens.get(result.access_token)
        if new_access:
            self._save_token(new_access, "access")
        if result.refresh_token:
            new_refresh = self._inner.refresh_tokens.get(result.refresh_token)
            if new_refresh:
                self._save_token(new_refresh, "refresh")
        return result

    async def revoke_token(self, token: Any) -> None:
        token_str = token.token
        await self._inner.revoke_token(token)
        self._delete_token(token_str)

    # All other methods (authorize, get_client, load_authorization_code,
    # load_access_token, load_refresh_token, verify_token …) fall through
    # to self._inner via __getattr__.


# ---------------------------------------------------------------------------
# ASGI app factory
# ---------------------------------------------------------------------------

def create_asgi_app():
    """
    Returns an ASGI app for mounting at /mcp in FastAPI.

    If app_settings.mcp_base_url is a non-empty string the server runs with
    full OAuth 2.1 (PKCE) using SaltStocksOAuthProvider — suitable for
    claude.ai connector registration.

    Otherwise a simple bearer-token middleware guards the endpoint using the
    auto-generated app_settings.mcp_bearer_token value (local-dev default).
    """
    base_url = _get_setting("mcp_base_url") or ""

    if base_url.strip():
        # OAuth 2.1 mode — set auth on the FastMCP singleton before building
        # the ASGI app so FastMCP wires up the discovery/token endpoints.
        provider = SaltStocksOAuthProvider(base_url=base_url.rstrip("/") + "/mcp")
        mcp.auth = provider  # type: ignore[assignment]
        return mcp.http_app(path="/", transport="streamable-http")

    # Bearer-token fallback (local dev / VPS without OAuth configured)
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class _BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            expected = _get_setting("mcp_bearer_token")
            if not expected:
                return JSONResponse({"error": "MCP not configured"}, status_code=503)
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:].strip() != expected:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

    inner = mcp.http_app(path="/", transport="streamable-http")
    inner.add_middleware(_BearerAuth)
    return inner
