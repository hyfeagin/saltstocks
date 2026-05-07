from __future__ import annotations

import sqlite3
import unittest

from starlette.requests import Request
from starlette.templating import _TemplateResponse

from app.accounting.routes import sales_tax_settings_page, sales_tax_settings_submit


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sales_tax_rates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          jurisdiction TEXT NOT NULL,
          rate TEXT NOT NULL,
          is_default INTEGER NOT NULL DEFAULT 0,
          effective_from TEXT NOT NULL,
          effective_to TEXT
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO sales_tax_rates (jurisdiction, rate, is_default, effective_from, effective_to)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("North Carolina", "0.0475", 1, "2024-01-01", None),
            ("South Carolina", "0.0600", 0, "2024-01-01", None),
        ],
    )
    return conn


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/accounting/settings/sales-tax", "headers": []})


class SalesTaxSettingsRouteTests(unittest.TestCase):
    def test_settings_page_renders_default_jurisdiction(self) -> None:
        conn = _conn()
        try:
            response = sales_tax_settings_page(request=_request(), conn=conn)
            self.assertIsInstance(response, _TemplateResponse)
            self.assertEqual(response.template.name, "accounting/settings_sales_tax.html")
            rates = response.context["rates"]
            self.assertEqual(rates[0]["jurisdiction"], "North Carolina")
            self.assertTrue(rates[0]["is_default"])
            self.assertEqual(str(rates[0]["rate_percent"]), "4.75")
        finally:
            conn.close()

    def test_add_rate_can_replace_default_jurisdiction(self) -> None:
        conn = _conn()
        try:
            response = sales_tax_settings_submit(
                action="add",
                jurisdiction="Virginia",
                rate_percent="5.30",
                effective_from="2026-01-01",
                effective_to="",
                is_default="1",
                conn=conn,
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/accounting/settings/sales-tax?saved=1")

            rows = conn.execute(
                "SELECT jurisdiction, rate, is_default FROM sales_tax_rates ORDER BY id"
            ).fetchall()
            self.assertEqual(rows[-1]["jurisdiction"], "Virginia")
            self.assertEqual(rows[-1]["rate"], "0.053")
            self.assertEqual(rows[-1]["is_default"], 1)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM sales_tax_rates WHERE is_default=1"
                ).fetchone()[0],
                1,
            )
        finally:
            conn.close()

    def test_update_preserves_existing_default_when_checkbox_is_left_unchecked(self) -> None:
        conn = _conn()
        try:
            north_carolina = conn.execute(
                "SELECT id FROM sales_tax_rates WHERE jurisdiction='North Carolina'"
            ).fetchone()["id"]

            response = sales_tax_settings_submit(
                action="update",
                rate_id=str(north_carolina),
                jurisdiction="North Carolina",
                rate_percent="4.99",
                effective_from="2024-01-01",
                effective_to="",
                is_default="",
                conn=conn,
            )
            self.assertEqual(response.status_code, 303)

            row = conn.execute(
                "SELECT rate, is_default FROM sales_tax_rates WHERE id=?",
                (north_carolina,),
            ).fetchone()
            self.assertEqual(row["rate"], "0.0499")
            self.assertEqual(row["is_default"], 1)
        finally:
            conn.close()

    def test_invalid_date_range_redirects_with_error(self) -> None:
        conn = _conn()
        try:
            response = sales_tax_settings_submit(
                action="add",
                jurisdiction="Virginia",
                rate_percent="5.30",
                effective_from="2026-02-01",
                effective_to="2026-01-01",
                is_default="",
                conn=conn,
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("/accounting/settings/sales-tax?error=", response.headers["location"])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sales_tax_rates").fetchone()[0],
                2,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
