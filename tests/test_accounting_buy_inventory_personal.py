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


class BuyInventoryPersonalFlowTests(unittest.TestCase):
    def test_questionnaire_skips_payment_account_for_personal_inventory(self) -> None:
        session = QuestionnaireSession()

        record_answer(session, "template_id", "BUY_INVENTORY_PERSONAL")
        self.assertEqual(next_visible_step(session).id, "total_amount")

        record_answer(session, "total_amount", "15.00")
        self.assertEqual(next_visible_step(session).id, "entry_date")

        record_answer(session, "entry_date", "2026-05-06")
        self.assertEqual(next_visible_step(session).id, "vendor")

        record_answer(session, "vendor", "Toy World")
        self.assertEqual(next_visible_step(session).id, "inventory_link")

    def test_answer_set_derives_personal_funding_without_payment_account(self) -> None:
        session = QuestionnaireSession()
        answers = {
            "template_id": "BUY_INVENTORY_PERSONAL",
            "total_amount": "15.00",
            "entry_date": "2026-05-06",
            "vendor": "Toy World",
            "inventory_link": {"mode": "create_new", "name": "Plushie", "quantity": 12},
            "freight_in_amount": "",
            "memo": "",
            "receipt_files": [],
        }

        for step_id, raw_value in answers.items():
            record_answer(session, step_id, raw_value)

        tas = TransactionAnswerSet(**build_answer_set(session))

        self.assertEqual(tas.template_id, "BUY_INVENTORY_PERSONAL")
        self.assertEqual(tas.funding_source, "personal")
        self.assertIsNone(tas.payment_account_id)
        self.assertEqual(tas.freight_in_amount, Decimal("0"))

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
            template_id="BUY_INVENTORY_PERSONAL",
            total_amount=Decimal("15.00"),
            account_map=account_map,
            payment_account_id=None,
        )

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].debit, Decimal("15.00"))
        self.assertEqual(lines[0].credit, Decimal("0"))
        self.assertEqual(lines[1].debit, Decimal("0"))
        self.assertEqual(lines[1].credit, Decimal("15.00"))
        self.assertNotEqual(lines[0].account_id, lines[1].account_id)
        self.assertEqual(account_map["1200"], lines[0].account_id)
        self.assertEqual(account_map["3100"], lines[1].account_id)


if __name__ == "__main__":
    unittest.main()
