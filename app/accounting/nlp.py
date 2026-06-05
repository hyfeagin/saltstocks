"""
Natural-language transaction parsing via the Anthropic Messages API.

Usage:
    result = parse_transaction(user_text, accounts, date.today(), api_key)
    if result.error:
        ...
    # result.template_id, result.total_amount, etc.
    # result.confidence["template_id"] → float 0–1
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import requests

from app.accounting.catalog import CATALOG

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MODEL = "claude-haiku-4-5-20251001"

CONFIDENCE_THRESHOLD = 0.70


@dataclass
class NLPResult:
    """Partial TransactionAnswerSet extracted from natural language, with confidence scores."""
    template_id: Optional[str] = None
    total_amount: Optional[str] = None       # decimal string, e.g. "49.99"
    entry_date: Optional[str] = None         # ISO date string "YYYY-MM-DD"
    vendor: Optional[str] = None
    memo: Optional[str] = None
    payment_account_id: Optional[int] = None
    payment_account_hint: Optional[str] = None  # free-text hint when ID isn't certain
    funding_source: Optional[str] = None         # "business" | "personal"
    # Split purchase fields (BUY_INVENTORY / BUY_INVENTORY_PERSONAL only)
    is_split_purchase: bool = False
    inventory_amount: Optional[str] = None   # decimal string — inventory portion of total
    expense_splits: Optional[list[dict]] = None  # [{account_code, amount, memo?}]
    confidence: dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None


def _system_prompt(accounts: list[dict], today: date) -> str:
    catalog_lines = "\n".join(
        f"  {tid}: {t.name}"
        for tid, t in CATALOG.items()
        if tid != "COGS_RECOGNITION"
    )
    account_lines = "\n".join(
        "  id={} code={} name={}{}".format(
            a["id"], a["code"], a["name"],
            f" ({a['subtype']})" if a.get("subtype") else "",
        )
        for a in accounts
    )
    return f"""You are a bookkeeping assistant for a small resale business. Today is {today.isoformat()}.

Extract transaction details from the user's description and return them as JSON.

## Available transaction templates
{catalog_lines}

## Critical template-selection rules
These rules override generic guessing — apply them exactly:

PERSONAL FUNDS (personal card, personal account, out of pocket, own money):
  - Business expense paid personally → BUY_EXPENSE_PERSONAL  (no business payment account needed)
  - Inventory bought personally       → BUY_INVENTORY_PERSONAL (no business payment account needed)
  - Owner putting personal money into business → OWNER_CONTRIBUTION
  Never set payment_account_id for *_PERSONAL templates — there is no business account involved.

BUSINESS FUNDS (business card, business checking, business account):
  - Business expense     → pick the most specific expense template (BUSINESS_MEAL, TRAVEL_HOTEL, etc.)
  - Inventory purchase   → BUY_INVENTORY
  - Set payment_account_id to the matching business account id.

OWNER DRAW: owner taking money out of the business for personal use → OWNER_DRAW
REIMBURSEMENT: business paying the owner back for personal spending → REIMBURSE_OWNER

MIXED PURCHASE (inventory + expense on same receipt):
  If the description mentions BOTH resale inventory AND non-inventory items
  (e.g. bubble wrap, packing tape, office supplies, software, tools) in one transaction:
  - Set template_id to BUY_INVENTORY or BUY_INVENTORY_PERSONAL (based on funding source)
  - Set is_split_purchase: true
  - Set inventory_amount: the inventory-only portion as a decimal string
  - Set expense_splits: array of objects, one per non-inventory line:
      [{{"account_code": "<code from chart>", "amount": "<decimal string>", "memo": "<description>"}}]
    Match each expense to the closest account code in the chart of accounts above.
  - inventory_amount + sum(expense_splits[].amount) MUST equal total_amount exactly
  - Set confidence["expense_splits"] to reflect certainty in the split amounts
  If amounts are ambiguous or only one type is mentioned, set is_split_purchase: false instead.

## Chart of accounts (business payment accounts only)
{account_lines}

