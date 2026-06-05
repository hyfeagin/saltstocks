from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Literal, Optional

from app.accounting.catalog import CATALOG

# ── Constants ──────────────────────────────────────────────────────────────────

InputType = Literal[
    "multi_choice",
    "text",
    "number",
    "date",
    "account_picker",
    "inventory_picker",
    "expense_splits",
    "file_upload",
]

# Derived from catalog — templates where the user must pick a payment account.
_TEMPLATES_WITH_PAYMENT_ACCOUNT: frozenset[str] = frozenset(
    tid
    for tid, t in CATALOG.items()
    if t.debit_from_field == "payment_account_id"
    or t.credit_from_field == "payment_account_id"
)

# Templates where the user picks the expense category account.
_TEMPLATES_WITH_EXPENSE_CATEGORY: frozenset[str] = frozenset(
    tid
    for tid, t in CATALOG.items()
    if t.debit_from_field == "expense_category_account_id"
    or t.credit_from_field == "expense_category_account_id"
)

# Inventory-touching templates shown in the questionnaire.
_TEMPLATES_WITH_INVENTORY_LINK: frozenset[str] = frozenset(
    {"BUY_INVENTORY", "BUY_INVENTORY_PERSONAL"}
)

_TEMPLATES_WITH_FREIGHT_IN: frozenset[str] = frozenset(
    {"BUY_INVENTORY", "BUY_INVENTORY_PERSONAL"}
)

_TEMPLATES_WITH_SALES_TAX: frozenset[str] = frozenset({"SELL_INVENTORY_CASH"})

# eBay-specific detail steps.
_TEMPLATES_WITH_EBAY_FIELDS: frozenset[str] = frozenset({"SELL_INVENTORY_EBAY"})

# Templates that deduct sold inventory (all sale templates).
_TEMPLATES_SELL_INVENTORY: frozenset[str] = frozenset(
    {"SELL_INVENTORY_CASH", "SELL_INVENTORY_EBAY"}
)

# Funding-source derivation (used in build_answer_set).
_FUNDING_SOURCE: dict[str, str] = {
    "BUY_INVENTORY_PERSONAL": "personal",
    "BUY_EXPENSE_PERSONAL": "personal",
    "BUY_INVENTORY": "business",
}

# COGS_RECOGNITION is posted automatically — never shown in the questionnaire.
_QUESTIONNAIRE_TEMPLATES: list[tuple[str, str]] = [
    (tid, t.name)
    for tid, t in sorted(CATALOG.items())
    if tid != "COGS_RECOGNITION"
]


# ── Step ───────────────────────────────────────────────────────────────────────

@dataclass
class Step:
    """One question in a questionnaire flow."""

    id: str
    question: str
    input_type: InputType
    maps_to: str                                    # field name on TransactionAnswerSet
    options: list[tuple[str, str]] = field(default_factory=list)  # (value, label) for multi_choice
    shown_when: Optional[Callable[[dict], bool]] = None  # None = always shown
    optional: bool = False
    default: Any = None
    help_text: Optional[str] = None
    account_filter: Optional[str] = None           # comma-sep subtypes for account_picker UI


# ── Global flow ────────────────────────────────────────────────────────────────
#
# A single ordered list of all possible steps.  Each session walks through it,
# evaluating shown_when(session.answers) to skip irrelevant steps.
# The answers dict drives all branching — no separate per-template flows needed.

def _a(answers: dict, key: str) -> Any:
    """Convenience: read a key from partial answers, returning None if absent."""
    return answers.get(key)


