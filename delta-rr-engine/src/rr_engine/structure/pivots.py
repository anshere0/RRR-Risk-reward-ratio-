"""Fractal pivot detection on closed 5m candles.

Matches reference/rr_reference/structure.py exactly.
A pivot at index i requires k candles on each side, so it is only
'confirmed' k candles after it forms. It NEVER repaints.
"""

from __future__ import annotations

from collections.abc import Sequence


def confirmed_pivots(
    candles: Sequence[dict],
    k: int = 2,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Fractal pivots.

    A pivot at index i needs k candles on each side, so it is only 'confirmed'
    k candles after it forms (no repainting).
    Returns (swing_highs, swing_lows) as lists of (candle_index, price).
    """
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []

    for i in range(k, len(candles) - k):
        h = candles[i]["h"]
        low = candles[i]["l"]
        if h > max(c["h"] for c in candles[i - k : i]) and h >= max(c["h"] for c in candles[i + 1 : i + k + 1]):
            highs.append((i, h))
        if low < min(c["l"] for c in candles[i - k : i]) and low <= min(c["l"] for c in candles[i + 1 : i + k + 1]):
            lows.append((i, low))

    return highs, lows
