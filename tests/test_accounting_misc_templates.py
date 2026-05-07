from __future__ import annotations

import unittest
from decimal import Decimal

from app.accounting.catalog import resolve_lines
from app.accounting.questionnaire import QuestionnaireSession, next_visible_step, record_answer
from app.accounting.routes import _account_filter_for_step
from app.db import get_conn


class MiscAccountingTemplateFlowTests(unittest.TestCase):
    def test_other_expense_uses_shared_flow_with_category_and_payment(self) -> None:
        session = QuestionnaireSession()

        record_answer(session, "template_id", "OTHER_EXPENSE")
        self.assertEqual(next_visible_step(session).id, "total_amount")

        record_answer(session, "total_amount", "18.50")
        self.assertEqual(next_visible_step(session).id, "entry_date")

        record_answer(session, "entry_date", "2026-05-06")
        self.assertEqual(next_visible_step(session).id, "vendor")

        record_answer(session, "vendor", "Misc Vendor")
        self.assertEqual(next_visible_step(session).id, "payment_account_id")

        record_answer(session, "payment_account_id", "1")
        self.assertEqual(next_visible_step(session).id, "expense_category_account_id")

    def test_misc_templates_resolve_expected_debits_and_credits(self) -> None:
        conn = get_conn()
        try:
            account_map = {
                row["code"]: row["id"]
                for row in conn.execute("SELECT id, code FROM accounts").fetchall()
            }
        finally:
            conn.close()

        cases = [
            ("OWNER_CONTRIBUTION", None, account_map["1020"], account_map["1020"], account_map["3000"]),
            ("OWNER_DRAW", None, account_map["1020"], account_map["3000"], account_map["1020"]),
            ("PAY_CREDIT_CARD", None, account_map["1020"], account_map["2010"], account_map["1020"]),
            ("SALES_TAX_REMITTED", None, account_map["1020"], account_map["2100"], account_map["1020"]),
            ("OTHER_INCOME", None, account_map["1010"], account_map["1010"], account_map["4100"]),
            ("OTHER_EXPENSE", account_map["6030"], account_map["1010"], account_map["6030"], account_map["1010"]),
        ]

        for template_id, expense_category_id, payment_account_id, expected_debit, expected_credit in cases:
            with self.subTest(template_id=template_id):
                lines = resolve_lines(
                    template_id=template_id,
                    total_amount=Decimal("25.00"),
                    account_map=account_map,
                    payment_account_id=payment_account_id,
                    expense_category_account_id=expense_category_id,
                )
                self.assertEqual(len(lines), 2)
                self.assertEqual(lines[0].account_id, expected_debit)
                self.assertEqual(lines[0].debit, Decimal("25.00"))
                self.assertEqual(lines[1].account_id, expected_credit)
                self.assertEqual(lines[1].credit, Decimal("25.00"))

    def test_misc_template_payment_filters_match_spec(self) -> None:
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "OWNER_CONTRIBUTION"}),
            "bank,cash",
        )
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "OWNER_DRAW"}),
            "bank,cash",
        )
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "PAY_CREDIT_CARD"}),
            "bank",
        )
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "SALES_TAX_REMITTED"}),
            "bank",
        )
        self.assertEqual(
            _account_filter_for_step("payment_account_id", {"template_id": "OTHER_INCOME"}),
            "bank,credit_card,cash",
        )


if __name__ == "__main__":
    unittest.main()
