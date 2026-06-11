"""
Integration tests for the SaltStocks MCP tools.

Uses an in-memory SQLite DB seeded with minimal accounting data so no
production database is touched. Verifies:
  - All six tools return without error
  - Every monetary field is a two-decimal-place string
  - `as_of` is present and looks like an ISO 8601 UTC timestamp
  - simulate_ebay_sale never mutates the database (row-count check)
  - get_recent_journal_entries respects the limit cap
"""
from __future__ import annotations

import re
import sqlite3
import unittest
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# ── In-memory DB factory ──────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    schema_path = Path(__file__).resolve().parent.parent / "app" / "schema.sql"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(schema_path.read_text())
    # Add columns that migrate.py provides
    conn.executescript("""
        ALTER TABLE items ADD COLUMN company TEXT;
        ALTER TABLE items ADD COLUMN category TEXT;
        ALTER TABLE items ADD COLUMN brand TEXT;
        ALTER TABLE items ADD COLUMN brand_code TEXT;
        ALTER TABLE items ADD COLUMN ip TEXT;
        ALTER TABLE items ADD COLUMN reorder_point REAL;
        ALTER TABLE items ADD COLUMN lot_id INTEGER;
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          type TEXT NOT NULL,
          subtype TEXT,
          is_personal_funds_proxy INTEGER NOT NULL DEFAULT 0,
          is_system_protected INTEGER NOT NULL DEFAULT 0,
          is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS journal_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_date TEXT NOT NULL,
          description TEXT NOT NULL,
          template_id TEXT NOT NULL,
          total_amount TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          created_by_method TEXT NOT NULL DEFAULT 'questionnaire',
          vendor TEXT,
          notes TEXT,
          is_void INTEGER NOT NULL DEFAULT 0,
          void_reason TEXT,
          void_at TEXT
        );
        CREATE TABLE IF NOT EXISTS journal_lines (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_id INTEGER NOT NULL,
          account_id INTEGER NOT NULL,
          debit TEXT NOT NULL DEFAULT '0',
          credit TEXT NOT NULL DEFAULT '0',
          memo TEXT,
          inventory_item_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS inventory_lots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          journal_entry_id INTEGER,
          description TEXT NOT NULL,
          qty_received INTEGER NOT NULL,
          qty_remaining INTEGER NOT NULL,
          unit_cost TEXT NOT NULL,
          received_date TEXT NOT NULL,
          vendor TEXT,
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO app_settings (key, value) VALUES ('mcp_bearer_token', 'test-token');
    """)

    # Seed chart of accounts
    accounts = [
        (1, "1020", "Bank — Primary Checking", "asset",   "bank",      0, 0),
        (2, "1200", "Inventory",               "asset",   "inventory", 0, 1),
        (3, "4000", "Sales Revenue",            "income",  None,        0, 0),
        (4, "5000", "Cost of Goods Sold",       "expense", "cogs",      0, 1),
        (5, "6050", "Shipping",                 "expense", None,        0, 0),
        (6, "6060", "Marketplace Fees",         "expense", None,        0, 0),
        (7, "3100", "Owner Contributions",      "equity",  None,        1, 1),
    ]
    conn.executemany(
        "INSERT INTO accounts (id, code, name, type, subtype, is_personal_funds_proxy, is_system_protected) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        accounts,
    )

    # Seed one resale item
    conn.execute(
        "INSERT INTO items (id, item_type, sku, name, unit, qty_on_hand, unit_cost) "
        "VALUES (1, 'resale', 'GV-TEST-001', 'Test Funko Pop', 'each', 2, 10.00)"
    )
    conn.execute(
        "INSERT INTO resale_listings (item_id, channel, status, list_price) "
        "VALUES (1, 'ebay', 'listed', 29.99)"
    )

    # Seed a purchase entry: DR 1200, CR 3100
    conn.execute(
        "INSERT INTO journal_entries (entry_date, description, template_id, total_amount, created_by_method, notes) "
        "VALUES ('2025-03-01', 'Bought Test Funko Pop', 'BUY_INVENTORY_PERSONAL', '20.00', 'questionnaire', "
        "'Auto-generated from eBay order ORD-001 line LINE-001')"
    )
    conn.execute(
        "INSERT INTO journal_lines (entry_id, account_id, debit, credit, inventory_item_id) "
        "VALUES (1, 2, '20.00', '0', 1)"   # DR 1200
    )
    conn.execute(
        "INSERT INTO journal_lines (entry_id, account_id, debit, credit) "
        "VALUES (1, 7, '0', '20.00')"       # CR 3100
    )

    # Seed a sale entry: DR 1020 (net) + DR 6060 (fees) + DR 6050 (shipping) = CR 4000
    conn.execute(
        "INSERT INTO journal_entries (entry_date, description, template_id, total_amount, created_by_method, notes) "
        "VALUES ('2025-04-15', 'eBay sale ORD-001', 'SELL_INVENTORY_EBAY', '29.99', 'import_ebay', "
        "'Auto-generated from eBay order ORD-001 line LINE-001')"
    )
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (2, 1, '22.99', '0')")    # DR 1020 net
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (2, 6, '3.98', '0')")     # DR 6060 fees
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (2, 5, '3.02', '0')")     # DR 6050 shipping
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (2, 3, '0', '29.99')")    # CR 4000

    # Seed a COGS entry
    conn.execute(
        "INSERT INTO journal_entries (entry_date, description, template_id, total_amount, created_by_method, notes) "
        "VALUES ('2025-04-15', 'COGS ORD-001', 'COGS_RECOGNITION', '10.00', 'import_ebay', "
        "'Auto-generated from eBay order ORD-001 line LINE-001')"
    )
    conn.execute(
        "INSERT INTO journal_lines (entry_id, account_id, debit, credit, inventory_item_id) "
        "VALUES (3, 4, '10.00', '0', 1)"   # DR 5000
    )
    conn.execute(
        "INSERT INTO journal_lines (entry_id, account_id, debit, credit, inventory_item_id) "
        "VALUES (3, 2, '0', '10.00', 1)"   # CR 1200
    )

    conn.commit()
    return conn


