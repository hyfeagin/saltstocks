"""
Pure functions for eBay fee and margin math.
No I/O — safe to unit test without a database.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_UP

_CENT = Decimal("0.01")
DEFAULT_FEE_RATE = Decimal("0.1325")  # 13.25% combined eBay rate


def break_even_price(unit_cost: Decimal, fee_rate: Decimal = DEFAULT_FEE_RATE) -> Decimal:
    """Minimum list price at which gross_profit >= 0, rounded up to the nearest cent."""
    return (unit_cost / (1 - fee_rate)).quantize(_CENT, rounding=ROUND_UP)


def calculate_sale(
    list_price: Decimal,
    unit_cost: Decimal,
    shipping_cost: Decimal = Decimal("0"),
    fee_rate: Decimal = DEFAULT_FEE_RATE,
) -> dict:
    """
    Returns fee breakdown for a simulated eBay sale. All values are Decimal.

    Keys: ebay_fee, net_payout, cogs, gross_profit, margin_pct, break_even_price
    """
    ebay_fee = (list_price * fee_rate).quantize(_CENT)
    net_payout = (list_price - ebay_fee - shipping_cost).quantize(_CENT)
    gross_profit = (net_payout - unit_cost).quantize(_CENT)
    margin_pct = (
        (gross_profit / list_price * 100).quantize(_CENT)
        if list_price
        else Decimal("0.00")
    )
    return {
        "ebay_fee": ebay_fee,
        "net_payout": net_payout,
        "cogs": unit_cost.quantize(_CENT),
        "gross_profit": gross_profit,
        "margin_pct": margin_pct,
        "break_even_price": break_even_price(unit_cost, fee_rate),
    }
