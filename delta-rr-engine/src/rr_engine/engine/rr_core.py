"""Spot-to-option risk-reward conversion with conservative assumptions.

Matches reference/rr_reference/rr.py exactly.
All conservative rules enforced:
  1. Entry at best ask (taker). Exit at model value minus half the spread (taker at bid).
  2. Spread stress at the stop: exit half-spread multiplied by sl_spread_stress (default 1.5).
  3. IV shock: stop scenario uses sigma + iv_shock_sl (default -0.02); target scenario gets NO IV credit.
  4. Both scenarios valued at END of holding window (maximum time decay).
  5. Fees on entry and exit (taker), including tax.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rr_engine.costs.fees import option_fee_usd
from rr_engine.pricing.black_scholes import bs_price, years_from_seconds
from rr_engine.pricing.implied_vol import implied_vol


@dataclass
class RRParams:
    spot: float
    strike: float
    is_call: bool
    seconds_to_expiry: float
    mark: float  # option mark price (USD per 1 BTC) used to anchor volatility
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
    extra: dict[str, Any] = field(default_factory=dict)


def compute_rr(p: RRParams) -> dict[str, Any]:
    """Compute risk, reward, net RR, and scenario prices.

    Matches reference/rr_reference/rr.py exactly.
    """
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
    entry = p.extra.get("entry_override") if (p.extra and p.extra.get("entry_override") is not None) else p.ask

    # Clean instantaneous scenario prices at current time T0 (zero decay, pure chart mapping)
    clean_v_tp = bs_price(p.tp_spot, p.strike, T0, sigma, p.is_call, p.r)
    clean_v_sl = bs_price(p.sl_spot, p.strike, T0, sigma, p.is_call, p.r)
    clean_gain = max(clean_v_tp - entry, 0.0)
    clean_loss = max(entry - clean_v_sl, 0.0)
    clean_rr = (clean_gain / clean_loss) if clean_loss > 0 else float("inf")

    def fee(prem: float) -> float:
        return option_fee_usd(
            p.spot,
            prem,
            p.lots,
            p.contract_value,
            p.taker_commission_rate,
            p.premium_commission_rate,
            p.gst,
        )

    unit = p.contract_value * p.lots  # converts premium-units to USD for the position
    fee_in = fee(entry)
    net_tp = (exit_tp - entry) * unit - fee_in - fee(exit_tp)
    net_sl = (exit_sl - entry) * unit - fee_in - fee(exit_sl)
    risk = max(-net_sl, 0.0)
    reward = max(net_tp, 0.0)
    rr = (reward / risk) if risk > 0 else float("inf")
    p_breakeven = risk / (risk + reward) if (risk + reward) > 0 else 1.0
    max_loss_cap = entry * unit + fee_in  # premium paid + entry fee is the worst case for a long option

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
        "clean_rr": clean_rr,
        "clean_tp_price": clean_v_tp,
        "clean_sl_price": clean_v_sl,
        "breakeven_win_probability": p_breakeven,
        "worst_case_loss_usd": max_loss_cap,
        "fees_usd": {"entry": fee_in, "exit_tp": fee(exit_tp), "exit_sl": fee(exit_sl)},
        "spread_round_trip_usd": 2 * half_spread * unit,
    }


def scenario_ladder(p: RRParams, spot_offsets: list[float]) -> list[dict[str, float]]:
    """Option model value for a list of spot offsets at the end of the holding window (for the HUD ladder)."""
    T0 = years_from_seconds(p.seconds_to_expiry)
    T1 = years_from_seconds(p.seconds_to_expiry - p.hold_seconds)
    sigma = implied_vol(p.mark, p.spot, p.strike, T0, p.is_call, p.r)
    return [
        {
            "spot": p.spot + d,
            "offset": d,
            "option_model": bs_price(p.spot + d, p.strike, T1, sigma, p.is_call, p.r),
        }
        for d in spot_offsets
    ]


def compute_scenario_pricing(
    spot: float,
    strike: float,
    seconds_to_expiry: float,
    iv_anchor: float,
    is_call: bool,
    entry_premium: float,
    tp_spot: float,
    sl_spot: float,
    sigma_5m: float,
    spread: float = 5.0,
    lots: int = 1,
) -> dict[str, Any]:
    """Computes realistic scenario pricing using first-passage drift-diffusion
    hitting time and volatility skew expansion.
    """
    stop_dist = abs(spot - sl_spot)
    target_dist = abs(spot - tp_spot)
    candle_sigma_pts = spot * max(sigma_5m, 0.001)

    # 1. First-Passage Expected Resolution Time (in seconds)
    expected_candles = (stop_dist * target_dist) / (candle_sigma_pts**2)
    expected_seconds = max(600.0, min(expected_candles * 300.0, 1800.0))
    t_eval = max((seconds_to_expiry - expected_seconds) / 31536000.0, 1e-6)

    # 2. Volatility Skew Calibration
    if not is_call:
        iv_tp = iv_anchor + 0.008  # +0.8% vol smile expansion at Put target
        iv_sl = max(iv_anchor - 0.005, 0.05)  # -0.5% mild IV drop at stop
    else:
        iv_tp = iv_anchor
        iv_sl = max(iv_anchor - 0.005, 0.05)

    # 3. Model Scenario Exit Prices
    raw_tp = bs_price(tp_spot, strike, t_eval, iv_tp, is_call, 0.0)
    raw_sl = bs_price(sl_spot, strike, t_eval, iv_sl, is_call, 0.0)

    exit_tp = max(raw_tp - (0.5 * spread), 0.0)
    exit_sl = max(raw_sl - (0.75 * spread), 0.0)

    # 4. Cash Flows per Lot (0.001 BTC contract value)
    contract_val = 0.001
    gross_reward = max(exit_tp - entry_premium, 0.0) * contract_val * lots
    gross_risk = max(entry_premium - exit_sl, 0.01) * contract_val * lots

    # 5. Delta India Fee Schedule: 0.03% taker capped at 10% premium + 18% GST
    calc_fee = lambda p: min(0.0003 * spot * contract_val * lots, 0.10 * p * contract_val * lots) * 1.18
    fee_entry = calc_fee(entry_premium)
    fee_exit_tp = calc_fee(exit_tp)
    fee_exit_sl = calc_fee(exit_sl)

    net_reward = max(gross_reward - (fee_entry + fee_exit_tp), 0.0)
    net_risk = gross_risk + (fee_entry + fee_exit_sl)

    clean_rrr = gross_reward / gross_risk if gross_risk > 0 else 0.0
    net_rrr = net_reward / net_risk if net_risk > 0 else 0.0

    return {
        "expected_duration_mins": round(expected_seconds / 60.0, 1),
        "exit_tp_premium": round(exit_tp, 2),
        "exit_sl_premium": round(exit_sl, 2),
        "gross_reward_usd": round(gross_reward, 2),
        "gross_risk_usd": round(gross_risk, 2),
        "net_reward_usd": round(net_reward, 2),
        "net_risk_usd": round(net_risk, 2),
        "clean_rrr": round(clean_rrr, 2),
        "net_rrr": round(net_rrr, 2),
        "iv_tp": round(iv_tp, 4),
        "iv_sl": round(iv_sl, 4),
    }
