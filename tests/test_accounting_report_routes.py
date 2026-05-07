from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request
from starlette.templating import _TemplateResponse

from app.accounting.routes import (
    _balance_sheet_csv_rows,
    _build_year_end_export_zip,
    _expenses_by_category_csv_rows,
    _ledger_csv_rows,
    _pnl_csv_rows,
    _sales_tax_csv_rows,
    receipts_index_page,
    report_balance_sheet,
    report_expenses_by_category,
    report_general_ledger,
    report_profit_and_loss,
    report_sales_tax,
    report_year_end_export,
)
from app.accounting.reports import balance_sheet, expenses_by_category, general_ledger, profit_and_loss, sales_tax_summary


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
            (6, "2100", "Sales Tax Payable", "liability", "tax"),
        ],
    )
    conn.executemany(
        "INSERT INTO journal_entries (id, entry_date, description, template_id, total_amount, is_void) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "2026-01-01", "Opening contribution", "OWNER_CONTRIBUTION", "100.00", 0),
            (2, "2026-01-05", "Travel purchase", "TRAVEL_HOTEL", "20.00", 0),
            (3, "2026-01-10", "Sale", "SELL_INVENTORY_CASH", "55.00", 0),
            (4, "2026-01-10", "Auto COGS", "COGS_RECOGNITION", "10.00", 0),
            (5, "2026-01-18", "Tax payment", "SALES_TAX_REMITTED", "2.00", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO journal_lines (id, entry_id, account_id, debit, credit, memo) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, "100.00", "0.00", None),
            (2, 1, 5, "0.00", "100.00", None),
            (3, 2, 4, "20.00", "0.00", None),
            (4, 2, 1, "0.00", "20.00", None),
            (5, 3, 1, "55.00", "0.00", None),
            (6, 3, 2, "0.00", "50.00", None),
            (7, 4, 3, "10.00", "0.00", None),
            (8, 4, 1, "0.00", "10.00", None),
            (9, 3, 6, "0.00", "5.00", None),
            (10, 5, 6, "2.00", "0.00", None),
        ],
    )
    return conn


