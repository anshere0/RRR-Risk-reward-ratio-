"""Spot-to-option risk-reward conversion with conservative assumptions.

Conservative rules (all configurable, defaults shown):
  1. Entry at best ask (taker). Exit at model value minus half the spread (taker at bid).
  2. Spread stress at the stop: exit half-spread multiplied by sl_spread_stress (default 1.5).
  3. IV shock: stop scenario uses sigma + iv_shock_sl (default -0.02 = 2 vol points lower);
     target scenario gets NO IV credit (iv_shock_tp default 0.0).
  4. Both scenarios are valued at the END of the holding window (maximum time decay).
  5. Fees on entry and exit (taker), including tax, from fees.option_fee_usd.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .fees import option_fee_usd
from .pricing import bs_price, implied_vol, years_from_seconds


@dataclass
class RRParams:
    spot: float
    strike: float
    is_call: bool
    seconds_to_expiry: float
    mark: float                # option mark price (USD per 1 BTC) used to anchor volatility
    bid: float
    ask: float
    sl_spot: float
    tp_spot: float
    lots: float = 1.0
    contract_value: float = 0.001
    hold_seconds: float = 1800.0
    r: float = 0.0
    taker_commission_rate: float = 0.0003
    premium_commission_rate: float = 0.10
    gst: float = 0.18
    sl_spread_stress: float = 1.5
    iv_shock_sl: float = -0.02
    iv_shock_tp: float = 0.0
    extra: dict = field(default_factory=dict)


def compute_rr(p: RRParams) -> dict:
    direction = 1 if p.is_call else -1
    if not (p.bid > 0 and p.ask >= p.bid):
        raise ValueError("invalid quotes: need 0 < bid <= ask")
    if direction > 0 and not (p.sl_spot < p.spot < p.tp_spot):
        raise ValueError("call requires sl_spot < spot < tp_spot")
    if direction < 0 and not (p.tp_spot < p.spot < p.sl_spot):
        raise ValueError("put requires tp_spot < spot < sl_spot")
    if p.seconds_to_expiry <= p.hold_seconds:
        raise ValueError("holding window must end before expiry")

    T0 = years_from_seconds(p.seconds_to_expiry)
    T1 = years_from_seconds(p.seconds_to_expiry - p.hold_seconds)
    sigma = implied_vol(p.mark, p.spot, p.strike, T0, p.is_call, p.r)  # anchor model to live mark

    half_spread = 0.5 * (p.ask - p.bid)
    v_tp = bs_price(p.tp_spot, p.strike, T1, max(sigma + p.iv_shock_tp, 1e-4), p.is_call, p.r)
    v_sl = bs_price(p.sl_spot, p.strike, T1, max(sigma + p.iv_shock_sl, 1e-4), p.is_call, p.r)
    exit_tp = max(v_tp - half_spread, 0.0)
    exit_sl = max(v_sl - half_spread * p.sl_spread_stress, 0.0)
    entry = p.ask

    def fee(prem: float) -> float:
        return option_fee_usd(p.spot, prem, p.lots, p.contract_value, p.taker_commission_rate,
                              p.premium_commission_rate, p.gst)

    unit = p.contract_value * p.lots           # converts premium-units to USD for the position
    fee_in = fee(entry)
    net_tp = (exit_tp - entry) * unit - fee_in - fee(exit_tp)
    net_sl = (exit_sl - entry) * unit - fee_in - fee(exit_sl)
    risk = max(-net_sl, 0.0)
    reward = max(net_tp, 0.0)
    rr = (reward / risk) if risk > 0 else float("inf")
    p_breakeven = risk / (risk + reward) if (risk + reward) > 0 else 1.0
    max_loss_cap = entry * unit + fee_in        # premium paid + entry fee is the worst case for a long option
    return {
        "sigma_anchor": sigma,
        "entry_price": entry,
        "tp_option_model": v_tp,
        "sl_option_model": v_sl,
        "tp_exit_net_price": exit_tp,
        "sl_exit_net_price": exit_sl,
        "risk_usd": risk,
        "reward_usd": reward,
        "net_rr": rr,
        "breakeven_win_probability": p_breakeven,
        "worst_case_loss_usd": max_loss_cap,
        "fees_usd": {"entry": fee_in, "exit_tp": fee(exit_tp), "exit_sl": fee(exit_sl)},
        "spread_round_trip_usd": 2 * half_spread * unit,
    }


def scenario_ladder(p: RRParams, spot_offsets: list) -> list:
    """Option model value for a list of spot offsets at the end of the holding window (for the HUD ladder)."""
    T0 = years_from_seconds(p.seconds_to_expiry)
    T1 = years_from_seconds(p.seconds_to_expiry - p.hold_seconds)
    sigma = implied_vol(p.mark, p.spot, p.strike, T0, p.is_call, p.r)
    return [{"spot": p.spot + d, "offset": d,
             "option_model": bs_price(p.spot + d, p.strike, T1, sigma, p.is_call, p.r)} for d in spot_offsets]