GLOBAL_FLOW: list[Step] = [

    # ── 1. Transaction type ──────────────────────────────────────────────────
    Step(
        id="template_id",
        question="What kind of transaction is this?",
        input_type="multi_choice",
        maps_to="template_id",
        options=_QUESTIONNAIRE_TEMPLATES,
    ),

    # ── 2. Reimbursement amount (REIMBURSE_OWNER only) ─────────────────────
    Step(
        id="total_amount_reimburse_owner",
        question="How much should the business reimburse you?",
        input_type="number",
        maps_to="total_amount",
        shown_when=lambda a: _a(a, "template_id") == "REIMBURSE_OWNER",
    ),

    # ── 3. Expense category (BUY_EXPENSE_PERSONAL only) ─────────────────────
    Step(
        id="expense_category_account_id_personal",
        question="What kind of expense was this?",
        input_type="account_picker",
        maps_to="expense_category_account_id",
        account_filter="expense",
        help_text="Pick the expense category that best describes this purchase.",
        shown_when=lambda a: _a(a, "template_id") == "BUY_EXPENSE_PERSONAL",
    ),

    # ── 4. Sale inventory item (cash or eBay sale) ──────────────────────────
    Step(
        id="inventory_link_sale",
        question="Which inventory item did you sell?",
        input_type="inventory_picker",
        maps_to="inventory_link",
        help_text="Choose the SKU sold and how many units were included in this sale.",
        shown_when=lambda a: _a(a, "template_id") in ("SELL_INVENTORY_CASH", "SELL_INVENTORY_EBAY"),
    ),

    # ── 4a. eBay item sale price ─────────────────────────────────────────────
    Step(
        id="ebay_total_amount",
        question="What was the item sale price? (not including shipping)",
        input_type="number",
        maps_to="total_amount",
        help_text="Enter the price the buyer paid for the item only.",
        shown_when=lambda a: _a(a, "template_id") == "SELL_INVENTORY_EBAY",
    ),

    # ── 4b. eBay shipping charged to buyer ───────────────────────────────────
    Step(
        id="ebay_shipping_charged",
        question="How much did the buyer pay for shipping? (enter 0 for free shipping)",
        input_type="number",
        maps_to="ebay_shipping_charged",
        optional=True,
        default=Decimal("0"),
        help_text="This is added to revenue. Enter 0 if you offered free shipping.",
        shown_when=lambda a: _a(a, "template_id") == "SELL_INVENTORY_EBAY",
    ),

    # ── 4c. eBay fees ────────────────────────────────────────────────────────
    Step(
        id="ebay_fees_amount",
        question="What were the total eBay fees for this sale?",
        input_type="number",
        maps_to="ebay_fees_amount",
        optional=True,
        default=Decimal("0"),
        help_text="Include final value fee and any other fees eBay charged. Posts to eBay Fees expense.",
        shown_when=lambda a: _a(a, "template_id") == "SELL_INVENTORY_EBAY",
    ),

    # ── 5. Total amount ──────────────────────────────────────────────────────
    # Skipped for BUY_INVENTORY / BUY_INVENTORY_PERSONAL — computed from line items.
    # Skipped for SELL_INVENTORY_EBAY — captured as ebay_total_amount above.
    Step(
        id="total_amount",
        question="What was the total amount?",
        input_type="number",
        maps_to="total_amount",
        help_text="Enter the grand total including any sales tax you collected or paid.",
        shown_when=lambda a: (
            _a(a, "template_id") != "REIMBURSE_OWNER"
            and _a(a, "template_id") not in _TEMPLATES_WITH_INVENTORY_LINK
            and _a(a, "template_id") != "SELL_INVENTORY_EBAY"
        ),
    ),

    # ── 6. Reimbursement source account (REIMBURSE_OWNER only) ──────────────
    Step(
        id="payment_account_id_reimburse_owner",
        question="Which bank account should reimburse you?",
        input_type="account_picker",
        maps_to="payment_account_id",
        account_filter="bank",
        shown_when=lambda a: _a(a, "template_id") == "REIMBURSE_OWNER",
    ),

    # ── 7. Date ──────────────────────────────────────────────────────────────
    Step(
        id="entry_date",
        question="When did this happen?",
        input_type="date",
        maps_to="entry_date",
        default="today",
    ),

    # ── 8. Vendor ────────────────────────────────────────────────────────────
    Step(
        id="vendor",
        question="Who did you buy from / sell to? (Optional)",
        input_type="text",
        maps_to="vendor",
        optional=True,
        shown_when=lambda a: _a(a, "template_id") not in (
            "OWNER_CONTRIBUTION", "OWNER_DRAW", "COGS_RECOGNITION", "REIMBURSE_OWNER"
        ),
    ),

    # ── 9. Payment account (business-funded templates) ──────────────────────
    Step(
        id="payment_account_id",
        question="Which account did you pay from?",
        input_type="account_picker",
        maps_to="payment_account_id",
        account_filter="bank,credit_card,cash",
        shown_when=lambda a: (
            _a(a, "template_id") in _TEMPLATES_WITH_PAYMENT_ACCOUNT
            and _a(a, "template_id") != "REIMBURSE_OWNER"
        ),
    ),

    # ── 10. Expense category (remaining expense-category templates) ──────────
    Step(
        id="expense_category_account_id",
        question="What kind of expense was this?",
        input_type="account_picker",
        maps_to="expense_category_account_id",
        account_filter="expense",
        help_text="Pick the expense category that best describes this purchase.",
        shown_when=lambda a: (
            _a(a, "template_id") in _TEMPLATES_WITH_EXPENSE_CATEGORY
            and _a(a, "template_id") != "BUY_EXPENSE_PERSONAL"
        ),
    ),

    # ── 11. Inventory line items (purchase templates) ─────────────────────────
    # Multi-SKU: user adds rows with item + qty + unit cost each.
    # Answer is serialized as JSON array → maps to line_items on TransactionAnswerSet.
    Step(
        id="inventory_link",
        question="What inventory did you purchase?",
        input_type="inventory_picker",
        maps_to="line_items",
        help_text="Add each SKU you bought. Use '+ Add another item' for multi-SKU shipments.",
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_INVENTORY_LINK,
    ),

    # ── 12. Freight-in ───────────────────────────────────────────────────────
    Step(
        id="freight_in_amount",
        question="Did the retailer charge you for shipping or handling?",
        input_type="number",
        maps_to="freight_in_amount",
        optional=True,
        default=Decimal("0"),
        help_text="If yes, enter the total. We'll add it to your inventory cost and split it across items automatically.",
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_FREIGHT_IN,
    ),

    # ── 12b. Purchase tax ────────────────────────────────────────────────────
    Step(
        id="purchase_tax_amount",
        question="Did you pay sales tax to the retailer?",
        input_type="number",
        maps_to="purchase_tax_amount",
        optional=True,
        default=Decimal("0"),
        help_text="Enter the total sales tax charged at purchase. We'll capitalize it into your inventory cost.",
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_FREIGHT_IN,
    ),

    # ── 12c. Non-inventory expense splits ────────────────────────────────────
    Step(
        id="has_expense_splits",
        question="Was anything else on this receipt NOT for resale inventory?",
        input_type="multi_choice",
        maps_to="has_expense_splits",
        options=[
            ("no", "No — everything on this receipt was inventory"),
            ("yes", "Yes — I also bought supplies, tools, or other non-inventory items"),
        ],
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_INVENTORY_LINK,
    ),
    Step(
        id="expense_splits",
        question="What non-inventory items were on this receipt?",
        input_type="expense_splits",
        maps_to="expense_splits",
        help_text="Check each expense category and enter the amount. The remaining total goes to inventory.",
        shown_when=lambda a: (
            _a(a, "template_id") in _TEMPLATES_WITH_INVENTORY_LINK
            and _a(a, "has_expense_splits") == "yes"
        ),
    ),

    # ── 13. Sales tax collected ──────────────────────────────────────────────
    Step(
        id="sales_tax_applies",
        question="Was this sale taxable?",
        input_type="multi_choice",
        maps_to="sales_tax_applies",
        options=[("yes", "Yes, sales tax was collected"), ("no", "No, this sale was tax-exempt")],
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_SALES_TAX,
    ),
    Step(
        id="sales_tax_mode",
        question="How should we figure out the sales tax amount?",
        input_type="multi_choice",
        maps_to="sales_tax_mode",
        options=[("auto", "Auto-calculate from the default sales tax rate"), ("manual", "I'll enter the tax amount myself")],
        shown_when=lambda a: (
            _a(a, "template_id") in _TEMPLATES_WITH_SALES_TAX
            and _a(a, "sales_tax_applies") == "yes"
        ),
    ),
    Step(
        id="sales_tax_amount",
        question="How much sales tax did you collect?",
        input_type="number",
        maps_to="sales_tax_amount",
        help_text="Enter just the tax portion collected for this sale.",
        shown_when=lambda a: (
            _a(a, "template_id") in _TEMPLATES_WITH_SALES_TAX
            and _a(a, "sales_tax_applies") == "yes"
            and _a(a, "sales_tax_mode") == "manual"
        ),
    ),

    # ── 14. Memo ─────────────────────────────────────────────────────────────
    Step(
        id="memo",
        question="Any notes? (Optional)",
        input_type="text",
        maps_to="memo",
        optional=True,
    ),

    # ── 15. Receipt ──────────────────────────────────────────────────────────
    Step(
        id="receipt_files",
        question="Got a receipt? Attach it or skip.",
        input_type="file_upload",
        maps_to="receipt_files",
        optional=True,
        shown_when=lambda a: _a(a, "template_id") not in ("COGS_RECOGNITION", "REIMBURSE_OWNER"),
    ),
]