def _receipts_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE journal_entries (
          id INTEGER PRIMARY KEY,
          entry_date TEXT NOT NULL,
          description TEXT NOT NULL,
          template_id TEXT NOT NULL,
          total_amount TEXT NOT NULL,
          vendor TEXT,
          is_void INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE receipts (
          id INTEGER PRIMARY KEY,
          entry_id INTEGER NOT NULL,
          original_filename TEXT NOT NULL,
          stored_path TEXT NOT NULL,
          mime_type TEXT NOT NULL,
          file_size_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          uploaded_at TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO journal_entries (id, entry_date, description, template_id, total_amount, vendor, is_void)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "2026-01-05", "Travel purchase", "TRAVEL_HOTEL", "20.00", "Hotel", 0),
            (2, "2026-01-10", "Sale", "SELL_INVENTORY_CASH", "55.00", "Buyer", 0),
            (3, "2026-01-18", "Tax payment", "SALES_TAX_REMITTED", "2.00", None, 0),
        ],
    )
    conn.executemany(
        """
        INSERT INTO receipts (id, entry_id, original_filename, stored_path, mime_type, file_size_bytes, sha256, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, "hotel.pdf", "data/receipts/2026/01/hotel.pdf", "application/pdf", 1024, "abc", "2026-01-05T10:00:00"),
            (2, 2, "sale.jpg", "data/receipts/2026/01/sale.jpg", "image/jpeg", 2048, "def", "2026-01-10T12:00:00"),
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
            self.assertEqual(response.context["report"]["total_assets"].__str__(), "125.00")
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
            self.assertIn("Total Assets,125.00", body)
            self.assertIn("Total Liabilities + Equity,123.00", body)
        finally:
            conn.close()

    def test_balance_sheet_csv_rows_include_section_totals(self) -> None:
        conn = _reports_conn()
        try:
            report = balance_sheet(conn, as_of="2026-01-31")
            rows = _balance_sheet_csv_rows(report)
            self.assertIn(["", "Total Assets", "125.00"], rows)
            self.assertIn(["", "Total Liabilities", "3.00"], rows)
            self.assertIn(["", "Total Liabilities + Equity", "123.00"], rows)
        finally:
            conn.close()


class ExpensesByCategoryRouteTests(unittest.TestCase):
    def test_expenses_by_category_route_renders_html_report(self) -> None:
        conn = _reports_conn()
        try:
            response = report_expenses_by_category(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                format="html",
                conn=conn,
            )
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/report_expenses_by_category.html")
            self.assertEqual(response.context["report"]["total_expenses"].__str__(), "30.00")
        finally:
            conn.close()

    def test_expenses_by_category_route_returns_csv_download(self) -> None:
        conn = _reports_conn()
        try:
            response = report_expenses_by_category(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                format="csv",
                conn=conn,
            )
            self.assertEqual(response.media_type, "text/csv; charset=utf-8")
            self.assertIn("attachment; filename=", response.headers["content-disposition"])
            body = response.body.decode()
            self.assertIn("Expenses by Category", body)
            self.assertIn("Total Expenses,30.00", body)
            self.assertIn("6020,Travel,20.00", body)
        finally:
            conn.close()

    def test_expenses_by_category_csv_rows_include_total(self) -> None:
        conn = _reports_conn()
        try:
            report = expenses_by_category(conn, from_date="2026-01-01", to_date="2026-01-31")
            rows = _expenses_by_category_csv_rows(report)
            self.assertIn(["Account Code", "Category", "Amount"], rows)
            self.assertIn(["6020", "Travel", "20.00"], rows)
            self.assertIn(["", "Total Expenses", "30.00"], rows)
        finally:
            conn.close()


class GeneralLedgerRouteTests(unittest.TestCase):
    def test_general_ledger_route_renders_html_report(self) -> None:
        conn = _reports_conn()
        try:
            response = report_general_ledger(
                account_id=1,
                request=_request(),
                from_date="2026-01-05",
                to_date="2026-01-31",
                format="html",
                conn=conn,
            )
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/report_ledger.html")
            self.assertEqual(response.context["report"]["opening_balance"].__str__(), "100.00")
            self.assertEqual(response.context["report"]["closing_balance"].__str__(), "125.00")
        finally:
            conn.close()

    def test_general_ledger_route_returns_csv_download(self) -> None:
        conn = _reports_conn()
        try:
            response = report_general_ledger(
                account_id=1,
                request=_request(),
                from_date="2026-01-05",
                to_date="2026-01-31",
                format="csv",
                conn=conn,
            )
            self.assertEqual(response.media_type, "text/csv; charset=utf-8")
            self.assertIn("attachment; filename=", response.headers["content-disposition"])
            body = response.body.decode()
            self.assertIn("General Ledger / Account Activity", body)
            self.assertIn("Opening Balance,100.00", body)
            self.assertIn("Closing Balance,,,", body)
        finally:
            conn.close()

    def test_ledger_csv_rows_include_running_balances(self) -> None:
        conn = _reports_conn()
        try:
            report = general_ledger(conn, account_id=1, from_date="2026-01-05", to_date="2026-01-31")
            rows = _ledger_csv_rows(report)
            self.assertIn(["From", "2026-01-05"], rows)
            self.assertIn(["To", "2026-01-31"], rows)
            self.assertIn(["Opening Balance", "100.00"], rows)
            self.assertIn(["2026-01-10", 3, "SELL_INVENTORY_CASH", "Sale", "", "55.00", "0.00", "135.00"], rows)
        finally:
            conn.close()


class SalesTaxRouteTests(unittest.TestCase):
    def test_sales_tax_route_renders_html_report(self) -> None:
        conn = _reports_conn()
        try:
            response = report_sales_tax(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                format="html",
                conn=conn,
            )
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/report_sales_tax.html")
            self.assertEqual(response.context["report"]["collected"].__str__(), "5.00")
            self.assertEqual(response.context["report"]["remitted"].__str__(), "2.00")
            self.assertEqual(response.context["report"]["net_liability"].__str__(), "3.00")
        finally:
            conn.close()


class ReceiptsIndexRouteTests(unittest.TestCase):
    def test_receipts_index_renders_entries_with_and_without_receipts(self) -> None:
        conn = _receipts_conn()
        try:
            response = receipts_index_page(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                template_id="",
                receipt_status="all",
                conn=conn,
            )
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/receipts_index.html")
            items = response.context["report"]["items"]
            self.assertEqual([item["entry_id"] for item in items], [3, 2, 1])
            self.assertFalse(items[0]["has_receipt"])
            self.assertTrue(items[1]["primary_receipt"]["is_image"])
            self.assertTrue(items[2]["primary_receipt"]["is_pdf"])
        finally:
            conn.close()

    def test_receipts_index_filters_missing_receipts_by_template(self) -> None:
        conn = _receipts_conn()
        try:
            response = receipts_index_page(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                template_id="SALES_TAX_REMITTED",
                receipt_status="missing_receipt",
                conn=conn,
            )
            items = response.context["report"]["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["entry_id"], 3)
            self.assertFalse(items[0]["has_receipt"])
        finally:
            conn.close()

    def test_sales_tax_route_returns_csv_download(self) -> None:
        conn = _reports_conn()
        try:
            response = report_sales_tax(
                request=_request(),
                from_date="2026-01-01",
                to_date="2026-01-31",
                format="csv",
                conn=conn,
            )
            self.assertEqual(response.media_type, "text/csv; charset=utf-8")
            self.assertIn("attachment; filename=", response.headers["content-disposition"])
            body = response.body.decode()
            self.assertIn("Sales Tax Summary", body)
            self.assertIn("Collected,5.00", body)
            self.assertIn("Remitted,2.00", body)
            self.assertIn("Net Liability,3.00", body)
        finally:
            conn.close()

    def test_sales_tax_csv_rows_include_summary_totals(self) -> None:
        conn = _reports_conn()
        try:
            report = sales_tax_summary(conn, from_date="2026-01-01", to_date="2026-01-31")
            rows = _sales_tax_csv_rows(report)
            self.assertIn(["Metric", "Amount"], rows)
            self.assertIn(["Collected", "5.00"], rows)
            self.assertIn(["Remitted", "2.00"], rows)
            self.assertIn(["Net Liability", "3.00"], rows)
        finally:
            conn.close()


class YearEndExportRouteTests(unittest.TestCase):
    def test_year_end_export_zip_contains_reports_receipts_and_db_snapshot(self) -> None:
        conn = _reports_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE receipts (
                  id INTEGER PRIMARY KEY,
                  entry_id INTEGER NOT NULL,
                  original_filename TEXT NOT NULL,
                  stored_path TEXT NOT NULL
                );
                """
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                base_dir = Path(tmpdir)
                receipt_rel = Path("data/receipts/2026/01/receipt.pdf")
                receipt_abs = base_dir / receipt_rel
                receipt_abs.parent.mkdir(parents=True, exist_ok=True)
                receipt_abs.write_bytes(b"pdf-data")
                snapshot_path = base_dir / "saltstocks.db"
                snapshot_path.write_bytes(b"sqlite-snapshot")

                conn.execute(
                    "INSERT INTO receipts (id, entry_id, original_filename, stored_path) VALUES (1, 2, 'hotel.pdf', ?)",
                    (str(receipt_rel),),
                )

                payload = _build_year_end_export_zip(
                    conn,
                    year=2026,
                    base_dir=base_dir,
                    db_path=snapshot_path,
                )

                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    names = set(zf.namelist())
                    self.assertIn("reports/profit-and-loss_2026.csv", names)
                    self.assertIn("reports/expenses-by-category_2026.csv", names)
                    self.assertIn("reports/general-ledger_2026.csv", names)
                    self.assertIn("receipts/01/1_hotel.pdf", names)
                    self.assertIn("database/saltstocks_2026.db", names)
                    self.assertEqual(zf.read("database/saltstocks_2026.db"), b"sqlite-snapshot")
        finally:
            conn.close()

    def test_year_end_export_route_returns_zip_response(self) -> None:
        conn = _reports_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE receipts (
                  id INTEGER PRIMARY KEY,
                  entry_id INTEGER NOT NULL,
                  original_filename TEXT NOT NULL,
                  stored_path TEXT NOT NULL
                );
                """
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                base_dir = Path(tmpdir)
                snapshot_path = base_dir / "saltstocks.db"
                snapshot_path.write_bytes(b"sqlite-snapshot")

                with patch("app.accounting.routes.BASE_DIR", base_dir), patch("app.accounting.routes.DB_PATH", snapshot_path):
                    response = report_year_end_export(year=2026, conn=conn)
                    self.assertEqual(response.media_type, "application/zip")
                    self.assertIn("year-end-export_2026.zip", response.headers["content-disposition"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
