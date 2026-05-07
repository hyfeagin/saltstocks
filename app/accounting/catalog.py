from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.accounting.posting import JournalLineInput


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    # Fixed account codes resolved against the chart of accounts.
    # None means the account ID comes from the answer-set (see *_from_field).
    debit_account_code: Optional[str]
    credit_account_code: Optional[str]
    # Names of answer-set fields that supply dynamic account IDs when
    # the corresponding code field is None.
    debit_from_field: Optional[str]
    credit_from_field: Optional[str]
    # Answer-set fields that must be non-None before posting.
    required_fields: tuple[str, ...]


# fmt: off
_TEMPLATES: list[Template] = [
    # ── Inventory purchases ────────────────────────────────────────────────
    Template(
        id="BUY_INVENTORY",
        name="Bought inventory for resale (business funds)",
        debit_account_code="1200", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="BUY_INVENTORY_PERSONAL",
        name="Bought inventory for resale (personal funds)",
        debit_account_code="1200", debit_from_field=None,
        credit_account_code="3100", credit_from_field=None,
        required_fields=("total_amount",),
    ),
    # ── Personal-funds expense + reimbursement ────────────────────────────
    Template(
        id="BUY_EXPENSE_PERSONAL",
        name="Paid a business expense with personal funds",
        debit_account_code=None,   debit_from_field="expense_category_account_id",
        credit_account_code="3100", credit_from_field=None,
        required_fields=("total_amount", "expense_category_account_id"),
    ),
    Template(
        id="REIMBURSE_OWNER",
        name="Business pays owner back for personal funds used",
        debit_account_code="3200", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    # ── Sales ─────────────────────────────────────────────────────────────
    # SELL_INVENTORY_CASH credit may split into 4000 + 2100 when tax present.
    # resolve_lines handles the split; credit_account_code is the primary credit.
    Template(
        id="SELL_INVENTORY_CASH",
        name="Sold inventory (cash/card payment received)",
        debit_account_code=None,   debit_from_field="payment_account_id",
        credit_account_code="4000", credit_from_field=None,
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="COGS_RECOGNITION",
        name="Cost of goods sold (auto when sale recorded)",
        debit_account_code="5000", debit_from_field=None,
        credit_account_code="1200", credit_from_field=None,
        required_fields=("total_amount",),
    ),
    # ── Business expenses ─────────────────────────────────────────────────
    Template(
        id="BUSINESS_MEAL",
        name="Business meal",
        debit_account_code="6010", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="TRAVEL_HOTEL",
        name="Hotel for business travel",
        debit_account_code="6020", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="TRAVEL_TRANSPORT",
        name="Flight / train / rideshare for business",
        debit_account_code="6020", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="OFFICE_SUPPLIES",
        name="Office supplies",
        debit_account_code="6030", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="SOFTWARE_SUBSCRIPTION",
        name="Software subscription",
        debit_account_code="6040", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="SHIPPING_OUTBOUND",
        name="Shipping label for customer order",
        debit_account_code="6050", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="EBAY_FEES",
        name="eBay or marketplace fees",
        debit_account_code="6060", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="PAYMENT_PROCESSING_FEE",
        name="Stripe / PayPal / Square fee",
        debit_account_code="6070", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="UTILITIES",
        name="Internet, phone, electric (business %)",
        debit_account_code="6080", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="RENT",
        name="Business rent / coworking",
        debit_account_code="6090", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="PROFESSIONAL_SERVICES",
        name="Accountant, lawyer, consultant",
        debit_account_code="6100", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="BANK_FEE",
        name="Bank service fee",
        debit_account_code="6110", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    # ── Owner equity movements ────────────────────────────────────────────
    Template(
        id="OWNER_CONTRIBUTION",
        name="Owner put personal money into the business",
        debit_account_code=None,   debit_from_field="payment_account_id",
        credit_account_code="3000", credit_from_field=None,
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="OWNER_DRAW",
        name="Owner took money out of the business",
        debit_account_code="3000", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    # ── Liabilities & tax ────────────────────────────────────────────────
    Template(
        id="PAY_CREDIT_CARD",
        name="Paid down credit card from bank",
        debit_account_code="2010", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    Template(
        id="SALES_TAX_REMITTED",
        name="Paid sales tax to state",
        debit_account_code="2100", debit_from_field=None,
        credit_account_code=None,  credit_from_field="payment_account_id",
        required_fields=("total_amount", "payment_account_id"),
    ),
    # ── Catch-alls ────────────────────────────────────────────────────────
    Template(
        id="OTHER_EXPENSE",
        name="Other business expense",
        debit_account_code=None,  debit_from_field="expense_category_account_id",
        credit_account_code=None, credit_from_field="payment_account_id",
        required_fields=("total_amount", "expense_category_account_id", "payment_account_id"),
    ),
    Template(
        id="OTHER_INCOME",
        name="Non-sales income (refund, rebate, etc.)",
        debit_account_code=None,   debit_from_field="payment_account_id",
        credit_account_code="4100", credit_from_field=None,
        required_fields=("total_amount", "payment_account_id"),
    ),
]
# fmt: on

CATALOG: dict[str, Template] = {t.id: t for t in _TEMPLATES}


def get_template(template_id: str) -> Template:
    """Return the Template for template_id; raises KeyError on unknown ID."""
    try:
        return CATALOG[template_id]
    except KeyError:
        raise KeyError(f"Unknown template '{template_id}'. Valid IDs: {sorted(CATALOG)}")


def resolve_lines(
    template_id: str,
    total_amount: Decimal,
    account_map: dict[str, int],
    *,
    payment_account_id: Optional[int] = None,
    expense_category_account_id: Optional[int] = None,
    sales_tax_amount: Optional[Decimal] = None,
    memo: Optional[str] = None,
    inventory_item_id: Optional[int] = None,
) -> list[JournalLineInput]:
    """Build journal lines for a template given resolved answer-set values.

    account_map  maps account code → database account.id for fixed-code lookups.
    Dynamic accounts (payment, expense category) are supplied as integer IDs.

    SELL_INVENTORY_CASH splits the credit into Sales Revenue + Sales Tax Payable
    when sales_tax_amount is provided and > 0.

    Raises ValueError for missing required dynamic accounts.
    """
    tmpl = get_template(template_id)

    # Validate required dynamic accounts
    field_values = {
        "payment_account_id": payment_account_id,
        "expense_category_account_id": expense_category_account_id,
    }
    for field in tmpl.required_fields:
        if field == "total_amount":
            continue
        if field in field_values and field_values[field] is None:
            raise ValueError(f"Template '{template_id}' requires '{field}' but it is None.")

    def _fixed(code: str) -> int:
        if code not in account_map:
            raise ValueError(f"Account code '{code}' not found in account_map.")
        return account_map[code]

    def _debit_account_id() -> int:
        if tmpl.debit_account_code is not None:
            return _fixed(tmpl.debit_account_code)
        if tmpl.debit_from_field == "payment_account_id":
            return payment_account_id  # type: ignore[return-value]
        if tmpl.debit_from_field == "expense_category_account_id":
            return expense_category_account_id  # type: ignore[return-value]
        raise ValueError(f"Cannot resolve debit account for template '{template_id}'.")

    def _credit_account_id() -> int:
        if tmpl.credit_account_code is not None:
            return _fixed(tmpl.credit_account_code)
        if tmpl.credit_from_field == "payment_account_id":
            return payment_account_id  # type: ignore[return-value]
        if tmpl.credit_from_field == "expense_category_account_id":
            return expense_category_account_id  # type: ignore[return-value]
        raise ValueError(f"Cannot resolve credit account for template '{template_id}'.")

    # ── SELL_INVENTORY_CASH: optional tax split ───────────────────────────
    if template_id == "SELL_INVENTORY_CASH":
        tax = sales_tax_amount or Decimal("0")
        revenue = total_amount - tax
        lines: list[JournalLineInput] = [
            JournalLineInput(
                account_id=_debit_account_id(),
                debit=total_amount,
                memo=memo,
            )
        ]
        lines.append(
            JournalLineInput(
                account_id=_fixed("4000"),
                credit=revenue,
                memo=memo,
            )
        )
        if tax > Decimal("0"):
            lines.append(
                JournalLineInput(
                    account_id=_fixed("2100"),
                    credit=tax,
                    memo="Sales tax",
                )
            )
        return lines

    # ── Standard two-line entry ───────────────────────────────────────────
    return [
        JournalLineInput(
            account_id=_debit_account_id(),
            debit=total_amount,
            memo=memo,
            inventory_item_id=inventory_item_id,
        ),
        JournalLineInput(
            account_id=_credit_account_id(),
            credit=total_amount,
            memo=memo,
            inventory_item_id=inventory_item_id,
        ),
    ]
