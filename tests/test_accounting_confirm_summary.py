from __future__ import annotations

import unittest
from decimal import Decimal

from app.accounting.routes import _build_summary_text
from app.accounting.schemas import TransactionAnswerSet


class ConfirmSummaryTests(unittest.TestCase):
    def test_inventory_summary_mentions_freight_inclusive_unit_cost(self) -> None:
        tas = TransactionAnswerSet(
            template_id="BUY_INVENTORY_PERSONAL",
            total_amount=Decimal("15.00"),
            freight_in_amount=Decimal("3.00"),
            inventory_link={"mode": "create_new", "name": "plushies", "quantity": 12},
        )

        summary = _build_summary_text(
            tas,
            "Bought inventory for resale (personal funds)",
            payment_account_name=None,
            expense_account_name=None,
            inventory_item_name="plushies",
            inventory_purchase_ctx={"allocated_unit_cost": Decimal("1.25")},
        )

        self.assertIn("$12.00 + $3.00 shipping = $15.00 total", summary)
        self.assertIn("Each plushies costs $1.25 with shipping included.", summary)
