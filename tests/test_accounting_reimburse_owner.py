from __future__ import annotations

import sqlite3
import unittest
from decimal import Decimal

from app.accounting.questionnaire import QuestionnaireSession, next_visible_step, record_answer
from app.accounting.routes import _owner_reimbursement_balance, _validate_reimburse_owner_amount


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL
        );
        CREATE TABLE journal_entries (
          id INTEGER PRIMARY KEY,
          is_void INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE journal_lines (
          id INTEGER PRIMARY KEY,
          entry_id INTEGER NOT NULL,
          account_id INTEGER NOT NULL,
          debit TEXT NOT NULL DEFAULT '0',
          credit TEXT NOT NULL DEFAULT '0'
        );
        """
    )
    return conn


class ReimburseOwnerFlowTests(unittest.TestCase):
    def test_questionnaire_uses_reimbursement_specific_order(self) -> None:
        session = QuestionnaireSession()

        record_answer(session, "template_id", "REIMBURSE_OWNER")
        self.assertEqual(next_visible_step(session).id, "total_amount_reimburse_owner")

        record_answer(session, "total_amount_reimburse_owner", "12.00")
        self.assertEqual(next_visible_step(session).id, "payment_account_id_reimburse_owner")

        record_answer(session, "payment_account_id_reimburse_owner", "1")
        self.assertEqual(next_visible_step(session).id, "entry_date")

        record_answer(session, "entry_date", "2026-05-06")
        self.assertEqual(next_visible_step(session).id, "memo")

    def test_owner_reimbursement_balance_uses_contributions_minus_draws(self) -> None:
        conn = _memory_conn()
        try:
            conn.executemany(
                "INSERT INTO accounts (id, code) VALUES (?, ?)",
                [(1, "3100"), (2, "3200")],
            )
            conn.executemany(
                "INSERT INTO journal_entries (id, is_void) VALUES (?, ?)",
                [(1, 0), (2, 0), (3, 1)],
            )
            conn.executemany(
                "INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (?, ?, ?, ?)",
                [
                    (1, 1, "0", "60.00"),
                    (2, 2, "12.00", "0"),
                    (3, 1, "0", "999.00"),
                ],
            )

            self.assertEqual(_owner_reimbursement_balance(conn), Decimal("48.00"))
        finally:
            conn.close()

    def test_reimbursement_amount_cannot_exceed_current_balance(self) -> None:
        conn = _memory_conn()
        try:
            conn.executemany(
                "INSERT INTO accounts (id, code) VALUES (?, ?)",
                [(1, "3100"), (2, "3200")],
            )
            conn.executemany(
                "INSERT INTO journal_entries (id, is_void) VALUES (?, ?)",
                [(1, 0), (2, 0)],
            )
            conn.executemany(
                "INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (?, ?, ?, ?)",
                [
                    (1, 1, "0", "30.00"),
                    (2, 2, "5.00", "0"),
                ],
            )

            self.assertEqual(
                _validate_reimburse_owner_amount(conn, Decimal("25.00")),
                Decimal("25.00"),
            )
            with self.assertRaisesRegex(ValueError, "cannot exceed"):
                _validate_reimburse_owner_amount(conn, Decimal("25.01"))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