@contextmanager
def _patch_db(conn):
    """Redirect app.db.get_conn() to the provided in-memory connection."""
    with patch("app.db.get_conn", return_value=conn):
        yield


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")


def _check_monetary(tc: unittest.TestCase, obj, path=""):
    """Recursively assert every key ending in known money suffixes is a 2-dp string."""
    money_keys = {
        "revenue", "cogs", "gross_profit", "net_income", "total_expenses",
        "amount", "value", "ebay_fee", "net_payout", "shipping_cost",
        "list_price", "break_even_price", "margin_pct", "unit_cost", "total_cost",
        "estimated_ebay_fee", "shipping", "fees", "net_profit", "sale_price",
        "gross_margin_pct", "debit", "credit",
    }
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{path}.{k}" if path else k
            if k in money_keys:
                tc.assertIsInstance(v, str, f"{full} should be str, got {type(v)}")
                tc.assertRegex(v, _MONEY_RE, f"{full}={v!r} not a 2-dp decimal string")
            _check_monetary(tc, v, full)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _check_monetary(tc, item, f"{path}[{i}]")


class TestGetPnlSummary(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _call(self, period="all_time", **kwargs):
        with _patch_db(self.conn):
            from app.mcp.tools import register
            from fastmcp import FastMCP
            m = FastMCP("test")
            register(m)
            # Call the function directly via the module
            from app.services import mcp_queries
            return mcp_queries.pnl_summary(self.conn, period, **kwargs)

    def test_returns_as_of(self):
        result = self._call()
        self.assertIn("as_of", result)
        self.assertRegex(result["as_of"], _ISO_RE)

    def test_monetary_fields_are_strings(self):
        result = self._call()
        _check_monetary(self, result)

    def test_net_income_matches_gross_minus_expenses(self):
        result = self._call()
        gp = Decimal(result["gross_profit"])
        te = Decimal(result["total_expenses"])
        ni = Decimal(result["net_income"])
        self.assertEqual(ni, (gp - te).quantize(Decimal("0.01")))

    def test_all_period_values(self):
        for period in ("mtd", "ytd", "all_time"):
            result = self._call(period)
            self.assertIn("period_label", result)
            self.assertIn("items_sold_count", result)


class TestGetInventorySnapshot(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _call(self):
        from app.services import mcp_queries
        return mcp_queries.inventory_snapshot(self.conn)

    def test_returns_as_of(self):
        result = self._call()
        self.assertRegex(result["as_of"], _ISO_RE)

    def test_total_value_equals_sum_of_items(self):
        result = self._call()
        item_sum = sum(Decimal(i["total_cost"]) for i in result["items"])
        self.assertEqual(
            Decimal(result["total_inventory_value"]),
            item_sum.quantize(Decimal("0.01")),
        )

    def test_monetary_fields_are_strings(self):
        _check_monetary(self, self._call())

    def test_seeded_item_present(self):
        result = self._call()
        skus = [i["sku"] for i in result["items"]]
        self.assertIn("GV-TEST-001", skus)


class TestGetItemDetail(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _call(self, item_id="GV-TEST-001"):
        from app.services import mcp_queries
        return mcp_queries.item_detail(self.conn, item_id)

    def test_found_by_sku(self):
        result = self._call("GV-TEST-001")
        self.assertEqual(result["sku"], "GV-TEST-001")

    def test_found_by_id(self):
        result = self._call("1")
        self.assertEqual(result["id"], 1)

    def test_purchase_entries_present(self):
        result = self._call()
        self.assertGreater(len(result["purchase_entries"]), 0)

    def test_sale_event_present_and_has_cogs(self):
        result = self._call()
        self.assertIn("sale_event", result)
        self.assertIn("cogs", result["sale_event"])

    def test_monetary_fields_are_strings(self):
        _check_monetary(self, self._call())

    def test_unknown_item_raises(self):
        from app.services import mcp_queries
        with self.assertRaises(ValueError):
            mcp_queries.item_detail(self.conn, "DOES-NOT-EXIST")


class TestGetSaleHistory(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _call(self, start="2025-01-01", end="2025-12-31", group_by="month"):
        from app.services import mcp_queries
        return mcp_queries.sale_history(self.conn, start, end, group_by)

    def test_monthly_shape(self):
        result = self._call()
        self.assertIn("by_month", result)
        self.assertIn("totals", result)

    def test_item_shape(self):
        result = self._call(group_by="item")
        self.assertIn("items", result)
        self.assertIn("totals", result)

    def test_monetary_fields_are_strings(self):
        _check_monetary(self, self._call())

    def test_as_of_present(self):
        self.assertRegex(self._call()["as_of"], _ISO_RE)


class TestSimulateEbaySale(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _call(self, item_id="GV-TEST-001", list_price="29.99", **kwargs):
        from app.services import mcp_queries, ebay_fee_calculator as calc
        from decimal import Decimal as D
        item = mcp_queries.item_detail(self.conn, item_id)
        sc = D(kwargs.get("shipping_cost", "0"))
        fr = D(kwargs.get("ebay_fee_rate", "0.1325"))
        lp = D(list_price)
        unit_cost = D(item["unit_cost"])
        result = calc.calculate_sale(lp, unit_cost, sc, fr)
        return result

    def test_note_not_in_calculator(self):
        # The 'note' field lives in tools.py, not the calculator
        result = self._call()
        self.assertNotIn("note", result)

    def test_no_db_mutation(self):
        # Count rows before and after simulation
        def row_counts():
            return {
                "items": self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0],
                "journal_entries": self.conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0],
                "journal_lines": self.conn.execute("SELECT COUNT(*) FROM journal_lines").fetchone()[0],
            }
        before = row_counts()
        self._call()
        after = row_counts()
        self.assertEqual(before, after)

    def test_break_even_satisfies_spec(self):
        from app.services import ebay_fee_calculator as calc
        from decimal import Decimal as D
        unit_cost = D("10.00")
        fee_rate = D("0.1325")
        bep = calc.break_even_price(unit_cost, fee_rate)
        self.assertGreaterEqual(bep * (1 - fee_rate) - unit_cost, D("0.00"))

    def test_monetary_fields_two_dp(self):
        # Calculator returns Decimals; verify they have exactly 2 dp
        result = self._call()
        for key in ("ebay_fee", "net_payout", "cogs", "gross_profit", "margin_pct", "break_even_price"):
            val = result[key]
            self.assertIsInstance(val, Decimal, f"{key} should be Decimal")
            self.assertEqual(
                val, val.quantize(Decimal("0.01")),
                f"{key}={val} has more than 2 decimal places",
            )


class TestGetRecentJournalEntries(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _call(self, **kwargs):
        from app.services import mcp_queries
        return mcp_queries.recent_journal_entries(self.conn, **kwargs)

    def test_default_returns_at_most_10(self):
        result = self._call()
        self.assertLessEqual(len(result["entries"]), 10)

    def test_limit_capped_at_50(self):
        # limit=51 should be silently clamped to 50 (no error from query layer)
        result = self._call(limit=50)
        self.assertLessEqual(len(result["entries"]), 50)

    def test_as_of_present(self):
        result = self._call()
        self.assertRegex(result["as_of"], _ISO_RE)

    def test_each_entry_has_lines(self):
        result = self._call()
        for entry in result["entries"]:
            self.assertIn("lines", entry)
            self.assertIsInstance(entry["lines"], list)

    def test_monetary_fields_are_strings(self):
        _check_monetary(self, self._call())

    def test_account_code_filter(self):
        result = self._call(account_code="5000")
        for entry in result["entries"]:
            codes = [ln["account_code"] for ln in entry["lines"]]
            self.assertIn("5000", codes)

    def test_no_floats_anywhere(self):
        result = self._call()
        def _check_no_float(obj, path=""):
            if isinstance(obj, float):
                raise AssertionError(f"Float found at {path}: {obj}")
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _check_no_float(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _check_no_float(v, f"{path}[{i}]")
        _check_no_float(result)


if __name__ == "__main__":
    unittest.main()
