"""Delta Exchange options fee model with GST taxation.

Matches reference/rr_reference/fees.py exactly.
Structure:
  fee = min( taker_commission_rate * underlying_notional , premium_commission_rate * premium_value )
  fee_with_tax = fee * (1 + gst)
"""

from __future__ import annotations


def option_fee_usd(
    spot: float,
    premium: float,
    lots: float,
    contract_value: float,
    taker_commission_rate: float,
    premium_commission_rate: float,
    gst: float = 0.0,
) -> float:
    """Calculate option taker fee in USD including tax for a given leg."""
    if lots < 0 or premium < 0:
        raise ValueError("lots and premium must be non-negative")

    notional_fee = taker_commission_rate * spot * contract_value * lots
    premium_cap = premium_commission_rate * premium * contract_value * lots
    return min(notional_fee, premium_cap) * (1.0 + gst)
