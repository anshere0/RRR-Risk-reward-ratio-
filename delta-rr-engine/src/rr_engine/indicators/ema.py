"""Exponential Moving Average (EMA).

Matches reference/rr_reference/indicators.py exactly.
Seeded with SMA of first n values (alpha = 2 / (n + 1)).
"""

from __future__ import annotations

from collections.abc import Sequence


def ema(values: Sequence[float], n: int) -> list[float | None]:
    """EMA seeded with the SMA of the first n values (alpha = 2/(n+1)).

    First n-1 entries are None. Entry n-1 is the SMA seed.
    """
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    a = 2.0 / (n + 1.0)
    out[n - 1] = sum(values[:n]) / n
    for i in range(n, len(values)):
        prev = out[i - 1]
        assert prev is not None
        out[i] = a * values[i] + (1 - a) * prev
    return out
