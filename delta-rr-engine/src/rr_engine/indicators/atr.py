"""Average True Range (Wilder smoothing) on closed candles.

Matches reference/rr_reference/indicators.py exactly.
"""

from __future__ import annotations

from collections.abc import Sequence


def true_range(candles: Sequence[dict]) -> list[float | None]:
    """Calculate True Range for each candle. First entry is None."""
    out: list[float | None] = [None]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def atr_wilder(candles: Sequence[dict], n: int = 14) -> list[float | None]:
    """Wilder's smoothed Average True Range over n periods.

    First n entries are None. Entry n is seeded with the SMA of the first n true ranges.
    Returns list aligned 1:1 with input candles.
    """
    tr = true_range(candles)
    out: list[float | None] = [None] * len(candles)
    if len(candles) <= n:
        return out
    seed = sum(tr[1 : n + 1]) / n  # type: ignore[arg-type]
    out[n] = seed
    for i in range(n + 1, len(candles)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n  # type: ignore[operator]
    return out
