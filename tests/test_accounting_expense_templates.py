from __future__ import annotations

import unittest
from decimal import Decimal

from app.accounting.catalog import resolve_lines
from app.accounting.questionnaire import QuestionnaireSession, next_visible_step, record_answer
from app.accounting.routes import _account_filter_for_step
from app.db import get_conn


_EXPENSE_TEMPLATE_TO_DEBIT_CODE = {
    "BUSINESS_MEAL": "6010",
    "TRAVEL_HOTEL": "6020",
    "TRAVEL_TRANSPORT": "6020",
    "OFFICE_SUPPLIES": "6030",
    "SOFTWARE_SUBSCRIPTION": "6040",
    "SHIPPING_OUTBOUND": "6050",
    "EBAY_FEES": "6060",
    "PAYMENT_PROCESSING_FEE": "6070",
    "UTILITIES": "6080",
    "RENT": "6090",
    "PROFESSIONAL_SERVICES": "6100",
    "BANK_FEE": "6110",
}


class FixedExpenseTemplateFlowTests(unittest.TestCase):
    def test_fixed_expense_template_uses_shared_questionnaire_flow(self) -> None:
        session = QuestionnaireSession()

        record_answer(session, "template_id", "TRAVEL_HOTEL")
        self.assertEqual(next_visible_step(session).id, "total_amount")

        record_answer(session, "total_amount", "60.00")
        self.assertEqual(next_visible_step(session).id, "entry_date")

        record_answer(session, "entry_date", "2026-05-06")
        self.assertEqual(next_visible_step(session).id, "vendor")

        record_answer(session, "vendor", "Hilton")
        self.assertEqual(next_visible_step(session).id, "payment_account_id")

    def test_fixed_expense_templates_resolve_to_expected_debit_accounts(self) -> None:
        conn = get_conn()
        try:
            account_map = {
                row["code"]: row["id"]
                for row in conn.execute("SELECT id, code FROM accounts").fetchall()
            }
        finally:
            conn.close()

        payment_account_id = account_map["1020"]
        for template_id, debit_code in _EXPENSE_TEMPLATE_TO_DEBIT_CODE.items():
            with self.subTest(template_id=template_id):
                lines = resolve_lines(
                    template_id=template_id,
                    total_amount=Decimal("25.00"),
                    account_map=account_map,
                    payment_account_id=payment_account_id,
                )
                self.assertEqual(len(lines), 2)
                self.assertEqual(lines[0].account_id, account_map[debit_code])
                self.assertEqual(lines[0].debit, Decimal("25.00"))
                self.assertEqual(lines[1].account_id, payment_account_id)
                self.assertEqual(lines[1].credit, Decimal("25.00"))

    def test_template_specific_payment_account_filters_match_spec(self) -> None:
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "BANK_FEE"}),
            "bank",
        )
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "PAYMENT_PROCESSING_FEE"}),
            "bank,cash",
        )
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "TRAVEL_HOTEL"}),
            "bank,credit_card,cash",
        )


if __name__ == "__main__":
    unittest.main()
