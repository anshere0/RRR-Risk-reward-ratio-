"""Delta Exchange options fee model. Parameters MUST be read from the live product (see docs/03).

Documented structure (verify against a real fill's `paid_commission` during paper trading):
    fee = min( taker_commission_rate * underlying_notional , premium_commission_rate * premium_value )
    fee_with_tax = fee * (1 + gst)      # gst = 0.18 for Delta India (VERIFY); 0 if not applicable
underlying_notional = spot * contract_value * lots ; premium_value = premium * contract_value * lots
"""
from __future__ import annotations


def option_fee_usd(spot: float, premium: float, lots: float, contract_value: float,
                   taker_commission_rate: float, premium_commission_rate: float, gst: float = 0.0) -> float:
    if lots < 0 or premium < 0:
        raise ValueError("lots and premium must be non-negative")
    notional_fee = taker_commission_rate * spot * contract_value * lots
    premium_cap = premium_commission_rate * premium * contract_value * lots
    return min(notional_fee, premium_cap) * (1.0 + gst)
