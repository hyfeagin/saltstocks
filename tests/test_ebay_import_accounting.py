from __future__ import annotations

import sqlite3
import unittest
from decimal import Decimal

from app.routers.ebay import _post_ebay_cogs_entry


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL
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
          debit TEXT NOT NULL,
          credit TEXT NOT NULL,
          memo TEXT,
          inventory_item_id INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO accounts (id, code, name) VALUES (?, ?, ?)",
        [
            (1, "1200", "Inventory"),
            (2, "5000", "Cost of Goods Sold"),
        ],
    )
    return conn


class EbayImportAccountingTests(unittest.TestCase):
    def test_post_ebay_cogs_entry_records_cogs_and_inventory_lines(self) -> None:
        conn = _conn()
        try:
            entry_id = _post_ebay_cogs_entry(
                conn,
                order_id="ORDER-1",
                line_item_id="LINE-1",
                item_id=42,
                item_name="Plushie",
                sku="GV-MISC-000042",
                quantity_deducted=Decimal("2"),
                unit_cost=Decimal("1.25"),
            )

            self.assertIsNotNone(entry_id)
            entry = conn.execute(
                "SELECT template_id, total_amount, created_by_method, notes FROM journal_entries WHERE id=?",
                (entry_id,),
            ).fetchone()
            self.assertEqual(entry["template_id"], "COGS_RECOGNITION")
            self.assertEqual(entry["total_amount"], "2.50")
            self.assertEqual(entry["created_by_method"], "import_ebay")
            self.assertIn("ORDER-1", entry["notes"])

            lines = conn.execute(
                """
                SELECT account_id, debit, credit, memo, inventory_item_id
                FROM journal_lines
                WHERE entry_id=?
                ORDER BY id
                """,
                (entry_id,),
            ).fetchall()
            self.assertEqual(
                [(line["account_id"], line["debit"], line["credit"], line["inventory_item_id"]) for line in lines],
                [
                    (2, "2.50", "0", 42),
                    (1, "0", "2.50", 42),
                ],
            )
        finally:
            conn.close()

    def test_post_ebay_cogs_entry_skips_zero_amount(self) -> None:
        conn = _conn()
        try:
            entry_id = _post_ebay_cogs_entry(
                conn,
                order_id="ORDER-2",
                line_item_id="LINE-2",
                item_id=43,
                item_name="Sticker",
                sku=None,
                quantity_deducted=Decimal("0"),
                unit_cost=Decimal("1.25"),
            )
            self.assertIsNone(entry_id)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0],
                0,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
