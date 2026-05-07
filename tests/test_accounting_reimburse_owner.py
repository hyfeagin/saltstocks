from __future__ import annotations

import sqlite3
import unittest
from decimal import Decimal

from app.accounting.questionnaire import QuestionnaireSession, next_visible_step, record_answer
from app.accounting.routes import (
    _load_session,
    _owner_balance_detail,
    _owner_reimbursement_balance,
    _validate_reimburse_owner_amount,
    entry_start,
    owner_balance_page,
)
from starlette.requests import Request
from starlette.templating import _TemplateResponse


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
          entry_date TEXT,
          description TEXT,
          template_id TEXT,
          is_void INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE journal_lines (
          id INTEGER PRIMARY KEY,
          entry_id INTEGER NOT NULL,
          account_id INTEGER NOT NULL,
          debit TEXT NOT NULL DEFAULT '0',
          credit TEXT NOT NULL DEFAULT '0'
        );
        CREATE TABLE questionnaire_sessions (
          session_id TEXT PRIMARY KEY,
          data TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    return conn


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/accounting/owner-balance", "headers": []})


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
                "INSERT INTO journal_entries (id, entry_date, description, template_id, is_void) VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "2026-01-01", "Owner contribution", "OWNER_CONTRIBUTION", 0),
                    (2, "2026-01-02", "Owner draw", "OWNER_DRAW", 0),
                    (3, "2026-01-03", "Voided contribution", "OWNER_CONTRIBUTION", 1),
                ],
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
                "INSERT INTO journal_entries (id, entry_date, description, template_id, is_void) VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "2026-01-01", "Owner contribution", "OWNER_CONTRIBUTION", 0),
                    (2, "2026-01-02", "Owner draw", "OWNER_DRAW", 0),
                ],
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

    def test_owner_balance_detail_splits_contributions_and_draws_with_running_totals(self) -> None:
        conn = _memory_conn()
        try:
            conn.executemany(
                "INSERT INTO accounts (id, code) VALUES (?, ?)",
                [(1, "3100"), (2, "3200")],
            )
            conn.executemany(
                "INSERT INTO journal_entries (id, entry_date, description, template_id, is_void) VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "2026-01-01", "Owner contribution A", "OWNER_CONTRIBUTION", 0),
                    (2, "2026-01-02", "Owner draw", "OWNER_DRAW", 0),
                    (3, "2026-01-03", "Owner contribution B", "OWNER_CONTRIBUTION", 0),
                ],
            )
            conn.executemany(
                "INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (?, ?, ?, ?)",
                [
                    (1, 1, "0", "30.00"),
                    (2, 2, "5.00", "0"),
                    (3, 1, "0", "12.00"),
                ],
            )

            detail = _owner_balance_detail(conn)
            self.assertEqual([str(row["running_total"]) for row in detail["contributions"]], ["30.00", "42.00"])
            self.assertEqual([str(row["running_total"]) for row in detail["draws"]], ["5.00"])
            self.assertEqual(str(detail["total_contributions"]), "42.00")
            self.assertEqual(str(detail["total_draws"]), "5.00")
            self.assertEqual(str(detail["balance"]), "37.00")
        finally:
            conn.close()

    def test_owner_balance_page_renders_and_reimburse_start_prefills_full_balance(self) -> None:
        conn = _memory_conn()
        try:
            conn.executemany(
                "INSERT INTO accounts (id, code) VALUES (?, ?)",
                [(1, "3100"), (2, "3200")],
            )
            conn.executemany(
                "INSERT INTO journal_entries (id, entry_date, description, template_id, is_void) VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "2026-01-01", "Owner contribution", "OWNER_CONTRIBUTION", 0),
                    (2, "2026-01-02", "Owner draw", "OWNER_DRAW", 0),
                ],
            )
            conn.executemany(
                "INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (?, ?, ?, ?)",
                [
                    (1, 1, "0", "50.00"),
                    (2, 2, "8.00", "0"),
                ],
            )

            response = owner_balance_page(request=_request(), conn=conn)
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/owner_balance.html")
            self.assertEqual(response.context["detail"]["balance"].__str__(), "42.00")

            redirect = entry_start(template_id="REIMBURSE_OWNER", prefill_owner_balance=1, conn=conn)
            session_id = redirect.headers["location"].rsplit("/", 1)[-1]
            session = _load_session(conn, session_id)
            self.assertEqual(session.answers["template_id"], "REIMBURSE_OWNER")
            self.assertEqual(str(session.answers["total_amount"]), "42.00")
            self.assertEqual(session.history, ["template_id", "total_amount_reimburse_owner"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
