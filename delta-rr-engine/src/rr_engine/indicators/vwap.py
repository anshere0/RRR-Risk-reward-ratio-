"""Session Volume Weighted Average Price (VWAP) anchored to 00:00 UTC.

Computed on the BTCUSD perpetual (the spot index has no volume).
Formula: Cumulative(Typical Price * Volume) / Cumulative(Volume)
where Typical Price = (High + Low + Close) / 3.
"""

from __future__ import annotations

from collections.abc import Sequence

SECONDS_PER_DAY = 86400


def session_vwap(perp_candles: Sequence[dict]) -> list[float | None]:
    """Calculate session VWAP resetting at 00:00 UTC each day.

    Candles must have 't' (epoch seconds), 'h', 'l', 'c', and 'v' (volume).
    Returns list aligned with input candles.
    """
    length = len(perp_candles)
    out: list[float | None] = [None] * length
    if length == 0:
        return out

    cum_pv = 0.0
    cum_v = 0.0
    current_day = -1

    for i in range(length):
        c = perp_candles[i]
        t = c["t"]
        day_index = t // SECONDS_PER_DAY

        if day_index != current_day:
            # New UTC session -> reset accumulators
            cum_pv = 0.0
            cum_v = 0.0
            current_day = day_index

        vol = float(c.get("v") or 0.0)
        typical_price = (c["h"] + c["l"] + c["c"]) / 3.0

        cum_pv += typical_price * vol
        cum_v += vol

        out[i] = (cum_pv / cum_v) if cum_v > 0 else typical_price

    return out
