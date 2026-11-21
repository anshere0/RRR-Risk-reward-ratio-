"""Indicators on CLOSED candles only (never include the still-forming candle).

Candle = dict(t=<epoch seconds of candle open>, o=, h=, l=, c=).
All functions return lists aligned 1:1 with the input; warm-up entries are None.
"""
from __future__ import annotations

from typing import Sequence


def true_range(candles: Sequence[dict]) -> list:
    out = [None]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def atr_wilder(candles: Sequence[dict], n: int = 14) -> list:
    tr = true_range(candles)
    out = [None] * len(candles)
    if len(candles) <= n:
        return out
    seed = sum(tr[1:n + 1]) / n
    out[n] = seed
    for i in range(n + 1, len(candles)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def ema(values: Sequence[float], n: int) -> list:
    """EMA seeded with the SMA of the first n values (alpha = 2/(n+1))."""
    out = [None] * len(values)
    if len(values) < n:
        return out
    a = 2.0 / (n + 1.0)
    out[n - 1] = sum(values[:n]) / n
    for i in range(n, len(values)):
        out[i] = a * values[i] + (1 - a) * out[i - 1]
    return out


def adx_wilder(candles: Sequence[dict], n: int = 14) -> dict:
    """Wilder ADX. Returns dict(adx=[...], plus_di=[...], minus_di=[...])."""
    L = len(candles)
    adx = [None] * L
    pdi = [None] * L
    mdi = [None] * L
    if L < 2 * n + 1:
        return {"adx": adx, "plus_di": pdi, "minus_di": mdi}
    tr = true_range(candles)
    pdm = [0.0] * L
    mdm = [0.0] * L
    for i in range(1, L):
        up = candles[i]["h"] - candles[i - 1]["h"]
        dn = candles[i - 1]["l"] - candles[i]["l"]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    s_tr = sum(tr[1:n + 1])
    s_p = sum(pdm[1:n + 1])
    s_m = sum(mdm[1:n + 1])
    dx = [None] * L

    def _di(sp, sm, st):
        if st == 0:
            return 0.0, 0.0
        return 100.0 * sp / st, 100.0 * sm / st

    pdi[n], mdi[n] = _di(s_p, s_m, s_tr)
    den = pdi[n] + mdi[n]
    dx[n] = 0.0 if den == 0 else 100.0 * abs(pdi[n] - mdi[n]) / den
    for i in range(n + 1, L):
        s_tr = s_tr - s_tr / n + tr[i]
        s_p = s_p - s_p / n + pdm[i]
        s_m = s_m - s_m / n + mdm[i]
        pdi[i], mdi[i] = _di(s_p, s_m, s_tr)
        den = pdi[i] + mdi[i]
        dx[i] = 0.0 if den == 0 else 100.0 * abs(pdi[i] - mdi[i]) / den
    first = 2 * n - 1
    adx[first] = sum(dx[n:2 * n]) / n
    for i in range(first + 1, L):
        adx[i] = (adx[i - 1] * (n - 1) + dx[i]) / n
    return {"adx": adx, "plus_di": pdi, "minus_di": mdi}
