from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from .constants import DEFAULT_CHANNEL, DEFAULT_STATUS, EBAY_ENVIRONMENTS, DEFAULT_ENVIRONMENT

# Sentinel — distinguishes "caller didn't pass this field" from None (valid NULL value).
_UNSET = object()


def clean_str(val: str, default: Optional[str] = None) -> Optional[str]:
    """Strip whitespace from val; return default if the result is empty."""
    cleaned = (val or "").strip()
    return default if not cleaned else cleaned


def safe_parse_float(val: Optional[str], default: Optional[float] = None) -> Optional[float]:
    """Parse a string as float.

    - Blank or None → returns default.
    - Valid number string → returns float.
    - Non-empty but unparseable → raises ValueError.
    """
    if val is None or (stripped := val.strip()) == "":
        return default
    try:
        return float(stripped)
    except ValueError:
        raise ValueError(f"invalid number: {val!r}")


def normalize_code(s: str, default: str = "") -> str:
    s = (s or "").strip().upper()
    s = s.replace(" ", "").replace("-", "").replace("_", "")
    return s if s else default


def get_next_sku(conn: sqlite3.Connection, company: str, code: str) -> str:
    company = normalize_code(company)
    code = normalize_code(code)
    if not company or not code:
        raise ValueError("company and code required for SKU generation")

    row = conn.execute(
        "SELECT next_seq FROM sku_counters WHERE company=? AND code=?",
        (company, code),
    ).fetchone()

    if row is None:
        next_seq = 1
        conn.execute(
            "INSERT INTO sku_counters(company, code, next_seq) VALUES(?,?,?)",
            (company, code, 2),
        )
    else:
        next_seq = int(row["next_seq"])
        conn.execute(
            "UPDATE sku_counters SET next_seq=? WHERE company=? AND code=?",
            (next_seq + 1, company, code),
        )

    return f"{company}-{code}-{next_seq:06d}"


def _empty_ebay_profile() -> Dict[str, Optional[str]]:
    return {
        "client_id": "",
        "client_secret": "",
        "refresh_token": "",
        "ru_name": "",
        "updated_at": None,
    }


def get_ebay_profile_bundle(conn: sqlite3.Connection) -> Dict[str, Any]:
    profiles = {env: _empty_ebay_profile() for env in EBAY_ENVIRONMENTS}

    try:
        active_row = conn.execute(
            "SELECT active_environment FROM ebay_state WHERE id=1"
        ).fetchone()
        active_environment = (active_row["active_environment"] if active_row else DEFAULT_ENVIRONMENT) or DEFAULT_ENVIRONMENT
        if active_environment not in EBAY_ENVIRONMENTS:
            active_environment = DEFAULT_ENVIRONMENT

        rows = conn.execute(
            "SELECT environment, client_id, client_secret, refresh_token, ru_name, updated_at FROM ebay_credentials"
        ).fetchall()
        for row in rows:
            env = (row["environment"] or "").upper()
            if env in profiles:
                profiles[env] = {
                    "client_id": row["client_id"] or "",
                    "client_secret": row["client_secret"] or "",
                    "refresh_token": row["refresh_token"] or "",
                    "ru_name": row["ru_name"] or "",
                    "updated_at": row["updated_at"],
                }
    except sqlite3.OperationalError:
        # Pre-migration fallback for older local databases.
        legacy_row = conn.execute(
            "SELECT client_id, client_secret, environment, refresh_token, updated_at FROM ebay_settings WHERE id=1"
        ).fetchone()
        active_environment = DEFAULT_ENVIRONMENT
        if legacy_row:
            legacy_env = (legacy_row["environment"] or DEFAULT_ENVIRONMENT).upper()
            if legacy_env in EBAY_ENVIRONMENTS:
                active_environment = legacy_env
            profiles[active_environment] = {
                "client_id": legacy_row["client_id"] or "",
                "client_secret": legacy_row["client_secret"] or "",
                "refresh_token": legacy_row["refresh_token"] or "",
                "updated_at": legacy_row["updated_at"],
            }

    return {
        "active_environment": active_environment,
        "profiles": profiles,
        "active_profile": profiles[active_environment],
    }


