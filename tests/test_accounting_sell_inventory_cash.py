from __future__ import annotations

import sqlite3
import unittest
from datetime import date
from decimal import Decimal

from app.accounting.catalog import resolve_lines
from app.accounting.posting import JournalLineInput, PostEntryRequest, post_entries
from app.accounting.questionnaire import QuestionnaireSession, next_visible_step, record_answer
from app.accounting.routes import _normalize_sales_tax_answers, _resolve_sale_inventory_context


def _sale_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE items (
          id INTEGER PRIMARY KEY,
          item_type TEXT NOT NULL,
          sku TEXT,
          name TEXT NOT NULL,
          qty_on_hand REAL NOT NULL DEFAULT 0,
          unit_cost REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL
        );
        CREATE TABLE sales_tax_rates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          jurisdiction TEXT NOT NULL,
          rate TEXT NOT NULL,
          is_default INTEGER NOT NULL DEFAULT 0,
          effective_from TEXT NOT NULL,
          effective_to TEXT
        );
        CREATE TABLE journal_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_date TEXT NOT NULL,
          description TEXT NOT NULL,
          template_id TEXT NOT NULL,
          total_amount TEXT NOT NULL,
          created_by_method TEXT NOT NULL,
          vendor TEXT,
          notes TEXT,
          is_void INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE journal_lines (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_id INTEGER NOT NULL,
          account_id INTEGER NOT NULL,
          debit TEXT NOT NULL DEFAULT '0',
          credit TEXT NOT NULL DEFAULT '0',
          memo TEXT,
          inventory_item_id INTEGER
        );
        """
    )
    return conn


class SellInventoryCashFlowTests(unittest.TestCase):
    def test_questionnaire_prompts_for_sales_tax_branch(self) -> None:
        session = QuestionnaireSession()

        record_answer(session, "template_id", "SELL_INVENTORY_CASH")
        self.assertEqual(next_visible_step(session).id, "inventory_link_sale")

        record_answer(
            session,
            "inventory_link_sale",
            {"mode": "link_existing", "item_id": 1, "quantity": 1},
        )
        self.assertEqual(next_visible_step(session).id, "total_amount")

        record_answer(session, "total_amount", "10.00")
        record_answer(session, "entry_date", "2026-05-06")
        record_answer(session, "vendor", "Buyer")
        record_answer(session, "payment_account_id", "1")
        self.assertEqual(next_visible_step(session).id, "sales_tax_applies")

        record_answer(session, "sales_tax_applies", "yes")
        self.assertEqual(next_visible_step(session).id, "sales_tax_mode")

        record_answer(session, "sales_tax_mode", "auto")
        self.assertEqual(next_visible_step(session).id, "memo")

    def test_tax_exempt_sale_skips_tax_amount_in_answer_set(self) -> None:
        session = QuestionnaireSession()

        record_answer(session, "template_id", "SELL_INVENTORY_CASH")
        record_answer(
            session,
            "inventory_link_sale",
            {"mode": "link_existing", "item_id": 1, "quantity": 1},
        )
        record_answer(session, "total_amount", "10.00")
        record_answer(session, "entry_date", "2026-05-06")
        record_answer(session, "vendor", "Buyer")
        record_answer(session, "payment_account_id", "1")
        record_answer(session, "sales_tax_applies", "no")

        conn = _sale_conn()
        try:
            answer_dict = _normalize_sales_tax_answers(conn=conn, answer_dict=session.answers.copy())
            self.assertEqual(answer_dict["sales_tax_amount"], Decimal("0"))
            self.assertIsNone(answer_dict["sales_tax_jurisdiction_id"])
        finally:
            conn.close()

    def test_auto_sales_tax_uses_default_rate_and_backs_out_tax_from_total(self) -> None:
        conn = _sale_conn()
        try:
            conn.execute(
                """
                INSERT INTO sales_tax_rates (jurisdiction, rate, is_default, effective_from)
                VALUES ('North Carolina', '0.0475', 1, '2024-01-01')
                """
            )
            answer_dict = {
                "template_id": "SELL_INVENTORY_CASH",
                "total_amount": Decimal("10.48"),
                "sales_tax_applies": "yes",
                "sales_tax_mode": "auto",
            }

            normalized = _normalize_sales_tax_answers(conn, answer_dict)
            self.assertEqual(normalized["sales_tax_amount"], Decimal("0.48"))
            self.assertEqual(normalized["sales_tax_jurisdiction_id"], 1)
        finally:
            conn.close()

    def test_resolve_sale_inventory_context_validates_existing_stock(self) -> None:
        conn = _sale_conn()
        try:
            conn.execute(
                """
                INSERT INTO items (id, item_type, sku, name, qty_on_hand, unit_cost)
                VALUES (1, 'resale', 'SKU-1', 'Plushie', 3, 1.25)
                """
            )

            from app.accounting.schemas import TransactionAnswerSet

            tas = TransactionAnswerSet(
                template_id="SELL_INVENTORY_CASH",
                total_amount=Decimal("5.00"),
                payment_account_id=10,
                inventory_link={"mode": "link_existing", "item_id": 1, "quantity": 2},
            )
            ctx = _resolve_sale_inventory_context(conn, tas)
            self.assertEqual(ctx["item_name"], "Plushie")
            self.assertEqual(ctx["quantity"], Decimal("2"))
            self.assertEqual(ctx["cogs_amount"], Decimal("2.50"))

            oversell = TransactionAnswerSet(
                template_id="SELL_INVENTORY_CASH",
                total_amount=Decimal("5.00"),
                payment_account_id=10,
                inventory_link={"mode": "link_existing", "item_id": 1, "quantity": 4},
            )
            with self.assertRaisesRegex(ValueError, "can't record"):
                _resolve_sale_inventory_context(conn, oversell)
        finally:
            conn.close()

    def test_sale_lines_split_sales_tax_and_auto_cogs_can_post_atomically(self) -> None:
        conn = _sale_conn()
        try:
            conn.executemany(
                "INSERT INTO accounts (id, code, name) VALUES (?, ?, ?)",
                [
                    (1, "1020", "Bank"),
                    (2, "4000", "Sales Revenue"),
                    (3, "2100", "Sales Tax Payable"),
                    (4, "5000", "Cost of Goods Sold"),
                    (5, "1200", "Inventory"),
                ],
            )
            conn.execute(
                """
                INSERT INTO items (id, item_type, sku, name, qty_on_hand, unit_cost)
                VALUES (1, 'resale', 'SKU-1', 'Plushie', 3, 1.25)
                """
            )

            account_map = {
                row["code"]: row["id"]
                for row in conn.execute("SELECT id, code FROM accounts").fetchall()
            }
            sale_lines = resolve_lines(
                template_id="SELL_INVENTORY_CASH",
                total_amount=Decimal("27.50"),
                account_map=account_map,
                payment_account_id=account_map["1020"],
                sales_tax_amount=Decimal("2.50"),
                inventory_item_id=1,
            )
            self.assertEqual(
                [(ln.account_id, ln.debit, ln.credit) for ln in sale_lines],
                [
                    (account_map["1020"], Decimal("27.50"), Decimal("0")),
                    (account_map["4000"], Decimal("0"), Decimal("25.00")),
                    (account_map["2100"], Decimal("0"), Decimal("2.50")),
                ],
            )

            sale_req = PostEntryRequest(
                entry_date=date(2026, 5, 6),
                description="Sold inventory",
                template_id="SELL_INVENTORY_CASH",
                total_amount=Decimal("27.50"),
                lines=sale_lines,
            )
            cogs_req = PostEntryRequest(
                entry_date=date(2026, 5, 6),
                description="Auto COGS — Sold inventory",
                template_id="COGS_RECOGNITION",
                total_amount=Decimal("1.25"),
                lines=[
                    JournalLineInput(
                        account_id=account_map["5000"],
                        debit=Decimal("1.25"),
                        memo="Auto COGS",
                        inventory_item_id=1,
                    ),
                    JournalLineInput(
                        account_id=account_map["1200"],
                        credit=Decimal("1.25"),
                        memo="Auto COGS",
                        inventory_item_id=1,
                    ),
                ],
                created_by_method="system_auto",
            )

            post_entries(
                conn,
                [sale_req, cogs_req],
                after_insert=lambda tx_conn, _ids: tx_conn.execute(
                    "UPDATE items SET qty_on_hand = qty_on_hand - 1 WHERE id=1"
                ),
            )

            qty = conn.execute("SELECT qty_on_hand FROM items WHERE id=1").fetchone()["qty_on_hand"]
            self.assertEqual(qty, 2.0)
            templates = [
                row["template_id"]
                for row in conn.execute(
                    "SELECT template_id FROM journal_entries ORDER BY id"
                ).fetchall()
            ]
            self.assertEqual(templates, ["SELL_INVENTORY_CASH", "COGS_RECOGNITION"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
