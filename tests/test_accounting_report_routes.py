from __future__ import annotations

import sqlite3
import unittest

from starlette.requests import Request
from starlette.templating import _TemplateResponse

from app.accounting.routes import (
    _balance_sheet_csv_rows,
    _pnl_csv_rows,
    report_balance_sheet,
    report_profit_and_loss,
)
from app.accounting.reports import balance_sheet, profit_and_loss


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
            (2, "4000", "Sales Revenue", "income", None),
            (3, "5000", "Cost of Goods Sold", "expense", "cogs"),
            (4, "6020", "Travel", "expense", None),
            (5, "3000", "Owner's Equity", "equity", None),
        ],
    )
    conn.executemany(
        "INSERT INTO journal_entries (id, entry_date, description, template_id, total_amount, is_void) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "2026-01-01", "Opening contribution", "OWNER_CONTRIBUTION", "100.00", 0),
            (2, "2026-01-05", "Travel purchase", "TRAVEL_HOTEL", "20.00", 0),
            (3, "2026-01-10", "Sale", "SELL_INVENTORY_CASH", "50.00", 0),
            (4, "2026-01-10", "Auto COGS", "COGS_RECOGNITION", "10.00", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO journal_lines (id, entry_id, account_id, debit, credit, memo) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, "100.00", "0.00", None),
            (2, 1, 5, "0.00", "100.00", None),
            (3, 2, 4, "20.00", "0.00", None),
            (4, 2, 1, "0.00", "20.00", None),
            (5, 3, 1, "50.00", "0.00", None),
            (6, 3, 2, "0.00", "50.00", None),
            (7, 4, 3, "10.00", "0.00", None),
            (8, 4, 1, "0.00", "10.00", None),
        ],
    )
    return conn


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/accounting/reports/pnl", "headers": []})


class ProfitAndLossRouteTests(unittest.TestCase):
    def test_pnl_route_renders_html_report(self) -> None:
        conn = _reports_conn()
        try:
            response = report_profit_and_loss(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                format="html",
                conn=conn,
            )
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/report_pnl.html")
            self.assertEqual(response.context["report"]["net_income"].__str__(), "20.00")
        finally:
            conn.close()

    def test_pnl_route_returns_csv_download(self) -> None:
        conn = _reports_conn()
        try:
            response = report_profit_and_loss(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                format="csv",
                conn=conn,
            )
            self.assertEqual(response.media_type, "text/csv; charset=utf-8")
            self.assertIn("attachment; filename=", response.headers["content-disposition"])
            body = response.body.decode()
            self.assertIn("Profit & Loss", body)
            self.assertIn("Net Income,20.00", body)
        finally:
            conn.close()

    def test_pnl_csv_rows_include_totals(self) -> None:
        conn = _reports_conn()
        try:
            report = profit_and_loss(conn, from_date="2026-01-01", to_date="2026-01-31")
            rows = _pnl_csv_rows(report)
            self.assertIn(["", "Total Revenue", "50.00"], rows)
            self.assertIn(["", "Gross Profit", "40.00"], rows)
            self.assertIn(["", "Net Income", "20.00"], rows)
        finally:
            conn.close()

class BalanceSheetRouteTests(unittest.TestCase):
    def test_balance_sheet_route_renders_html_report(self) -> None:
        conn = _reports_conn()
        try:
            response = report_balance_sheet(
                request=_request(),
                asof="2026-01-31",
                format="html",
                conn=conn,
            )
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/report_balance_sheet.html")
            self.assertEqual(response.context["report"]["total_assets"].__str__(), "120.00")
        finally:
            conn.close()

    def test_balance_sheet_route_returns_csv_download(self) -> None:
        conn = _reports_conn()
        try:
            response = report_balance_sheet(
                request=_request(),
                asof="2026-01-31",
                format="csv",
                conn=conn,
            )
            self.assertEqual(response.media_type, "text/csv; charset=utf-8")
            self.assertIn("attachment; filename=", response.headers["content-disposition"])
            body = response.body.decode()
            self.assertIn("Balance Sheet", body)
            self.assertIn("Total Assets,120.00", body)
            self.assertIn("Total Liabilities + Equity,120.00", body)
        finally:
            conn.close()

    def test_balance_sheet_csv_rows_include_section_totals(self) -> None:
        conn = _reports_conn()
        try:
            report = balance_sheet(conn, as_of="2026-01-31")
            rows = _balance_sheet_csv_rows(report)
            self.assertIn(["", "Total Assets", "120.00"], rows)
            self.assertIn(["", "Total Liabilities", "0.00"], rows)
            self.assertIn(["", "Total Liabilities + Equity", "120.00"], rows)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