def upsert_resale_listing(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    status: Any = _UNSET,
    channel: Any = _UNSET,
    list_price: Any = _UNSET,
    url: Any = _UNSET,
) -> None:
    """Insert or update the resale_listings row for item_id.

    Fields left at _UNSET are skipped on UPDATE (existing value is preserved)
    and replaced with column defaults on INSERT.
    """
    existing = conn.execute(
        "SELECT id FROM resale_listings WHERE item_id=?", (item_id,)
    ).fetchone()

    if existing:
        set_parts: List[str] = []
        params: List[Any] = []
        if status is not _UNSET:
            set_parts.append("status=?")
            params.append(status)
        if channel is not _UNSET:
            set_parts.append("channel=?")
            params.append(channel)
        if list_price is not _UNSET:
            set_parts.append("list_price=?")
            params.append(list_price)
        if url is not _UNSET:
            set_parts.append("url=?")
            params.append(url)
        if set_parts:
            set_parts.append("updated_at=datetime('now')")
            params.append(item_id)
            conn.execute(
                f"UPDATE resale_listings SET {', '.join(set_parts)} WHERE item_id=?",
                params,
            )
    else:
        conn.execute(
            "INSERT INTO resale_listings (item_id, channel, status, list_price, url) VALUES (?, ?, ?, ?, ?)",
            (
                item_id,
                channel if channel is not _UNSET else DEFAULT_CHANNEL,
                status if status is not _UNSET else DEFAULT_STATUS,
                list_price if list_price is not _UNSET else None,
                url if url is not _UNSET else None,
            ),
        )


def fetch_resale_rows(conn: sqlite3.Connection, q: Optional[str] = None) -> list:
    """Return all resale items joined with listing data, optionally filtered by search term."""
    params: Dict[str, Any] = {}
    where = "WHERE i.item_type='resale'"
    if q:
        where += " AND (i.name LIKE :q OR i.sku LIKE :q OR i.tags LIKE :q)"
        params["q"] = f"%{q}%"
    return conn.execute(
        f"""
        SELECT
          i.*,
          COALESCE(rl.status, 'unlisted') AS status,
          COALESCE(rl.channel, 'unassigned') AS channel,
          rl.list_price,
          rl.url
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        {where}
        ORDER BY i.updated_at DESC
        """,
        params,
    ).fetchall()


def fetch_resale_item_by_id(conn: sqlite3.Connection, item_id: int) -> Any:
    """Return a single resale item joined with listing data, or None if not found."""
    return conn.execute(
        """
        SELECT
          i.*,
          COALESCE(rl.status, 'unlisted') AS status,
          COALESCE(rl.channel, 'unassigned') AS channel,
          rl.list_price,
          rl.url
        FROM items i
        LEFT JOIN resale_listings rl ON rl.item_id = i.id
        WHERE i.id = ? AND i.item_type='resale'
        """,
        (item_id,),
    ).fetchone()


def missing_ebay_credentials(settings: Dict[str, Optional[str]]) -> List[str]:
    return [
        key for key in ["client_id", "client_secret", "refresh_token"]
        if not (settings.get(key) or "").strip()
    ]


def extract_line_item_id(order_id: str, line: Dict[str, Any], line_idx: int) -> str:
    val = (
        line.get("lineItemId")
        or line.get("legacyReference", {}).get("legacyOrderLineItemId")
        or line.get("legacyReferenceId")
    )
    if val:
        return str(val)
    return f"{order_id}-line-{line_idx}"


def extract_line_item_sku(line: Dict[str, Any]) -> str:
    for key in ["sku", "sellerSku", "inventoryReferenceId", "merchantSku"]:
        value = line.get(key)
        if value:
            return str(value).strip()
    return ""


def extract_line_item_qty(line: Dict[str, Any]) -> float:
    for key in ["quantity", "lineItemQuantity", "quantityPurchased"]:
        value = line.get(key)
        if value is None:
            continue
        try:
            qty = float(value)
            return qty if qty > 0 else 0.0
        except (TypeError, ValueError):
            continue
    return 0.0
