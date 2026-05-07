from __future__ import annotations

import unittest
from decimal import Decimal

from app.accounting.catalog import resolve_lines
from app.accounting.questionnaire import (
    QuestionnaireSession,
    build_answer_set,
    next_visible_step,
    record_answer,
)
from app.accounting.schemas import TransactionAnswerSet
from app.db import get_conn


class BuyExpensePersonalFlowTests(unittest.TestCase):
    def _expense_account_id(self) -> int:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM accounts WHERE code='6020'"
            ).fetchone()
            assert row is not None
            return row["id"]
        finally:
            conn.close()

    def test_questionnaire_asks_expense_category_before_amount(self) -> None:
        session = QuestionnaireSession()

        record_answer(session, "template_id", "BUY_EXPENSE_PERSONAL")
        self.assertEqual(next_visible_step(session).id, "expense_category_account_id_personal")

        record_answer(
            session,
            "expense_category_account_id_personal",
            str(self._expense_account_id()),
        )
        self.assertEqual(next_visible_step(session).id, "total_amount")

        record_answer(session, "total_amount", "60.00")
        self.assertEqual(next_visible_step(session).id, "entry_date")

        record_answer(session, "entry_date", "2026-05-06")
        self.assertEqual(next_visible_step(session).id, "vendor")

    def test_answer_set_derives_personal_funding_without_payment_account(self) -> None:
        session = QuestionnaireSession()
        answers = {
            "template_id": "BUY_EXPENSE_PERSONAL",
            "expense_category_account_id_personal": str(self._expense_account_id()),
            "total_amount": "60.00",
            "entry_date": "2026-05-06",
            "vendor": "Hilton",
            "memo": "",
            "receipt_files": [],
        }

        for step_id, raw_value in answers.items():
            record_answer(session, step_id, raw_value)

        tas = TransactionAnswerSet(**build_answer_set(session))

        self.assertEqual(tas.template_id, "BUY_EXPENSE_PERSONAL")
        self.assertEqual(tas.funding_source, "personal")
        self.assertIsNone(tas.payment_account_id)
        self.assertEqual(tas.expense_category_account_id, self._expense_account_id())

    def test_posting_lines_credit_owner_contributions(self) -> None:
        conn = get_conn()
        try:
            account_map = {
                row["code"]: row["id"]
                for row in conn.execute("SELECT id, code FROM accounts").fetchall()
            }
        finally:
            conn.close()

        lines = resolve_lines(
            template_id="BUY_EXPENSE_PERSONAL",
            total_amount=Decimal("60.00"),
            account_map=account_map,
            expense_category_account_id=account_map["6020"],
            payment_account_id=None,
        )

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].account_id, account_map["6020"])
        self.assertEqual(lines[0].debit, Decimal("60.00"))
        self.assertEqual(lines[1].account_id, account_map["3100"])
        self.assertEqual(lines[1].credit, Decimal("60.00"))


if __name__ == "__main__":
    unittest.main()
