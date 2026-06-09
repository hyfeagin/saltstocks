#!/usr/bin/env python3
"""
One-off backfill: create missing journal entries for 3 eBay orders.

Case 1 — Star Wars Talking Bank (27-14321-10931)
  Imported before accounting was built → needs COGS + Revenue

Case 2 — Funko (04-14734-54875)
  Has COGS entry already → needs Revenue only

Case 3 — Lego Playpack (05-14155-68337)
  Never imported (no SKU match); inventory manually deducted → needs COGS + Revenue

Sale prices exclude sales tax (eBay collects and remits tax; it never flows
through the seller's books as revenue).

Run from the project root:
    python backfill_ebay_entries.py
"""
from __future__ import annotations

import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.db import DB_PATH
from app.routers.ebay import _post_ebay_cogs_entry, _post_ebay_revenue_entry

# ── Order data ─────────────────────────────────────────────────────────────────
# sale_price = total collected from buyer minus sales tax
# All three verified: sale_price - fees - shipping == order_earnings

ORDERS = [
    {
        "label":            "Star Wars Talking Bank",
        "order_id":         "27-14321-10931",
        "sku":              "GV-KENNAR-000001",
        "sale_price":       Decimal("33.30"),   # 35.30 - 2.00 tax
        "transaction_fees": Decimal("5.20"),
        "shipping_cost":    Decimal("6.51"),
        "cogs_amount":      Decimal("6.75"),
        "needs_cogs":       True,
        "needs_revenue":    True,
    },
    {
        "label":            "Funko",
        "order_id":         "04-14734-54875",
        "sku":              None,               # resolved from ebay_import_log below
        "sale_price":       Decimal("19.19"),   # 19.94 - 0.75 tax
        "transaction_fees": Decimal("3.11"),
        "shipping_cost":    Decimal("6.69"),
        "cogs_amount":      None,
        "needs_cogs":       False,
        "needs_revenue":    True,
    },
    {
        "label":            "Lego Playpack",
        "order_id":         "05-14155-68337",
        "sku":              "GV-LEGO-000001",
        "sale_price":       Decimal("14.00"),   # 14.84 - 0.84 tax
        "transaction_fees": Decimal("2.42"),
        "shipping_cost":    Decimal("6.50"),
        "cogs_amount":      Decimal("5.98"),
        "needs_cogs":       True,
        "needs_revenue":    True,
    },
]


def _already_has_revenue(conn: sqlite3.Connection, order_id: str) -> bool:
    """Return True if an EBAY_SALE entry already exists for this order."""
    row = conn.execute(
        "SELECT id FROM journal_entries WHERE template_id='EBAY_SALE' AND notes LIKE ?",
        (f"%{order_id}%",),
    ).fetchone()
    return row is not None


def _already_has_cogs(conn: sqlite3.Connection, order_id: str) -> bool:
    """Return True if a COGS_RECOGNITION entry already exists for this order."""
    row = conn.execute(
        "SELECT id FROM journal_entries WHERE template_id='COGS_RECOGNITION' AND notes LIKE ?",
        (f"%{order_id}%",),
    ).fetchone()
    return row is not None


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    errors: list[str] = []

    try:
        with conn:
            for order in ORDERS:
                label    = order["label"]
                order_id = order["order_id"]
                print(f"\n{'─' * 55}")
                print(f"  {label}  ({order_id})")
                print(f"{'─' * 55}")

                # ── Resolve line_item_id and item_id ──────────────────────────
                log_rows = conn.execute(
                    "SELECT line_item_id, sku, item_id FROM ebay_import_log WHERE order_id=?",
                    (order_id,),
                ).fetchall()

                if log_rows:
                    # Use the first log row; warn if multiple
                    if len(log_rows) > 1:
                        print(f"  NOTE: {len(log_rows)} lines in import log — using first")
                    log = log_rows[0]
                    line_item_id = log["line_item_id"]
                    resolved_sku = order["sku"] or log["sku"]
                    resolved_item_id = log["item_id"]
                else:
                    print(f"  NOTE: not in ebay_import_log — using synthetic line_item_id")
                    line_item_id     = f"{order_id}-line-1"
                    resolved_sku     = order["sku"]
                    resolved_item_id = None

                # Look up item name (and item_id if not resolved from log)
                item_row = None
                if resolved_item_id:
                    item_row = conn.execute(
                        "SELECT id, name FROM items WHERE id=?", (resolved_item_id,)
                    ).fetchone()
                if item_row is None and resolved_sku:
                    item_row = conn.execute(
                        "SELECT id, name FROM items WHERE sku=?", (resolved_sku,)
                    ).fetchone()

                if item_row:
                    resolved_item_id = item_row["id"]
                    item_name        = item_row["name"]
                else:
                    item_name = label

                # ── COGS entry ────────────────────────────────────────────────
                if order["needs_cogs"]:
                    if _already_has_cogs(conn, order_id):
                        print(f"  COGS    — already exists, skipped")
                    elif resolved_item_id is None:
                        msg = f"COGS skipped for {label}: item not found (SKU={resolved_sku})"
                        print(f"  COGS    — ERROR: {msg}")
                        errors.append(msg)
                    else:
                        cogs_id = _post_ebay_cogs_entry(
                            conn,
                            order_id=order_id,
                            line_item_id=line_item_id,
                            item_id=resolved_item_id,
                            item_name=item_name,
                            sku=resolved_sku,
                            quantity_deducted=Decimal("1"),
                            unit_cost=order["cogs_amount"],
                        )
                        print(f"  COGS    — created entry id={cogs_id}  "
                              f"debit 5000 ${order['cogs_amount']}  "
                              f"credit 1200 ${order['cogs_amount']}")

                # ── Revenue entry ─────────────────────────────────────────────
                if order["needs_revenue"]:
                    if _already_has_revenue(conn, order_id):
                        print(f"  Revenue — already exists, skipped")
                    else:
                        net = (order["sale_price"]
                               - order["transaction_fees"]
                               - order["shipping_cost"]).quantize(Decimal("0.01"))
                        rev_id = _post_ebay_revenue_entry(
                            conn,
                            order_id=order_id,
                            line_item_id=line_item_id,
                            item_name=item_name,
                            sku=resolved_sku,
                            sale_price=order["sale_price"],
                            transaction_fees=order["transaction_fees"],
                            shipping_cost=order["shipping_cost"],
                        )
                        print(f"  Revenue — created entry id={rev_id}")
                        print(f"            credit 4000 ${order['sale_price']}  "
                              f"(revenue)")
                        print(f"            debit  6060 ${order['transaction_fees']}  "
                              f"(eBay fees)")
                        print(f"            debit  6050 ${order['shipping_cost']}  "
                              f"(shipping)")
                        print(f"            debit  1020 ${net}  "
                              f"(net payout to bank)")

        print(f"\n{'═' * 55}")
        if errors:
            print(f"  Completed with {len(errors)} error(s):")
            for e in errors:
                print(f"    • {e}")
        else:
            print("  All entries committed successfully.")
        print(f"{'═' * 55}\n")

    except Exception as exc:
        print(f"\n  ERROR — entire transaction rolled back: {exc}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
