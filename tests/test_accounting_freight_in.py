from __future__ import annotations

import sqlite3
import unittest
from datetime import date
from decimal import Decimal

from app.accounting.posting import JournalLineInput, PostEntryRequest
from app.accounting.routes import _apply_inventory_purchase, _resolve_inventory_purchase_context
from app.accounting.schemas import TransactionAnswerSet


def _inventory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_type TEXT NOT NULL,
          sku TEXT,
          name TEXT NOT NULL,
          qty_on_hand REAL NOT NULL DEFAULT 0,
          unit_cost REAL NOT NULL DEFAULT 0
        );
        """
    )
    return conn


class FreightInAllocationTests(unittest.TestCase):
    def test_create_new_inventory_purchase_allocates_freight_into_unit_cost(self) -> None:
        conn = _inventory_conn()
        try:
            tas = TransactionAnswerSet(
                template_id="BUY_INVENTORY_PERSONAL",
                total_amount=Decimal("15.00"),
                freight_in_amount=Decimal("3.00"),
                inventory_link={"mode": "create_new", "name": "Plushie", "quantity": 12},
            )
            ctx = _resolve_inventory_purchase_context(conn, tas)
            self.assertEqual(ctx["allocated_total_cost"], Decimal("15.00"))
            self.assertEqual(ctx["allocated_unit_cost"], Decimal("1.25"))

            req = PostEntryRequest(
                entry_date=date(2026, 5, 6),
                description="Bought inventory",
                template_id="BUY_INVENTORY_PERSONAL",
                total_amount=Decimal("15.00"),
                lines=[
                    JournalLineInput(account_id=1, debit=Decimal("15.00")),
                    JournalLineInput(account_id=2, credit=Decimal("15.00")),
                ],
            )
            item_id = _apply_inventory_purchase(conn, req, ctx)
            row = conn.execute(
                "SELECT name, qty_on_hand, unit_cost FROM items WHERE id=?",
                (item_id,),
            ).fetchone()
            self.assertEqual(row["name"], "Plushie")
            self.assertEqual(row["qty_on_hand"], 12.0)
            self.assertEqual(row["unit_cost"], 1.25)
            self.assertEqual({ln.inventory_item_id for ln in req.lines}, {item_id})
        finally:
            conn.close()

    def test_link_existing_inventory_purchase_updates_weighted_average_cost(self) -> None:
        conn = _inventory_conn()
        try:
            conn.execute(
                """
                INSERT INTO items (item_type, sku, name, qty_on_hand, unit_cost)
                VALUES ('resale', 'SKU-1', 'Plushie', 10, 1.00)
                """
            )
            tas = TransactionAnswerSet(
                template_id="BUY_INVENTORY",
                total_amount=Decimal("15.00"),
                freight_in_amount=Decimal("5.00"),
                payment_account_id=101,
                inventory_link={"mode": "link_existing", "item_id": 1, "quantity": 10},
            )
            ctx = _resolve_inventory_purchase_context(conn, tas)
            self.assertEqual(ctx["allocated_unit_cost"], Decimal("1.50"))
            self.assertEqual(ctx["weighted_unit_cost"], Decimal("1.25"))

            req = PostEntryRequest(
                entry_date=date(2026, 5, 6),
                description="Bought inventory",
                template_id="BUY_INVENTORY",
                total_amount=Decimal("15.00"),
                lines=[
                    JournalLineInput(account_id=1, debit=Decimal("15.00")),
                    JournalLineInput(account_id=2, credit=Decimal("15.00")),
                ],
            )
            item_id = _apply_inventory_purchase(conn, req, ctx)
            row = conn.execute(
                "SELECT qty_on_hand, unit_cost FROM items WHERE id=?",
                (item_id,),
            ).fetchone()
            self.assertEqual(row["qty_on_hand"], 20.0)
            self.assertEqual(row["unit_cost"], 1.25)
            self.assertEqual({ln.inventory_item_id for ln in req.lines}, {1})
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
