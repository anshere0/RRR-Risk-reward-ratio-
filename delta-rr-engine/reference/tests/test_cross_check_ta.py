"""Optional: cross-check ATR/ADX against the independent `ta` library (pip install ta). Skipped if missing."""
import random
import pytest
ta = pytest.importorskip("ta")
pd = pytest.importorskip("pandas")
from rr_reference.indicators import atr_wilder, adx_wilder


def series(n=300, seed=7):
    random.seed(seed)
    p, out = 64000.0, []
    for i in range(n):
        o = p
        c = o + random.gauss(0, 60)
        h = max(o, c) + abs(random.gauss(0, 25))
        l = min(o, c) - abs(random.gauss(0, 25))
        out.append(dict(t=i * 300, o=o, h=h, l=l, c=c))
        p = c
    return out


def test_atr_matches_ta_after_warmup():
    c = series()
    df = pd.DataFrame(c)
    ref = ta.volatility.AverageTrueRange(df["h"], df["l"], df["c"], window=14).average_true_range()
    mine = atr_wilder(c, 14)
    # Wilder smoothing converges regardless of seed; compare far from the warm-up
    for i in range(200, 300):
        assert mine[i] == pytest.approx(ref.iloc[i], rel=1e-3)


def test_adx_matches_ta_after_warmup():
    c = series()
    df = pd.DataFrame(c)
    ref = ta.trend.ADXIndicator(df["h"], df["l"], df["c"], window=14).adx()
    mine = adx_wilder(c, 14)["adx"]
    for i in range(220, 300):
        assert mine[i] == pytest.approx(ref.iloc[i], rel=5e-3)
