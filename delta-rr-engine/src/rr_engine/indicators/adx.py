"""Wilder Average Directional Index (ADX) on closed candles.

Matches reference/rr_reference/indicators.py exactly.
Returns dict with 'adx', 'plus_di', 'minus_di'.
"""

from __future__ import annotations

from collections.abc import Sequence

from rr_engine.indicators.atr import true_range


def adx_wilder(candles: Sequence[dict], n: int = 14) -> dict[str, list[float | None]]:
    """Wilder ADX. Returns dict(adx=[...], plus_di=[...], minus_di=[...]).

    First valid ADX value is at index 2*n - 1.
    """
    length = len(candles)
    adx: list[float | None] = [None] * length
    pdi: list[float | None] = [None] * length
    mdi: list[float | None] = [None] * length
    if length < 2 * n + 1:
        return {"adx": adx, "plus_di": pdi, "minus_di": mdi}

    tr = true_range(candles)
    pdm = [0.0] * length
    mdm = [0.0] * length
    for i in range(1, length):
        up = candles[i]["h"] - candles[i - 1]["h"]
        dn = candles[i - 1]["l"] - candles[i]["l"]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0

    s_tr = sum(tr[1 : n + 1])  # type: ignore[arg-type]
    s_p = sum(pdm[1 : n + 1])
    s_m = sum(mdm[1 : n + 1])
    dx: list[float | None] = [None] * length

    def _di(sp: float, sm: float, st: float) -> tuple[float, float]:
        if st == 0:
            return 0.0, 0.0
        return 100.0 * sp / st, 100.0 * sm / st

    pdi[n], mdi[n] = _di(s_p, s_m, s_tr)
    den = pdi[n] + mdi[n]  # type: ignore[operator]
    dx[n] = 0.0 if den == 0 else 100.0 * abs(pdi[n] - mdi[n]) / den  # type: ignore[operator]

    for i in range(n + 1, length):
        s_tr = s_tr - s_tr / n + tr[i]  # type: ignore[operator]
        s_p = s_p - s_p / n + pdm[i]
        s_m = s_m - s_m / n + mdm[i]
        pdi[i], mdi[i] = _di(s_p, s_m, s_tr)
        den = pdi[i] + mdi[i]  # type: ignore[operator]
        dx[i] = 0.0 if den == 0 else 100.0 * abs(pdi[i] - mdi[i]) / den  # type: ignore[operator]

    first = 2 * n - 1
    adx[first] = sum(dx[n : 2 * n]) / n  # type: ignore[arg-type]
    for i in range(first + 1, length):
        adx[i] = (adx[i - 1] * (n - 1) + dx[i]) / n  # type: ignore[operator]

    return {"adx": adx, "plus_di": pdi, "minus_di": mdi}
