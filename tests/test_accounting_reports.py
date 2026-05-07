from __future__ import annotations

import sqlite3
import unittest

from app.accounting.reports import balance_sheet, expenses_by_category, general_ledger, profit_and_loss


def _reports_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL,
          name TEXT NOT NULL,
          type TEXT NOT NULL,
          subtype TEXT
        );
        CREATE TABLE journal_entries (
          id INTEGER PRIMARY KEY,
          entry_date TEXT NOT NULL,
          description TEXT NOT NULL,
          template_id TEXT NOT NULL,
          total_amount TEXT NOT NULL,
          is_void INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE journal_lines (
          id INTEGER PRIMARY KEY,
          entry_id INTEGER NOT NULL,
          account_id INTEGER NOT NULL,
          debit TEXT NOT NULL,
          credit TEXT NOT NULL,
          memo TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO accounts (id, code, name, type, subtype) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "1020", "Bank", "asset", "bank"),
            (2, "1200", "Inventory", "asset", "inventory"),
            (3, "2010", "Credit Card", "liability", "credit_card"),
            (4, "2100", "Sales Tax Payable", "liability", "tax"),
            (5, "3000", "Owner's Equity", "equity", None),
            (6, "4000", "Sales Revenue", "income", None),
            (7, "4100", "Other Income", "income", None),
            (8, "5000", "Cost of Goods Sold", "expense", "cogs"),
            (9, "6020", "Travel", "expense", None),
        ],
    )
    conn.executemany(
        "INSERT INTO journal_entries (id, entry_date, description, template_id, total_amount, is_void) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "2026-01-01", "Opening contribution", "OWNER_CONTRIBUTION", "100.00", 0),
            (2, "2026-01-05", "Travel purchase", "TRAVEL_HOTEL", "20.00", 0),
            (3, "2026-01-10", "Sale", "SELL_INVENTORY_CASH", "55.00", 0),
            (4, "2026-01-10", "Auto COGS", "COGS_RECOGNITION", "10.00", 0),
            (5, "2026-01-20", "Rebate", "OTHER_INCOME", "5.00", 0),
            (6, "2026-01-25", "Voided sample", "OTHER_INCOME", "99.00", 1)
        ],
    )
    conn.executemany(
        "INSERT INTO journal_lines (id, entry_id, account_id, debit, credit, memo) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, "100.00", "0.00", None),
            (2, 1, 5, "0.00", "100.00", None),
            (3, 2, 9, "20.00", "0.00", None),
            (4, 2, 1, "0.00", "20.00", None),
            (5, 3, 1, "55.00", "0.00", None),
            (6, 3, 6, "0.00", "50.00", None),
            (7, 3, 4, "0.00", "5.00", None),
            (8, 4, 8, "10.00", "0.00", None),
            (9, 4, 2, "0.00", "10.00", None),
            (10, 5, 1, "5.00", "0.00", None),
            (11, 5, 7, "0.00", "5.00", None),
            (12, 6, 1, "99.00", "0.00", None),
            (13, 6, 7, "0.00", "99.00", None)
        ],
    )
    return conn


class AccountingReportsTests(unittest.TestCase):
    def test_profit_and_loss_groups_revenue_cogs_and_expenses(self) -> None:
        conn = _reports_conn()
        try:
            report = profit_and_loss(conn, from_date="2026-01-01", to_date="2026-01-31")
            self.assertEqual(str(report["total_revenue"]), "55.00")
            self.assertEqual(str(report["total_cogs"]), "10.00")
            self.assertEqual(str(report["total_expenses"]), "20.00")
            self.assertEqual(str(report["gross_profit"]), "45.00")
            self.assertEqual(str(report["net_income"]), "25.00")
        finally:
            conn.close()

    def test_balance_sheet_uses_non_void_entries_only(self) -> None:
        conn = _reports_conn()
        try:
            report = balance_sheet(conn, as_of="2026-01-31")
            self.assertEqual(str(report["total_assets"]), "130.00")
            self.assertEqual(str(report["total_liabilities"]), "5.00")
            self.assertEqual(str(report["total_equity"]), "125.00")
            self.assertEqual(str(report["total_liabilities_and_equity"]), "130.00")
        finally:
            conn.close()

    def test_expenses_by_category_includes_cogs_and_travel(self) -> None:
        conn = _reports_conn()
        try:
            report = expenses_by_category(conn, from_date="2026-01-01", to_date="2026-01-31")
            names = [line.account_name for line in report["categories"]]
            self.assertEqual(names, ["Cost of Goods Sold", "Travel"])
            self.assertEqual(str(report["total_expenses"]), "30.00")
        finally:
            conn.close()

    def test_general_ledger_returns_running_balance(self) -> None:
        conn = _reports_conn()
        try:
            report = general_ledger(conn, account_id=1, from_date="2026-01-05", to_date="2026-01-31")
            self.assertEqual(str(report["opening_balance"]), "100.00")
            self.assertEqual(str(report["closing_balance"]), "140.00")
            self.assertEqual([str(line.running_balance) for line in report["lines"]], ["80.00", "135.00", "140.00"])
        finally:
            conn.close()
