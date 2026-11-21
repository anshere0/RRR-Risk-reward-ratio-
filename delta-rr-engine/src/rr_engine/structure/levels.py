"""Structural price levels: swing pivots, prior-day H/L, VWAP, round numbers.

Used by select_target to identify potential opposing barriers.
"""

from __future__ import annotations

from collections.abc import Sequence

SECONDS_PER_DAY = 86400


def prior_day_high_low_close(candles: Sequence[dict]) -> tuple[float | None, float | None, float | None]:
    """Find High, Low, and Close of the previous UTC calendar day (00:00-24:00 UTC).

    Returns (pdh, pdl, pdc).
    """
    if not candles:
        return None, None, None

    last_t = candles[-1]["t"]
    current_day = last_t // SECONDS_PER_DAY
    prior_day = current_day - 1

    prior_candles = [c for c in candles if (c["t"] // SECONDS_PER_DAY) == prior_day]
    if not prior_candles:
        return None, None, None

    pdh = max(c["h"] for c in prior_candles)
    pdl = min(c["l"] for c in prior_candles)
    pdc = prior_candles[-1]["c"]
    return pdh, pdl, pdc


def prior_day_high_low(candles: Sequence[dict]) -> tuple[float | None, float | None]:
    """Find High and Low of the previous UTC calendar day.

    Returns (pdh, pdl).
    """
    pdh, pdl, _ = prior_day_high_low_close(candles)
    return pdh, pdl


def standard_pivot_points(candles: Sequence[dict]) -> dict[str, float]:
    """Calculate Classical Standard Pivot Points (P, R1, R2, S1, S2)
    anchored to the previous UTC day (00:00-24:00 UTC H/L/C) per F6.
    R3 and S3 are omitted so they are never used as targets.
    """
    if not candles:
        return {}

    pdh, pdl, pdc = prior_day_high_low_close(candles)
    if pdh is None or pdl is None or pdc is None:
        lookback = min(len(candles), 288)
        prior = candles[-lookback:]
        pdh = max(c["h"] for c in prior)
        pdl = min(c["l"] for c in prior)
        pdc = prior[-1]["c"]

    h, l, c = pdh, pdl, pdc
    p = (h + l + c) / 3.0
    diff = h - l
    r1 = 2 * p - l
    s1 = 2 * p - h
    r2 = p + diff
    s2 = p - diff

    return {
        "P": round(p, 1),
        "R1": round(r1, 1),
        "S1": round(s1, 1),
        "R2": round(r2, 1),
        "S2": round(s2, 1),
    }


def round_numbers(
    spot: float,
    steps: Sequence[int] = (500, 1000),
    num_levels_each_side: int = 3,
) -> list[tuple[float, str]]:
    """Generate round numbers (500s and 1000s) surrounding spot."""
    levels: list[tuple[float, str]] = []
    seen = set()

    for step in steps:
        base = round(spot / step) * step
        for offset in range(-num_levels_each_side, num_levels_each_side + 1):
            val = float(base + offset * step)
            if val > 0 and val not in seen:
                seen.add(val)
                label = f"round_{step}"
                levels.append((val, label))

    return levels


def build_extra_levels(
    candles: Sequence[dict],
    vwap: float | None = None,
    spot: float | None = None,
    steps: Sequence[int] = (500, 1000),
) -> list[tuple[float, str]]:
    """Assemble prior-day H/L, VWAP, and round numbers into a level list [(price, label)]."""
    levels: list[tuple[float, str]] = []

    pdh, pdl = prior_day_high_low(candles)
    if pdh is not None:
        levels.append((pdh, "prior_day_high"))
    if pdl is not None:
        levels.append((pdl, "prior_day_low"))

    if vwap is not None and vwap > 0:
        levels.append((vwap, "session_vwap"))

    pivots = standard_pivot_points(candles)
    for p_name, p_val in pivots.items():
        levels.append((p_val, f"pivot_standard_{p_name}"))

    ref_spot = spot or (candles[-1]["c"] if candles else 0.0)
    if ref_spot > 0:
        levels.extend(round_numbers(ref_spot, steps=steps))

    return levels
