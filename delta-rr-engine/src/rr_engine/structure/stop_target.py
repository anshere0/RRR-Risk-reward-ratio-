"""Stop and target selection on closed 5m index candles.

Optimized with dynamic tactical stop loss bounds (max 20% option premium cap)
and institutional target expansion (bypassing micro-walls to target Standard Pivots).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from rr_engine.structure.pivots import confirmed_pivots


def select_stop(
    direction: int,
    spot: float,
    candles: Sequence[dict],
    atr: float,
    *,
    k: int = 2,
    lookback: int = 36,
    buffer_atr: float = 0.25,
    min_atr: float = 0.5,
    max_atr: float = 2.0,
    fallback_atr: float = 1.0,
    entry_premium: float | None = None,
    delta: float | None = None,
    max_premium_risk_pct: float = 0.20,
) -> dict:
    """direction = +1 for call (bullish), -1 for put (bearish). Returns SPOT stop level dict.

    Calls: stop below the most recent confirmed swing low below spot, minus a buffer.
    Puts : stop above the most recent confirmed swing high above spot, plus a buffer.
    If entry_premium and delta are provided, caps the stop distance to the max allowable
    option premium drawdown (default 20%).
    """
    start = max(0, len(candles) - lookback)
    highs, lows = confirmed_pivots(candles, k)
    source = "swing"
    level: float | None = None

    if direction > 0:
        cands = [(i, p) for i, p in lows if i >= start and p < spot]
        if cands:
            level = cands[-1][1] - buffer_atr * atr
    else:
        cands = [(i, p) for i, p in highs if i >= start and p > spot]
        if cands:
            level = cands[-1][1] + buffer_atr * atr

    if level is None:
        source = "atr_fallback"
        level = spot - direction * fallback_atr * atr

    dist = abs(spot - level)

    # Tactical Option Premium Drawdown Cap (eliminates 50% wipeout trap)
    if entry_premium is not None and entry_premium > 0 and delta is not None:
        abs_delta = max(abs(delta), 0.20)
        max_spot_excursion = (entry_premium * max_premium_risk_pct) / abs_delta
        if dist > max_spot_excursion:
            level = spot - direction * max_spot_excursion
            dist = max_spot_excursion
            source = "premium_drawdown_cap"

    widened = False
    if dist < min_atr * atr:
        level = spot - direction * min_atr * atr
        dist = min_atr * atr
        widened = True

    gate = None
    if dist > max_atr * atr:
        gate = "STOP_TOO_WIDE"

    return {
        "sl_spot": level,
        "dist": dist,
        "dist_atr": dist / atr if atr > 0 else 0.0,
        "source": source,
        "widened_to_noise_floor": widened,
        "gate": gate,
    }


def select_target(
    direction: int,
    spot: float,
    candles: Sequence[dict],
    atr: float,
    *,
    k: int = 2,
    lookback: int = 144,
    min_tp_atr: float = 0.75,
    extra_levels: Sequence[tuple[float, str]] = (),
    fallback_atr: float = 1.5,
    stop_dist: float | None = None,
    min_rrr: float | None = None,
) -> dict:
    """Nearest opposing structure in the trade direction with minimum RRR filtering.

    extra_levels = [(price, label), ...] (prior-day H/L, VWAP, round numbers, standard pivots).
    Filters out micro-noise barriers closer than min_tp_atr * ATR and ensures target
    satisfies min_rrr * stop_dist when specified.
    """
    start = max(0, len(candles) - lookback)
    highs, lows = confirmed_pivots(candles, k)
    levels: list[tuple[float, str]] = []

    if direction > 0:
        levels += [(p, "swing_high") for i, p in highs if i >= start and p > spot]
        levels += [(p, lab) for p, lab in extra_levels if p > spot]
    else:
        levels += [(p, "swing_low") for i, p in lows if i >= start and p < spot]
        levels += [(p, lab) for p, lab in extra_levels if p < spot]

    levels.sort(key=lambda x: abs(x[0] - spot))

    # Required target distance threshold
    min_target_dist = min_tp_atr * atr
    if stop_dist is not None and min_rrr is not None:
        min_target_dist = max(min_target_dist, stop_dist * min_rrr)

    # Filter out minor noise levels that choke payoff if we have valid further levels
    satisfying_levels = [lvl for lvl in levels if abs(lvl[0] - spot) >= min_target_dist]

    if satisfying_levels:
        p, lab = satisfying_levels[0]
        dist = abs(p - spot)
        return {
            "tp_spot": p,
            "dist": dist,
            "dist_atr": dist / atr if atr > 0 else 0.0,
            "source": "structure",
            "label": lab,
            "gate": None,
        }

    if not levels:
        tp_dist = max(fallback_atr * atr, min_target_dist)
        tp = spot + direction * tp_dist
        return {
            "tp_spot": tp,
            "dist": abs(tp - spot),
            "dist_atr": tp_dist / atr if atr > 0 else 0.0,
            "source": "atr_fallback",
            "label": "open_air",
            "gate": None,
        }

    # If all levels are closer than min_target_dist, pick the furthest or first level with appropriate gate
    p, lab = levels[0]
    dist = abs(p - spot)
    gate = "TARGET_BLOCKED" if dist < min_tp_atr * atr else None

    return {
        "tp_spot": p,
        "dist": dist,
        "dist_atr": dist / atr if atr > 0 else 0.0,
        "source": "structure",
        "label": lab,
        "gate": gate,
    }


def check_target_reachability(
    target_dist: float,
    spot: float,
    sigma_5m: float,
    holding_candles: int = 6,  # 30 minutes = 6 * 5m candles
    reach_factor: float = 1.5,
) -> tuple[bool, float, float]:
    """Check if target distance is reachable within the holding window.

    Formula from docs/02 §2: target distance <= reach_factor * spot * sigma_5m * sqrt(N).
    Returns (is_reachable, target_dist, max_reach).
    """
    if spot <= 0 or sigma_5m <= 0 or holding_candles <= 0:
        return True, target_dist, float("inf")

    # Standard deviation move in price points over N candles
    max_reach = reach_factor * spot * sigma_5m * math.sqrt(holding_candles)
    is_reachable = target_dist <= max_reach
    return is_reachable, target_dist, max_reach
