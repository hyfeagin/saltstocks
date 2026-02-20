from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional

import requests

OAUTH_ENDPOINT = "https://api.ebay.com/identity/v1/oauth2/token"


class EbayIntegrationError(Exception):
    pass


@dataclass
class EbaySettings:
    client_id: str
    client_secret: str
    environment: str
    refresh_token: str


def get_api_base(environment: str) -> str:
    env = (environment or "SANDBOX").upper()
    if env == "PRODUCTION":
        return "https://api.ebay.com"
    return "https://api.sandbox.ebay.com"


def build_iso_date_range(start_date: date, end_date: date) -> tuple[str, str]:
    start_dt = datetime.combine(start_date, time.min).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, time.max).replace(tzinfo=timezone.utc)
    return start_dt.isoformat(timespec="seconds").replace("+00:00", "Z"), end_dt.isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def refresh_access_token(settings: EbaySettings, timeout_seconds: int = 20) -> str:
    client_id = (settings.client_id or "").strip()
    client_secret = (settings.client_secret or "").strip()
    refresh_token = (settings.refresh_token or "").strip()

    if not client_id or not client_secret or not refresh_token:
        raise EbayIntegrationError(
            "Missing eBay credentials. Configure client_id, client_secret, and refresh_token first."
        )

    try:
        response = requests.post(
            OAUTH_ENDPOINT,
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
            },
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise EbayIntegrationError(f"Unable to reach eBay OAuth endpoint: {exc}") from exc

    if response.status_code >= 400:
        message = _best_error_message(response)
        raise EbayIntegrationError(f"Token refresh failed ({response.status_code}): {message}")

    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise EbayIntegrationError("Token refresh succeeded but no access token was returned.")

    return access_token


def fetch_orders(
    settings: EbaySettings,
    access_token: str,
    created_from_iso: str,
    created_to_iso: str,
    status_filter: str,
    limit: int = 200,
    max_pages: int = 25,
    timeout_seconds: int = 25,
) -> List[Dict[str, Any]]:
    base_url = get_api_base(settings.environment)
    endpoint = f"{base_url}/sell/fulfillment/v1/order"

    clauses = [f"creationdate:[{created_from_iso}..{created_to_iso}]"]
    status_value = (status_filter or "").strip().upper()
    payment_statuses = {"PAID", "NOT_PAID", "PENDING", "FAILED"}

    if status_value and status_value != "ANY":
        if status_value in payment_statuses:
            clauses.append(f"orderpaymentstatus:{{{status_value}}}")
        else:
            clauses.append(f"orderfulfillmentstatus:{{{status_value}}}")

    filter_value = ",".join(clauses)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    offset = 0
    all_orders: List[Dict[str, Any]] = []

    for _ in range(max_pages):
        params = {
            "filter": filter_value,
            "limit": limit,
            "offset": offset,
        }

        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=timeout_seconds)
        except requests.RequestException as exc:
            raise EbayIntegrationError(f"Unable to fetch eBay orders: {exc}") from exc

        if response.status_code >= 400:
            message = _best_error_message(response)
            raise EbayIntegrationError(
                f"eBay getOrders failed ({response.status_code}) for filter '{filter_value}': {message}"
            )

        payload = response.json()
        page_orders = payload.get("orders", []) or []
        all_orders.extend(page_orders)

        total = payload.get("total")
        if not page_orders:
            break

        offset += len(page_orders)
        if isinstance(total, int) and offset >= total:
            break

    return all_orders


def _best_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:300] if text else "No details returned."

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        msg = first.get("message") if isinstance(first, dict) else None
        long_msg = first.get("longMessage") if isinstance(first, dict) else None
        joined = " | ".join([m for m in [msg, long_msg] if m])
        if joined:
            return joined

    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return "No details returned."
