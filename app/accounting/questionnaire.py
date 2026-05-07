from __future__ import annotations

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

    # ── 4. Sale inventory item (SELL_INVENTORY_CASH only) ──────────────────
    Step(
        id="inventory_link_sale",
        question="Which inventory item did you sell?",
        input_type="inventory_picker",
        maps_to="inventory_link",
        help_text="Choose the SKU sold and how many units were included in this sale.",
        shown_when=lambda a: _a(a, "template_id") == "SELL_INVENTORY_CASH",
    ),

    # ── 5. Total amount ──────────────────────────────────────────────────────
    Step(
        id="total_amount",
        question="What was the total amount?",
        input_type="number",
        maps_to="total_amount",
        help_text="Enter the grand total including any sales tax you collected or paid.",
        shown_when=lambda a: _a(a, "template_id") != "REIMBURSE_OWNER",
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

    # ── 11. Inventory link ───────────────────────────────────────────────────
    Step(
        id="inventory_link",
        question="Which inventory item does this purchase add to?",
        input_type="inventory_picker",
        maps_to="inventory_link",
        help_text="Link to an existing SKU or create a new one. Use '+ Add another item' for multi-SKU shipments.",
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_INVENTORY_LINK,
    ),

    # ── 12. Freight-in ───────────────────────────────────────────────────────
    Step(
        id="freight_in_amount",
        question="Did the wholesaler charge you for shipping or handling?",
        input_type="number",
        maps_to="freight_in_amount",
        optional=True,
        default=Decimal("0"),
        help_text="If yes, enter the total. We'll add it to your inventory cost and split it across items automatically.",
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_FREIGHT_IN,
    ),

    # ── 13. Sales tax collected ──────────────────────────────────────────────
    Step(
        id="sales_tax_amount",
        question="How much sales tax did you collect? (Optional)",
        input_type="number",
        maps_to="sales_tax_amount",
        optional=True,
        default=Decimal("0"),
        help_text="Leave blank if the sale was tax-exempt or you didn't collect tax.",
        shown_when=lambda a: _a(a, "template_id") in _TEMPLATES_WITH_SALES_TAX,
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
    """Undo the last answered step. Returns the step_id now at the top of history."""
    if len(session.history) < 2:
        return None
    last_id = session.history.pop()
    last_step = _STEP_BY_ID.get(last_id)
    if last_step:
        session.answers.pop(last_step.maps_to, None)
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

    # receipt_files must always be a list (never None)
    if not isinstance(d.get("receipt_files"), list):
        d["receipt_files"] = []

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