## Response format
Return ONLY a JSON object — no markdown fences, no commentary. Fields:
- template_id        string   one of the template IDs above (required)
- total_amount       string   decimal like "49.99" (required)
- entry_date         string   ISO date "YYYY-MM-DD" (default: today if not mentioned)
- vendor             string|null   merchant or seller name, or null
- memo               string|null   brief transaction note, or null
- payment_account_id integer|null  business account id — null for *_PERSONAL templates
- payment_account_hint string|null payment method described but not clearly matched
- funding_source     string|null   "business" or "personal"
- is_split_purchase  boolean  true only when BOTH inventory AND non-inventory items present
- inventory_amount   string|null   inventory portion as decimal string (only when is_split_purchase)
- expense_splits     array|null    non-inventory lines (only when is_split_purchase); see format above
- confidence         object   float 0.0–1.0 per field populated

Confidence guide: 0.9+ = certain, 0.7–0.9 = confident, below 0.7 = guessing.
Set payment_account_id only when ≥ 0.8 confident AND the template is not a *_PERSONAL template.
Set is_split_purchase: true only when you are ≥ 0.7 confident the amounts break down correctly."""


def parse_transaction(
    text: str,
    accounts: list[dict],
    today: date,
    api_key: str,
) -> NLPResult:
    """Call the Anthropic API and return an NLPResult.

    Never raises — errors are returned as NLPResult(error=...).
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": _MODEL,
        "max_tokens": 512,
        "system": _system_prompt(accounts, today),
        "messages": [{"role": "user", "content": text}],
    }

    try:
        resp = requests.post(_API_URL, json=payload, headers=headers, timeout=20)
    except requests.RequestException as exc:
        return NLPResult(error=f"Network error: {exc}")

    if not resp.ok:
        try:
            err_data = resp.json()
            err_msg = err_data.get("error", {}).get("message") or resp.text[:300]
        except Exception:
            err_msg = resp.text[:300]
        return NLPResult(error=f"Anthropic {resp.status_code}: {err_msg}")

    try:
        body = resp.json()
        raw = body["content"][0]["text"].strip()
        # Strip markdown code fences Claude sometimes adds despite being told not to
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()
        if not raw:
            return NLPResult(error="API returned an empty response body")
        parsed = json.loads(raw)
    except (KeyError, IndexError, json.JSONDecodeError, ValueError) as exc:
        return NLPResult(error=f"Could not parse API response: {exc}")

    confidence = parsed.get("confidence") or {}
    if not isinstance(confidence, dict):
        confidence = {}

    try:
        pay_id_raw = parsed.get("payment_account_id")
        payment_account_id: Optional[int] = int(pay_id_raw) if pay_id_raw is not None else None
    except (ValueError, TypeError):
        payment_account_id = None

    amount_raw = parsed.get("total_amount")
    total_amount = str(amount_raw) if amount_raw is not None else None

    # Split purchase fields
    is_split_purchase = bool(parsed.get("is_split_purchase", False))
    inventory_amount_raw = parsed.get("inventory_amount")
    inventory_amount = str(inventory_amount_raw) if inventory_amount_raw is not None else None

    expense_splits: Optional[list[dict]] = None
    if is_split_purchase:
        raw_splits = parsed.get("expense_splits")
        if isinstance(raw_splits, list):
            valid_splits = []
            for s in raw_splits:
                if not isinstance(s, dict):
                    continue
                code = s.get("account_code")
                amt = s.get("amount")
                if not code or amt is None:
                    continue
                valid_splits.append({
                    "account_code": str(code),
                    "amount": str(amt),
                    "memo": s.get("memo") or None,
                })
            expense_splits = valid_splits if valid_splits else None

        # Sanity check: if splits don't sum to total, clear is_split_purchase
        if expense_splits and total_amount and inventory_amount:
            try:
                from decimal import Decimal as _D
                computed = _D(inventory_amount) + sum(_D(s["amount"]) for s in expense_splits)
                if abs(computed - _D(total_amount)) > _D("0.02"):
                    is_split_purchase = False
                    inventory_amount = None
                    expense_splits = None
            except Exception:
                is_split_purchase = False
                expense_splits = None

    return NLPResult(
        template_id=parsed.get("template_id") or None,
        total_amount=total_amount,
        entry_date=parsed.get("entry_date") or None,
        vendor=parsed.get("vendor") or None,
        memo=parsed.get("memo") or None,
        payment_account_id=payment_account_id,
        payment_account_hint=parsed.get("payment_account_hint") or None,
        funding_source=parsed.get("funding_source") or None,
        is_split_purchase=is_split_purchase,
        inventory_amount=inventory_amount,
        expense_splits=expense_splits,
        confidence={k: float(v) for k, v in confidence.items() if isinstance(v, (int, float))},
    )
