from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator


class ExpenseSplit(BaseModel):
    """One non-inventory expense line on a mixed purchase receipt."""

    account_code: str
    amount: Decimal
    memo: Optional[str] = None


class InventoryLink(BaseModel):
    """Link a journal-entry line to an inventory item (existing or to be created)."""

    mode: Literal["create_new", "link_existing"]
    item_id: Optional[int] = None   # link_existing: FK → items.id
    sku: Optional[str] = None       # link_existing: alt lookup when item_id unknown
    name: Optional[str] = None      # create_new: display name for the new item
    quantity: int = 1
    is_lot: bool = False            # create_new: receive as N individual items each with qty=1
    fungible: bool = False          # create_new + is_lot: single item row with qty_on_hand=N instead of N rows

    @model_validator(mode="after")
    def _check_mode_fields(self) -> InventoryLink:
        if self.mode == "link_existing" and self.item_id is None and not self.sku:
            raise ValueError("link_existing requires item_id or sku")
        if self.mode == "create_new" and not self.name:
            raise ValueError("create_new requires a name")
        return self


class LineItem(BaseModel):
    """One SKU line in a multi-item purchase.

    Used when a single shipment contains several different SKUs.
    The subtotal_cost feeds into freight-in allocation (§8.5) before posting.
    """

    inventory_link: InventoryLink
    unit_cost: Decimal
    quantity: int = 1

    @computed_field
    @property
    def subtotal_cost(self) -> Decimal:
        return (self.unit_cost * Decimal(self.quantity)).quantize(Decimal("0.01"))


class TransactionAnswerSet(BaseModel):
    """Canonical answer-set produced by the questionnaire or AI layer.

    Both entry paths converge on this object before the posting engine runs.
    Fields irrelevant to a given template default to None / [].
    """

    # ── Identification ────────────────────────────────────────────────────
    template_id: str
    funding_source: Optional[Literal["business", "personal"]] = None

    # ── Money & timing ────────────────────────────────────────────────────
    total_amount: Decimal
    entry_date: date = Field(default_factory=date.today)

    # ── Counterparty ──────────────────────────────────────────────────────
    vendor: Optional[str] = None
    payment_account_id: Optional[int] = None
    expense_category_account_id: Optional[int] = None

    # ── Inventory linkage ─────────────────────────────────────────────────
    # Single-item: set inventory_link.
    # Multi-SKU shipment: set line_items (one per SKU).
    # Both must not be set simultaneously.
    inventory_link: Optional[InventoryLink] = None
    line_items: list[LineItem] = []

    # ── Inbound freight & purchase tax (BUY_INVENTORY / BUY_INVENTORY_PERSONAL only) ─────
    freight_in_amount: Optional[Decimal] = None
    purchase_tax_amount: Optional[Decimal] = None  # sales tax paid to retailer, capitalized into inventory

    # ── Expense splits (BUY_INVENTORY / BUY_INVENTORY_PERSONAL only) ──────
    # Non-inventory lines on the same receipt (e.g. bubble wrap, office supplies).
    # These are expensed directly; only the remaining inventory portion is capitalized.
    expense_splits: list[ExpenseSplit] = []

    # ── Sales tax (SELL_INVENTORY_CASH and similar) ───────────────────────
    sales_tax_amount: Optional[Decimal] = None
    sales_tax_jurisdiction_id: Optional[int] = None

    # ── eBay sale details (SELL_INVENTORY_EBAY only) ──────────────────────
    ebay_fees_amount: Optional[Decimal] = None       # eBay final value fee + other fees
    ebay_shipping_charged: Optional[Decimal] = None  # shipping charged to buyer (0 if free)

    # ── Free-form ─────────────────────────────────────────────────────────
    memo: Optional[str] = None
    description: Optional[str] = None  # None → auto-generated from template name
    notes: Optional[str] = None
    receipt_files: list[str] = []      # relative stored_path values after upload

    @model_validator(mode="after")
    def _validate(self) -> TransactionAnswerSet:
        # Inventory linkage is single-item XOR multi-SKU
        if self.inventory_link is not None and self.line_items:
            raise ValueError(
                "Set inventory_link (single item) or line_items (multi-SKU), not both."
            )

        # Defer catalog import to avoid top-level circularity risk
        from app.accounting.catalog import CATALOG

        if self.template_id not in CATALOG:
            raise ValueError(
                f"Unknown template_id {self.template_id!r}. "
                f"Valid IDs: {sorted(CATALOG)}"
            )

        return self
