from __future__ import annotations

import unittest
from decimal import Decimal

from app.services.ebay_fee_calculator import (
    DEFAULT_FEE_RATE,
    break_even_price,
    calculate_sale,
)


class BreakEvenPriceTests(unittest.TestCase):
    def test_break_even_satisfies_zero_profit(self):
        for unit_cost in ("5.00", "12.50", "100.00", "0.99"):
            cost = Decimal(unit_cost)
            bep = break_even_price(cost)
            # At break-even price, gross_profit must be >= 0
            fee = (bep * DEFAULT_FEE_RATE).quantize(Decimal("0.01"))
            net = bep - fee
            self.assertGreaterEqual(
                net - cost,
                Decimal("0.00"),
                f"break_even failed for unit_cost={unit_cost}: bep={bep}, net={net}",
            )

    def test_one_cent_below_break_even_is_negative(self):
        cost = Decimal("10.00")
        bep = break_even_price(cost)
        below = bep - Decimal("0.01")
        fee = (below * DEFAULT_FEE_RATE).quantize(Decimal("0.01"))
        net = below - fee
        self.assertLess(net - cost, Decimal("0.00"))

    def test_custom_fee_rate(self):
        cost = Decimal("20.00")
        rate = Decimal("0.10")
        bep = break_even_price(cost, rate)
        fee = (bep * rate).quantize(Decimal("0.01"))
        self.assertGreaterEqual(bep - fee - cost, Decimal("0.00"))


class CalculateSaleTests(unittest.TestCase):
    def _sale(self, list_price, unit_cost, shipping="0", fee_rate=None):
        kwargs = {}
        if fee_rate is not None:
            kwargs["fee_rate"] = Decimal(fee_rate)
        return calculate_sale(
            Decimal(list_price), Decimal(unit_cost), Decimal(shipping), **kwargs
        )

    def test_all_monetary_fields_are_decimal(self):
        result = self._sale("25.00", "10.00")
        for key in ("ebay_fee", "net_payout", "cogs", "gross_profit", "margin_pct", "break_even_price"):
            self.assertIsInstance(result[key], Decimal, f"{key} is not Decimal")

    def test_fee_equals_list_price_times_rate(self):
        result = self._sale("30.00", "10.00")
        expected_fee = (Decimal("30.00") * DEFAULT_FEE_RATE).quantize(Decimal("0.01"))
        self.assertEqual(result["ebay_fee"], expected_fee)

    def test_net_payout_equals_list_minus_fee_minus_shipping(self):
        result = self._sale("50.00", "15.00", shipping="5.00")
        fee = result["ebay_fee"]
        self.assertEqual(result["net_payout"], Decimal("50.00") - fee - Decimal("5.00"))

    def test_gross_profit_equals_net_payout_minus_cost(self):
        result = self._sale("40.00", "12.00")
        self.assertEqual(result["gross_profit"], result["net_payout"] - result["cogs"])

    def test_note_field_not_present(self):
        result = self._sale("20.00", "8.00")
        # The calculator does not include the simulation note — that lives in tools.py
        self.assertNotIn("note", result)

    def test_zero_list_price_margin_is_zero(self):
        result = self._sale("0", "0")
        self.assertEqual(result["margin_pct"], Decimal("0.00"))

    def test_break_even_satisfies_spec(self):
        # spec AC #6: break_even * (1 - fee_rate) - unit_cost >= 0
        for list_price, unit_cost in [("25.00", "10.00"), ("100.00", "45.00")]:
            r = self._sale(list_price, unit_cost)
            bep = r["break_even_price"]
            self.assertGreaterEqual(
                bep * (1 - DEFAULT_FEE_RATE) - r["cogs"],
                Decimal("0.00"),
            )

    def test_two_decimal_places_on_all_outputs(self):
        result = self._sale("29.99", "11.47", shipping="3.00")
        for key in ("ebay_fee", "net_payout", "cogs", "gross_profit", "margin_pct", "break_even_price"):
            val = result[key]
            self.assertEqual(
                val,
                val.quantize(Decimal("0.01")),
                f"{key}={val} has more than 2 decimal places",
            )