# Index for O(1) lookup by step id.
_STEP_BY_ID: dict[str, Step] = {s.id: s for s in GLOBAL_FLOW}


# ── QuestionnaireSession ───────────────────────────────────────────────────────

@dataclass
class QuestionnaireSession:
    """Server-side state for one in-progress questionnaire.

    Only plain JSON-serializable values are stored so the session can be
    persisted to SQLite between requests.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    answers: dict[str, Any] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)   # step IDs answered, in order
    created_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )


# ── Engine functions ───────────────────────────────────────────────────────────

def next_visible_step(session: QuestionnaireSession) -> Optional[Step]:
    """Return the next step not yet answered whose shown_when passes."""
    answered = set(session.history)
    for step in GLOBAL_FLOW:
        if step.id in answered:
            continue
        if step.shown_when is None or step.shown_when(session.answers):
            return step
    return None


def current_step(session: QuestionnaireSession) -> Optional[Step]:
    """Return the Step for the most recently answered step_id."""
    if not session.history:
        return None
    return _STEP_BY_ID.get(session.history[-1])


def record_answer(
    session: QuestionnaireSession,
    step_id: str,
    raw_value: Any,
) -> None:
    """Coerce raw_value to the right type, store in answers, and advance history."""
    step = _STEP_BY_ID[step_id]
    session.answers[step.maps_to] = _coerce(step, raw_value)
    if step_id not in session.history:
        session.history.append(step_id)
    session.updated_at = datetime.utcnow().isoformat()


def go_back(session: QuestionnaireSession) -> Optional[str]:
    """Undo the last answered step. Returns the step_id now at the top of history.

    The answer is intentionally kept in session.answers so the step renders pre-filled.
    record_answer overwrites it when the user re-submits.
    """
    if len(session.history) < 2:
        return None
    last_id = session.history.pop()
    session.updated_at = datetime.utcnow().isoformat()
    return session.history[-1]


def is_complete(session: QuestionnaireSession) -> bool:
    """True when all non-optional visible steps have been answered."""
    answered = set(session.history)
    for step in GLOBAL_FLOW:
        if step.optional:
            continue
        if step.shown_when is not None and not step.shown_when(session.answers):
            continue
        if step.id not in answered:
            return False
    return True


def build_answer_set(session: QuestionnaireSession) -> dict:
    """Convert session.answers to a dict ready for TransactionAnswerSet(**d).

    Fills in defaults for skipped optional steps and derives funding_source
    from template_id so the posting engine gets a complete picture.
    """
    d: dict[str, Any] = {}

    # Copy all collected answers
    d.update(session.answers)

    # Fill in optional step defaults when skipped
    for step in GLOBAL_FLOW:
        if step.optional and step.maps_to not in d:
            if step.shown_when is None or step.shown_when(session.answers):
                if step.default is not None:
                    d[step.maps_to] = step.default

    # Derive funding_source from template_id (never asked as a separate step)
    template_id = d.get("template_id", "")
    d.setdefault("funding_source", _FUNDING_SOURCE.get(template_id))

    # Ensure entry_date is a date object if it came in as "today"
    if d.get("entry_date") == "today" or not d.get("entry_date"):
        d["entry_date"] = date.today()

    # receipt_files must always be a list[str] of stored_path values.
    # entry_answer stores full metadata dicts; extract just stored_path here.
    raw_files = d.get("receipt_files")
    if not isinstance(raw_files, list):
        d["receipt_files"] = []
    else:
        d["receipt_files"] = [
            f["stored_path"] if isinstance(f, dict) else f
            for f in raw_files
        ]

    # Convert raw line_items list (from picker JSON) to LineItem-compatible dicts,
    # and compute total_amount from item subtotals + freight.
    raw_items = d.get("line_items")
    if raw_items and isinstance(raw_items, list):
        converted: list[dict] = []
        subtotal = Decimal("0")
        for item in raw_items:
            qty = int(item.get("quantity", 1))
            cost = Decimal(str(item.get("unit_cost", "0"))).quantize(Decimal("0.0001"))
            subtotal += cost * qty
            converted.append({
                "inventory_link": {
                    "mode": item["mode"],
                    "item_id": item.get("item_id"),
                    "sku": item.get("sku"),
                    "name": item.get("name"),
                    "quantity": qty,
                    "is_lot": bool(item.get("is_lot", False)),
                },
                "unit_cost": cost,
                "quantity": qty,
            })
        d["line_items"] = converted
        freight = d.get("freight_in_amount") or Decimal("0")
        if not isinstance(freight, Decimal):
            freight = Decimal(str(freight)).quantize(Decimal("0.01"))
        ptax = d.get("purchase_tax_amount") or Decimal("0")
        if not isinstance(ptax, Decimal):
            ptax = Decimal(str(ptax)).quantize(Decimal("0.01"))

        # Add non-inventory expense splits to the grand total
        raw_splits = d.get("expense_splits")
        splits_total = Decimal("0")
        if raw_splits and isinstance(raw_splits, list):
            for s in raw_splits:
                try:
                    splits_total += Decimal(str(s.get("amount", "0"))).quantize(Decimal("0.01"))
                except InvalidOperation:
                    pass

        d["total_amount"] = (subtotal + freight + ptax + splits_total).quantize(Decimal("0.01"))

    if template_id in _TEMPLATES_WITH_SALES_TAX and d.get("sales_tax_applies") != "yes":
        d["sales_tax_amount"] = Decimal("0")
        d["sales_tax_jurisdiction_id"] = None

    return d


def step_progress(session: QuestionnaireSession) -> tuple[int, int]:
    """Return (answered_count, total_visible_count) for a progress indicator."""
    answered = set(session.history)
    total = sum(
        1
        for s in GLOBAL_FLOW
        if s.shown_when is None or s.shown_when(session.answers)
    )
    done = sum(1 for sid in answered if sid in _STEP_BY_ID)
    return done, total


# ── Coercion helper ────────────────────────────────────────────────────────────

def _coerce(step: Step, raw: Any) -> Any:
    """Parse raw form value into the Python type expected for this step."""
    # file_upload always returns a list — handle before the None short-circuit
    if step.input_type == "file_upload":
        return raw if isinstance(raw, list) else []

    if raw is None or raw == "":
        return step.default

    if step.input_type == "expense_splits":
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    if step.input_type == "number":
        try:
            return Decimal(str(raw)).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise ValueError(f"Step '{step.id}': expected a number, got {raw!r}")

    if step.input_type == "account_picker":
        try:
            return int(raw)
        except (ValueError, TypeError):
            raise ValueError(f"Step '{step.id}': expected an account ID, got {raw!r}")

    if step.input_type == "date":
        if raw == "today":
            return date.today().isoformat()
        # Accept ISO strings; validation happens in TransactionAnswerSet
        return str(raw)

    if step.input_type == "file_upload":
        # raw is already a list of stored paths supplied by the upload handler
        return raw if isinstance(raw, list) else []

    # multi_choice, text, inventory_picker — return as-is (str or dict)
    return raw
