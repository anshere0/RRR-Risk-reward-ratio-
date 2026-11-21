"""NO_TRADE gates per docs/04_NO_TRADE_RULES.md.

Every gate is implemented as a pure function returning GateResult:
  - fired: bool
  - code: str
  - severity: 'hard' | 'warning'
  - message: str (plain English explanation for retail trader)
  - value: float | str | None
  - threshold: float | str | None

Gate categories (evaluated and ordered A -> F):
  A: Data integrity (hard)
  B: Option quality (hard)
  C: Market context (hard)
  D: Trade geometry (hard)
  E: Evidence (hard, only when edge_mode == 'empirical')
  F: Session discipline (hard)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GateResult:
    code: str
    severity: str  # 'hard' | 'warning'
    fired: bool
    message: str
    value: float | str | None = None
    threshold: float | str | None = None


# ============================================================================
# Section A: Data Integrity (Hard Gates)
# ============================================================================


def gate_feed_disconnected(is_connected: bool) -> GateResult:
    fired = not is_connected
    return GateResult(
        code="FEED_DISCONNECTED",
        severity="hard",
        fired=fired,
        message="Market data feed is currently disconnected. Cannot evaluate live trade.",
        value="disconnected" if fired else "connected",
        threshold="connected",
    )


def gate_data_stale(age_seconds: float, limit_seconds: float = 3.0) -> GateResult:
    fired = age_seconds > limit_seconds
    return GateResult(
        code="DATA_STALE",
        severity="hard",
        fired=fired,
        message=f"Market quotes are {age_seconds:.1f}s old (exceeds {limit_seconds:.1f}s limit). Waiting for fresh data.",
        value=round(age_seconds, 2),
        threshold=limit_seconds,
    )


def gate_candle_gap(has_gap: bool, gap_reason: str | None = None) -> GateResult:
    return GateResult(
        code="CANDLE_GAP",
        severity="hard",
        fired=has_gap,
        message=f"Missing 5-minute candles detected in history: {gap_reason or 'Gap detected'}."
        if has_gap
        else "Candle series continuous.",
        value="gap" if has_gap else "clean",
        threshold="clean",
    )


def gate_product_not_tradable(state: str, trading_status: str) -> GateResult:
    is_live = state.lower() == "live"
    is_operational = trading_status.lower() == "operational"
    fired = not (is_live and is_operational)
    return GateResult(
        code="PRODUCT_NOT_TRADABLE",
        severity="hard",
        fired=fired,
        message=f"Option status is not tradable: state='{state}', trading_status='{trading_status}'.",
        value=f"{state}/{trading_status}",
        threshold="live/operational",
    )


def gate_symbol_invalid(symbol: str, is_btc_option: bool) -> GateResult:
    fired = not is_btc_option
    return GateResult(
        code="SYMBOL_INVALID",
        severity="hard",
        fired=fired,
        message=f"'{symbol}' is not a valid BTC option symbol on Delta Exchange.",
        value=symbol,
        threshold="[C|P]-BTC-<strike>-<expiry>",
    )


def gate_quote_invalid(bid: float, ask: float) -> GateResult:
    fired = (bid <= 0) or (ask <= 0) or (ask < bid)
    msg = "Valid bid/ask quotes."
    if bid <= 0:
        msg = f"Option has no active buyers (best bid is {bid}). Cannot safely determine exit value."
    elif ask <= 0:
        msg = f"Option has no active sellers (best ask is {ask}). Cannot determine entry price."
    elif ask < bid:
        msg = f"Crossed orderbook detected: ask ({ask}) is lower than bid ({bid}). Exchange feed anomaly."

    return GateResult(
        code="QUOTE_INVALID",
        severity="hard",
        fired=fired,
        message=msg,
        value=f"bid={bid},ask={ask}",
        threshold="0 < bid <= ask",
    )


def gate_data_sanity_failed(
    sigma_5m: float | None,
    atr: float | None,
    spot: float | None,
    has_zero_close: bool = False,
) -> GateResult:
    """F5 Data-sanity gate:
    Checks if sigma_5m is within [1e-4, 2e-2] and ATR/price within [0.02%, 1%].
    Also checks if closes are 0 or spot is invalid.
    """
    if has_zero_close or spot is None or spot <= 0 or atr is None or sigma_5m is None:
        return GateResult(
            code="DATA_SANITY_FAILED",
            severity="hard",
            fired=True,
            message="Market data sanity check failed: zero close, missing spot price, or invalid candle data detected.",
            value="invalid_series",
            threshold="valid numeric data",
        )

    atr_pct = atr / spot
    sigma_failed = not (1e-4 <= sigma_5m <= 2e-2)
    atr_failed = not (0.0002 <= atr_pct <= 0.01)
    fired = sigma_failed or atr_failed

    msg = "Market volatility and ATR are within valid regime."
    if fired:
        reasons = []
        if sigma_failed:
            reasons.append(f"sigma_5m ({sigma_5m:.2e}) outside valid range [1e-4, 2e-2]")
        if atr_failed:
            reasons.append(f"ATR/price ({atr_pct * 100:.3f}%) outside valid range [0.02%, 1.0%]")
        msg = f"Market data sanity check failed: {'; '.join(reasons)}. Market feed may be flat, corrupted, or halted."

    return GateResult(
        code="DATA_SANITY_FAILED",
        severity="hard",
        fired=fired,
        message=msg,
        value=f"sigma={sigma_5m:.2e},atr_pct={atr_pct * 100:.3f}%",
        threshold="sigma in [1e-4, 2e-2], atr_pct in [0.02%, 1%]",
    )


def gate_indicator_unavailable(indicator_name: str) -> GateResult:
    return GateResult(
        code="INDICATOR_UNAVAILABLE",
        severity="hard",
        fired=True,
        message=f"Required market indicator '{indicator_name}' is unavailable or invalid.",
        value=indicator_name,
        threshold="valid numeric",
    )


def gate_greeks_unavailable() -> GateResult:
    return GateResult(
        code="GREEKS_UNAVAILABLE",
        severity="hard",
        fired=True,
        message="Option Greeks (delta) are missing from exchange quote. Cannot safely verify option pricing.",
        value="missing",
        threshold="numeric delta",
    )


def gate_index_perp_divergence(divergence_pct: float, limit_pct: float = 0.25) -> GateResult:
    fired = divergence_pct > limit_pct
    return GateResult(
        code="INDEX_PERP_DIVERGENCE",
        severity="hard",
        fired=fired,
        message=f"Spot index and Perpetual price diverge by {divergence_pct:.2f}% (limit {limit_pct:.2f}%). Feed stress.",
        value=round(divergence_pct, 3),
        threshold=limit_pct,
    )


def gate_model_calibration_failed(error_msg: str | None) -> GateResult:
    fired = error_msg is not None
    return GateResult(
        code="MODEL_CALIBRATION_FAILED",
        severity="hard",
        fired=fired,
        message=f"Failed to calibrate Black-Scholes model to live mark price: {error_msg}."
        if fired
        else "Calibration OK.",
        value=error_msg,
        threshold="converged",
    )


def gate_greeks_mismatch_severe(delta_diff: float, limit: float = 0.15) -> GateResult:
    fired = delta_diff > limit
    return GateResult(
        code="GREEKS_MISMATCH_SEVERE",
        severity="hard",
        fired=fired,
        message=f"Calculated delta differs from exchange delta by {delta_diff:.3f} (exceeds {limit:.2f}). Model inconsistency.",
        value=round(delta_diff, 4),
        threshold=limit,
    )


def gate_clock_skew(skew_seconds: float, limit_seconds: float = 2.0) -> GateResult:
    fired = skew_seconds > limit_seconds
    return GateResult(
        code="CLOCK_SKEW",
        severity="hard",
        fired=fired,
        message=f"Local machine clock diverges from exchange server by {skew_seconds:.1f}s (limit {limit_seconds:.1f}s).",
        value=round(skew_seconds, 2),
        threshold=limit_seconds,
    )


# ============================================================================
# Section B: Option Quality (Hard Gates)
# ============================================================================


def gate_expiry_too_close(
    seconds_to_expiry: float,
    min_minutes: int = 60,
    hold_minutes: int = 30,
    buffer_minutes: int = 10,
) -> GateResult:
    min_sec = min_minutes * 60
    hold_sec = hold_minutes * 60
    buf_sec = buffer_minutes * 60

    too_close = seconds_to_expiry < min_sec
    hold_beyond = seconds_to_expiry < (hold_sec + buf_sec)
    fired = too_close or hold_beyond

    mins_left = seconds_to_expiry / 60.0
    msg = f"Time to expiry is {mins_left:.0f}m. Need at least {min_minutes}m and holding window must end {buffer_minutes}m before expiry."
    return GateResult(
        code="EXPIRY_TOO_CLOSE",
        severity="hard",
        fired=fired,
        message=msg,
        value=round(mins_left, 1),
        threshold=min_minutes,
    )


def gate_spread_too_wide(spread_pct: float, limit_pct: float = 1.5) -> GateResult:
    fired = spread_pct > limit_pct
    return GateResult(
        code="SPREAD_TOO_WIDE",
        severity="hard",
        fired=fired,
        message=f"Option bid-ask spread is {spread_pct:.2f}% of price (limit is {limit_pct:.2f}%). High hidden friction.",
        value=round(spread_pct, 2),
        threshold=limit_pct,
    )


def gate_spread_eats_reward(
    spread_round_trip_usd: float, gross_reward_usd: float, limit_share: float = 0.20
) -> GateResult:
    share = (spread_round_trip_usd / gross_reward_usd) if gross_reward_usd > 0 else float("inf")
    fired = share > limit_share
    return GateResult(
        code="SPREAD_EATS_REWARD",
        severity="hard",
        fired=fired,
        message=f"Round-trip spread cost (${spread_round_trip_usd:.2f}) consumes {share * 100:.1f}% of potential reward (limit {limit_share * 100:.0f}%).",
        value=round(share, 3),
        threshold=limit_share,
    )


def gate_depth_too_thin(
    ask_size: float,
    bid_size: float,
    lots: float,
    entry_mult: float = 1.5,
    exit_mult: float = 1.0,
) -> GateResult:
    req_ask = lots * entry_mult
    req_bid = lots * exit_mult
    fired = (ask_size < req_ask) or (bid_size < req_bid)
    msg = f"Insufficient orderbook depth for {lots} lots: ask size={ask_size} (need {req_ask:.1f}), bid size={bid_size} (need {req_bid:.1f})."
    return GateResult(
        code="DEPTH_TOO_THIN",
        severity="hard",
        fired=fired,
        message=msg,
        value=f"ask={ask_size},bid={bid_size}",
        threshold=f"ask>={req_ask},bid>={req_bid}",
    )


def gate_premium_too_low(entry_price: float, min_premium: float = 20.0) -> GateResult:
    fired = entry_price < min_premium
    return GateResult(
        code="PREMIUM_TOO_LOW",
        severity="hard",
        fired=fired,
        message=f"Option premium ({entry_price:.1f}) is below minimum threshold ({min_premium:.1f}). Fees and tick size dominate cheap options.",
        value=round(entry_price, 2),
        threshold=min_premium,
    )


def gate_fees_eat_reward(total_fees_usd: float, gross_reward_usd: float, limit_share: float = 0.30) -> GateResult:
    share = (total_fees_usd / gross_reward_usd) if gross_reward_usd > 0 else float("inf")
    fired = share > limit_share
    return GateResult(
        code="FEES_EAT_REWARD",
        severity="hard",
        fired=fired,
        message=f"Total estimated fees (${total_fees_usd:.2f}) eat {share * 100:.1f}% of potential reward (limit {limit_share * 100:.0f}%).",
        value=round(share, 3),
        threshold=limit_share,
    )


# ============================================================================
# Section C: Market Context (Hard Gates)
# ============================================================================


def gate_choppy_market(adx: float, is_lowest_quartile_bw: bool, adx_limit: float = 18.0) -> GateResult:
    fired = (adx < adx_limit) and is_lowest_quartile_bw
    return GateResult(
        code="CHOPPY_MARKET",
        severity="hard",
        fired=fired,
        message=f"Choppy market: ADX is {adx:.1f} (< {adx_limit:.1f}) and Bollinger bandwidth is in lowest 25%. Low follow-through probability.",
        value=round(adx, 1),
        threshold=adx_limit,
    )


def gate_against_trend(
    direction: int,
    ema20: float,
    ema50: float,
    adx: float,
    adx_trend_threshold: float = 25.0,
) -> GateResult:
    # Bullish (call, +1) requires EMA20 >= EMA50. Opposing is EMA20 < EMA50 while trending (ADX > 25).
    # Bearish (put, -1) requires EMA20 <= EMA50. Opposing is EMA20 > EMA50 while trending (ADX > 25).
    fired = False
    trend_dir = "bullish" if ema20 > ema50 else "bearish"
    trade_dir = "bullish (Call)" if direction > 0 else "bearish (Put)"

    if adx > adx_trend_threshold and ((direction > 0 and ema20 < ema50) or (direction < 0 and ema20 > ema50)):
        fired = True

    return GateResult(
        code="AGAINST_TREND",
        severity="hard",
        fired=fired,
        message=f"Trade direction ({trade_dir}) opposes active {trend_dir} trend (EMA20 vs EMA50, ADX {adx:.1f} > {adx_trend_threshold:.1f}).",
        value=round(adx, 1),
        threshold=adx_trend_threshold,
    )


def gate_spike_just_happened(
    candles_since_spike: int,
    wait_candles: int = 2,
    last_candle_range: float = 0.0,
    atr: float = 1.0,
    spike_mult: float = 2.5,
) -> GateResult:
    is_spike = (last_candle_range > spike_mult * atr) if atr > 0 else False
    fired = (candles_since_spike < wait_candles) or is_spike
    return GateResult(
        code="SPIKE_JUST_HAPPENED",
        severity="hard",
        fired=fired,
        message=f"Price spike just occurred ({last_candle_range:.1f} vs {spike_mult}x ATR). Wait {wait_candles} closed candles for market calm.",
        value=round(last_candle_range / atr, 2) if atr > 0 else 0.0,
        threshold=spike_mult,
    )


def gate_vol_regime_extreme(is_extreme: bool, current_vol: float, threshold: float) -> GateResult:
    return GateResult(
        code="VOL_REGIME_EXTREME",
        severity="hard",
        fired=is_extreme,
        message=f"Current 5-minute volatility ({current_vol:.4f}) exceeds 3x its 7-day median ({threshold:.4f}). Extreme regime.",
        value=round(current_vol, 5),
        threshold=round(threshold, 5),
    )


def gate_event_window(
    now_epoch: float,
    event_windows: list[dict[str, Any]],
) -> GateResult:
    for event in event_windows:
        start = float(event.get("start_utc", 0))
        end = float(event.get("end_utc", 0))
        if start <= now_epoch <= end:
            label = event.get("label", "Economic event")
            return GateResult(
                code="EVENT_WINDOW",
                severity="hard",
                fired=True,
                message=f"Within scheduled high-impact event window: '{label}'. High volatility and spread widening risk.",
                value=label,
                threshold="no_events",
            )
    return GateResult(
        code="EVENT_WINDOW",
        severity="hard",
        fired=False,
        message="No scheduled event windows active.",
        value=None,
        threshold=None,
    )


# ============================================================================
# Section D: Trade Geometry (Hard Gates) & Warnings
# ============================================================================


def gate_stop_too_wide(dist_atr: float, max_atr: float = 2.0) -> GateResult:
    fired = dist_atr > max_atr
    return GateResult(
        code="STOP_TOO_WIDE",
        severity="hard",
        fired=fired,
        message=f"Stop loss distance is {dist_atr:.2f}x ATR (limit is {max_atr:.2f}x ATR). Invalidation is too far away.",
        value=round(dist_atr, 2),
        threshold=max_atr,
    )


def gate_target_blocked(dist_atr: float, min_atr: float = 0.75, barrier_label: str = "") -> GateResult:
    fired = dist_atr < min_atr
    label_info = f" ({barrier_label})" if barrier_label else ""
    return GateResult(
        code="TARGET_BLOCKED",
        severity="hard",
        fired=fired,
        message=f"Opposing structural level{label_info} is only {dist_atr:.2f}x ATR away (< {min_atr:.2f}x ATR). Path is blocked.",
        value=round(dist_atr, 2),
        threshold=min_atr,
    )


def gate_target_unreachable_in_window(is_reachable: bool, target_dist: float, max_reach: float) -> GateResult:
    fired = not is_reachable
    return GateResult(
        code="TARGET_UNREACHABLE_IN_WINDOW",
        severity="hard",
        fired=fired,
        message=f"Target distance ({target_dist:.1f}) exceeds maximum expected price reach in holding window ({max_reach:.1f}).",
        value=round(target_dist, 1),
        threshold=round(max_reach, 1),
    )


def gate_net_rr_too_low(
    net_rr: float, min_rr: float = 1.5, risk_usd: float = 0.0, reward_usd: float = 0.0
) -> GateResult:
    fired = net_rr < min_rr
    msg = (
        f"NO TRADE — after fees, spread and decay, you would risk ${risk_usd:.2f} to make ${reward_usd:.2f} "
        f"({net_rr:.2f} : 1). Needed at least {min_rr:.1f} : 1."
    )
    return GateResult(
        code="NET_RR_TOO_LOW",
        severity="hard",
        fired=fired,
        message=msg,
        value=round(net_rr, 2) if not math.isinf(net_rr) else 0.0,
        threshold=min_rr,
    )


def gate_breakeven_too_high(breakeven_win_rate: float, max_breakeven: float = 0.45) -> GateResult:
    fired = breakeven_win_rate > max_breakeven
    return GateResult(
        code="BREAKEVEN_TOO_HIGH",
        severity="hard",
        fired=fired,
        message=f"Required break-even win rate is {breakeven_win_rate * 100:.1f}% (maximum allowable is {max_breakeven * 100:.1f}%). Setup geometry unfavourable.",
        value=round(breakeven_win_rate, 3),
        threshold=max_breakeven,
    )


def gate_entry_override_invalid(
    override: float | None,
    bid: float,
    ask: float,
    tick_size: float = 0.1,
) -> GateResult:
    if override is None:
        return GateResult(
            code="ENTRY_OVERRIDE_INVALID", severity="hard", fired=False, message="No manual entry override."
        )
    min_allowed = bid - 2 * tick_size
    max_allowed = ask + 2 * tick_size
    fired = not (min_allowed <= override <= max_allowed)
    msg = f"Manual entry price (${override:.2f}) is outside allowable corridor [{min_allowed:.2f}, {max_allowed:.2f}] (must be within 2 ticks of current bid/ask)."
    return GateResult(
        code="ENTRY_OVERRIDE_INVALID",
        severity="hard",
        fired=fired,
        message=msg if fired else "Manual entry override is within valid quote corridor.",
        value=override,
        threshold=f"[{min_allowed:.2f}, {max_allowed:.2f}]",
    )


def gate_risk_over_budget(
    risk_usd: float,
    max_risk_usd: float | None,
    max_lots: int = 1,
    risk_per_lot: float = 0.0,
) -> GateResult:
    if max_risk_usd is None or max_risk_usd <= 0:
        return GateResult(
            code="RISK_OVER_BUDGET",
            severity="hard",
            fired=False,
            message="No maximum risk budget specified.",
            value=round(risk_usd, 2),
            threshold=None,
        )
    if max_lots == 0:
        return GateResult(
            code="RISK_OVER_BUDGET",
            severity="hard",
            fired=True,
            message=f"one lot risks ${risk_per_lot:.2f}, above your budget ${max_risk_usd:.2f}.",
            value=round(risk_per_lot, 2),
            threshold=round(max_risk_usd, 2),
        )
    fired = risk_usd > max_risk_usd
    return GateResult(
        code="RISK_OVER_BUDGET",
        severity="hard",
        fired=fired,
        message=f"Planned risk (${risk_usd:.2f}) exceeds your risk budget (${max_risk_usd:.2f}). Suggest max {max_lots} lots instead.",
        value=round(risk_usd, 2),
        threshold=round(max_risk_usd, 2),
    )


def gate_strike_not_atm(delta: float | None, delta_range: tuple[float, float] = (0.35, 0.65)) -> GateResult:
    """Warning-only gate: warns when option delta is far from ATM."""
    if delta is None:
        return GateResult(code="STRIKE_NOT_ATM", severity="warning", fired=False, message="Delta unknown.")
    abs_d = abs(delta)
    fired = not (delta_range[0] <= abs_d <= delta_range[1])
    return GateResult(
        code="STRIKE_NOT_ATM",
        severity="warning",
        fired=fired,
        message=f"Option strike is not ATM (|delta| = {abs_d:.2f}, outside {delta_range[0]}-{delta_range[1]}). Delta/liquidity decay risk.",
        value=round(abs_d, 2),
        threshold=f"{delta_range[0]}-{delta_range[1]}",
    )


def gate_greeks_mismatch_warn(delta_diff: float, limit: float = 0.07) -> GateResult:
    """Warning-only gate: warns when model delta differs slightly from exchange delta."""
    fired = delta_diff > limit
    return GateResult(
        code="GREEKS_MISMATCH",
        severity="warning",
        fired=fired,
        message=f"Calculated delta differs from exchange delta by {delta_diff:.3f} (exceeds warning limit {limit:.2f}).",
        value=round(delta_diff, 4),
        threshold=limit,
    )


# ============================================================================
# Section F: Session Discipline (Hard Gates, Optional / Uses Journal)
# ============================================================================


def gate_daily_loss_limit(daily_loss_usd: float, limit_usd: float | None) -> GateResult:
    """Fires when logged losses today exceed user limit."""
    if limit_usd is None or limit_usd <= 0:
        return GateResult(
            code="DAILY_LOSS_LIMIT",
            severity="hard",
            fired=False,
            message="No daily loss limit specified.",
            value=round(daily_loss_usd, 2),
            threshold=None,
        )
    fired = daily_loss_usd >= limit_usd
    return GateResult(
        code="DAILY_LOSS_LIMIT",
        severity="hard",
        fired=fired,
        message=f"Daily realized losses (${daily_loss_usd:.2f}) reached or exceeded limit (${limit_usd:.2f}). Trading paused for session discipline.",
        value=round(daily_loss_usd, 2),
        threshold=round(limit_usd, 2),
    )


def gate_cooldown(
    consecutive_losses: int,
    last_loss_seconds_ago: float | None,
    max_losses: int = 3,
    cooldown_seconds: float = 1800.0,
) -> GateResult:
    """Fires when consecutive losses exceed limit and cooldown is still active."""
    if consecutive_losses < max_losses or last_loss_seconds_ago is None:
        return GateResult(
            code="COOLDOWN",
            severity="hard",
            fired=False,
            message="No cooldown active.",
            value=consecutive_losses,
            threshold=max_losses,
        )
    fired = last_loss_seconds_ago < cooldown_seconds
    remaining_min = max(0, int((cooldown_seconds - last_loss_seconds_ago) / 60))
    return GateResult(
        code="COOLDOWN",
        severity="hard",
        fired=fired,
        message=f"Cooldown active after {consecutive_losses} consecutive losses ({remaining_min} min remaining). Take a break before next trade.",
        value=consecutive_losses,
        threshold=max_losses,
    )
