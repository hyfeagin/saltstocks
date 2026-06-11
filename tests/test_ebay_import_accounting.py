from __future__ import annotations

import json
import sqlite3
import unittest
from decimal import Decimal
from pathlib import Path

from app.routers.ebay import _extract_line_candidates, _post_ebay_cogs_entry, _post_ebay_revenue_entry


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
            (1, "1020", "Bank — Primary Checking"),
            (2, "1200", "Inventory"),
            (3, "4000", "Sales Revenue"),
            (4, "5000", "Cost of Goods Sold"),
            (5, "6050", "Shipping"),
            (6, "6060", "Marketplace Fees"),
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
                    (4, "2.50", "0", 42),   # 5000 COGS debit
                    (2, "0", "2.50", 42),   # 1200 Inventory credit
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


class EbayRevenueEntryTests(unittest.TestCase):
    def test_post_ebay_revenue_entry_full(self) -> None:
        """Revenue entry creates correct credit to 4000, debits to 6060/6050/1020."""
        conn = _conn()
        try:
            entry_id = _post_ebay_revenue_entry(
                conn,
                order_id="ORDER-10",
                line_item_id="LINE-10",
                item_name="Sneakers",
                sku="SHOE-001",
                sale_price=Decimal("50.00"),
                transaction_fees=Decimal("7.50"),
                shipping_cost=Decimal("5.00"),
            )
            self.assertIsNotNone(entry_id)
            entry = conn.execute(
                "SELECT template_id, total_amount, created_by_method FROM journal_entries WHERE id=?",
                (entry_id,),
            ).fetchone()
            self.assertEqual(entry["template_id"], "EBAY_SALE")
            self.assertEqual(entry["total_amount"], "50.00")
            self.assertEqual(entry["created_by_method"], "import_ebay")

            lines = conn.execute(
                "SELECT account_id, debit, credit, memo FROM journal_lines WHERE entry_id=? ORDER BY id",
                (entry_id,),
            ).fetchall()
            # Revenue credit to 4000, fees debit to 6060, shipping debit to 6050, net payout debit to 1020
            self.assertEqual(
                [(ln["account_id"], ln["debit"], ln["credit"]) for ln in lines],
                [
                    (3, "0", "50.00"),   # 4000 Revenue credit
                    (6, "7.50", "0"),    # 6060 Marketplace Fees debit
                    (5, "5.00", "0"),    # 6050 Shipping debit
                    (1, "37.50", "0"),   # 1020 Cash net payout debit (50 - 7.50 - 5.00)
                ],
            )
        finally:
            conn.close()

    def test_post_ebay_revenue_entry_no_fees_no_shipping(self) -> None:
        """Revenue entry with zero fees and shipping debits full amount to cash."""
        conn = _conn()
        try:
            entry_id = _post_ebay_revenue_entry(
                conn,
                order_id="ORDER-11",
                line_item_id="LINE-11",
                item_name="Widget",
                sku=None,
                sale_price=Decimal("20.00"),
                transaction_fees=Decimal("0"),
                shipping_cost=Decimal("0"),
            )
            self.assertIsNotNone(entry_id)
            lines = conn.execute(
                "SELECT account_id, debit, credit FROM journal_lines WHERE entry_id=? ORDER BY id",
                (entry_id,),
            ).fetchall()
            # Only revenue credit and net payout debit (no fee or shipping lines)
            self.assertEqual(
                [(ln["account_id"], ln["debit"], ln["credit"]) for ln in lines],
                [
                    (3, "0", "20.00"),   # 4000 Revenue credit
                    (1, "20.00", "0"),   # 1020 Cash debit
                ],
            )
        finally:
            conn.close()

    def test_post_ebay_revenue_entry_skips_zero_sale_price(self) -> None:
        conn = _conn()
        try:
            entry_id = _post_ebay_revenue_entry(
                conn,
                order_id="ORDER-12",
                line_item_id="LINE-12",
                item_name="Ghost",
                sku=None,
                sale_price=Decimal("0"),
                transaction_fees=Decimal("0"),
                shipping_cost=Decimal("0"),
            )
            self.assertIsNone(entry_id)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0], 0)
        finally:
            conn.close()


class EbayOrderExtractionTests(unittest.TestCase):
    """Integration tests using a real (sanitized) eBay order API response fixture."""

    FIXTURE = Path(__file__).parent / "fixtures" / "funko_order.json"

    def _order(self):
        with open(self.FIXTURE) as f:
            return json.load(f)["orders"][0]

    def test_sale_price_equals_item_plus_buyer_shipping(self) -> None:
        """sale_price = lineItemCost + pricingSummary.deliveryCost (tax excluded)."""
        candidates = _extract_line_candidates(self._order())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["sale_price"], Decimal("19.19"))

    def test_fees_read_from_total_marketplace_fee(self) -> None:
        """transaction_fees comes from order-level totalMarketplaceFee, not pricingSummary."""
        candidates = _extract_line_candidates(self._order())
        self.assertEqual(candidates[0]["transaction_fees"], Decimal("3.11"))

    def test_shipping_cost_is_label_not_buyer_shipping(self) -> None:
        """shipping_cost is the seller's label (lineItem.deliveryCost.shippingCost)."""
        candidates = _extract_line_candidates(self._order())
        self.assertEqual(candidates[0]["shipping_cost"], Decimal("6.69"))

    def test_net_payout_math(self) -> None:
        """sale_price - fees - label = order earnings reported by eBay ($9.39)."""
        c = _extract_line_candidates(self._order())[0]
        net = (c["sale_price"] - c["transaction_fees"] - c["shipping_cost"]).quantize(Decimal("0.01"))
        self.assertEqual(net, Decimal("9.39"))

    def test_sku_and_order_id_extracted(self) -> None:
        c = _extract_line_candidates(self._order())[0]
        self.assertEqual(c["order_id"], "04-14734-54875")
        self.assertEqual(c["sku"], "GV-MISC-000019")


if __name__ == "__main__":
    unittest.main()
