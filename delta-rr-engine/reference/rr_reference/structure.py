"""Swing structure, stop and target selection on CLOSED 5m index candles."""
from __future__ import annotations

from typing import Optional, Sequence


def confirmed_pivots(candles: Sequence[dict], k: int = 2):
    """Fractal pivots. A pivot at i needs k candles on each side, so it is only 'confirmed'
    k candles after it forms (no repainting). Returns (swing_highs, swing_lows) as lists of (index, price)."""
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        h = candles[i]["h"]
        l = candles[i]["l"]
        if h > max(c["h"] for c in candles[i - k:i]) and h >= max(c["h"] for c in candles[i + 1:i + k + 1]):
            highs.append((i, h))
        if l < min(c["l"] for c in candles[i - k:i]) and l <= min(c["l"] for c in candles[i + 1:i + k + 1]):
            lows.append((i, l))
    return highs, lows


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
    """direction = +1 for a call (bullish), -1 for a put (bearish). Returns the SPOT stop level.

    Calls: stop below the most recent confirmed swing low that is below spot, minus a buffer.
    Puts : stop above the most recent confirmed swing high that is above spot, plus a buffer.
    """
    start = max(0, len(candles) - lookback)
    highs, lows = confirmed_pivots(candles, k)
    source = "swing"
    level: Optional[float] = None
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
    """Nearest opposing structure in the trade direction with minimum RRR filtering."""
    start = max(0, len(candles) - lookback)
    highs, lows = confirmed_pivots(candles, k)
    levels = []
    if direction > 0:
        levels += [(p, "swing_high") for i, p in highs if i >= start and p > spot]
        levels += [(p, lab) for p, lab in extra_levels if p > spot]
    else:
        levels += [(p, "swing_low") for i, p in lows if i >= start and p < spot]
        levels += [(p, lab) for p, lab in extra_levels if p < spot]
    levels.sort(key=lambda x: abs(x[0] - spot))

    min_target_dist = min_tp_atr * atr
    if stop_dist is not None and min_rrr is not None:
        min_target_dist = max(min_target_dist, stop_dist * min_rrr)

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
