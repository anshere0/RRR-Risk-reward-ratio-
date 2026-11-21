"""Realized Volatility using Exponentially Weighted Moving Average (EWMA lambda=0.94).

Conventions:
  - Computed on 5-minute closed candle close prices: r_t = ln(C_t / C_{t-1})
  - sigma_5m: per-candle 5-minute standard deviation (used for reachability cone)
  - Annualized sigma: sigma_5m * sqrt(105120)  (288 * 365 periods per year)
"""

from __future__ import annotations

import math
from collections.abc import Sequence

PERIODS_PER_DAY_5M = 288
PERIODS_PER_YEAR_5M = 365 * PERIODS_PER_DAY_5M  # 105,120


def ewma_realized_vol(
    candles: Sequence[dict],
    decay: float = 0.94,
    min_periods: int = 30,
) -> list[float | None]:
    """Calculate per-candle 5-minute realized volatility (standard deviation) via EWMA.

    Returns list aligned with candles. First min_periods entries are None.
    """
    length = len(candles)
    out: list[float | None] = [None] * length
    if length < min_periods + 1:
        return out

    # Compute log returns
    returns = [0.0] * length
    for i in range(1, length):
        c_prev = candles[i - 1]["c"]
        c_curr = candles[i]["c"]
        if c_prev > 0 and c_curr > 0:
            returns[i] = math.log(c_curr / c_prev)

    # Initialize variance with sample variance of first min_periods returns
    sample_returns = returns[1 : min_periods + 1]
    mean_r = sum(sample_returns) / len(sample_returns)
    var = sum((r - mean_r) ** 2 for r in sample_returns) / len(sample_returns)
    out[min_periods] = math.sqrt(max(var, 1e-12))

    for i in range(min_periods + 1, length):
        r2 = returns[i] ** 2
        var = decay * var + (1.0 - decay) * r2
        out[i] = math.sqrt(max(var, 1e-12))

    return out


def annualize_vol_5m(sigma_5m: float) -> float:
    """Convert 5-minute volatility to annualized decimal volatility."""
    return sigma_5m * math.sqrt(PERIODS_PER_YEAR_5M)


def deannualize_vol_to_5m(annualized_sigma: float) -> float:
    """Convert annualized volatility to 5-minute per-candle standard deviation."""
    return annualized_sigma / math.sqrt(PERIODS_PER_YEAR_5M)


def check_vol_regime_extreme(
    sigmas: Sequence[float | None],
    multiplier: float = 3.0,
    lookback: int = 2016,  # 7 days of 5m candles (7 * 288)
) -> tuple[bool, float, float]:
    """Check if latest sigma_5m > multiplier * median(sigma_5m over lookback).

    Returns (is_extreme, current_sigma, threshold).
    """
    valid = [s for s in sigmas if s is not None]
    if not valid:
        return False, 0.0, 0.0

    current = valid[-1]
    window = valid[-min(len(valid), lookback) :]
    sorted_win = sorted(window)
    mid = len(sorted_win) // 2
    median = sorted_win[mid] if len(sorted_win) % 2 != 0 else (sorted_win[mid - 1] + sorted_win[mid]) / 2.0

    threshold = multiplier * median
    return (current > threshold), current, threshold
