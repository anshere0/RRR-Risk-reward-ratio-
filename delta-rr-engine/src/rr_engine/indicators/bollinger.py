"""Bollinger Bands and Bollinger Bandwidth.

Used for the CHOPPY_MARKET gate:
  Fires when ADX(14) < 18 AND Bollinger bandwidth is in the lowest quartile
  (<= 25th percentile) of the last 200 closed candles.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def bollinger_bands(
    closes: Sequence[float],
    period: int = 20,
    num_std: float = 2.0,
) -> dict[str, list[float | None]]:
    """Calculates SMA middle band, upper band, lower band, and bandwidth.

    Bandwidth = (upper - lower) / middle
    """
    length = len(closes)
    mid: list[float | None] = [None] * length
    upper: list[float | None] = [None] * length
    lower: list[float | None] = [None] * length
    bandwidth: list[float | None] = [None] * length

    if length < period:
        return {"middle": mid, "upper": upper, "lower": lower, "bandwidth": bandwidth}

    for i in range(period - 1, length):
        window = closes[i - period + 1 : i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        std = math.sqrt(max(var, 0.0))

        u = m + num_std * std
        low = m - num_std * std
        bw = (u - low) / m if m > 0 else 0.0

        mid[i] = m
        upper[i] = u
        lower[i] = low
        bandwidth[i] = bw

    return {"middle": mid, "upper": upper, "lower": lower, "bandwidth": bandwidth}


def is_bandwidth_in_lowest_quartile(
    bandwidths: Sequence[float | None],
    lookback: int = 200,
    percentile: float = 0.25,
) -> tuple[bool, float, float]:
    """Check if the latest bandwidth is in the lowest quartile of the past lookback candles.

    Returns (is_lowest_quartile, current_bw, quartile_threshold).
    """
    valid = [bw for bw in bandwidths if bw is not None]
    if not valid:
        return False, 0.0, 0.0

    current = valid[-1]
    window = valid[-min(len(valid), lookback) :]
    sorted_win = sorted(window)

    idx = int(len(sorted_win) * percentile)
    threshold = sorted_win[min(idx, len(sorted_win) - 1)]

    return (current <= threshold), current, threshold
