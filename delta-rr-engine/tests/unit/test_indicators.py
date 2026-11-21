"""Unit tests for Phase 2 indicators: ATR, EMA, ADX, EWMA vol, Bollinger, and VWAP."""

import random

import pandas as pd
import pytest
import ta

from reference.rr_reference import indicators as ref_ind
from rr_engine.indicators.adx import adx_wilder
from rr_engine.indicators.atr import atr_wilder, true_range
from rr_engine.indicators.bollinger import bollinger_bands, is_bandwidth_in_lowest_quartile
from rr_engine.indicators.ema import ema
from rr_engine.indicators.realized_vol import (
    annualize_vol_5m,
    check_vol_regime_extreme,
    deannualize_vol_to_5m,
    ewma_realized_vol,
)
from rr_engine.indicators.vwap import session_vwap


def generate_candle_series(n: int = 300, seed: int = 42) -> list[dict]:
    random.seed(seed)
    p = 64000.0
    out = []
    for i in range(n):
        o = p
        c = o + random.gauss(0, 50)
        h = max(o, c) + abs(random.gauss(0, 30))
        l = min(o, c) - abs(random.gauss(0, 30))
        v = abs(random.gauss(10.0, 5.0))
        out.append({"t": 1700000000 + i * 300, "o": o, "h": h, "l": l, "c": c, "v": v})
        p = c
    return out


def test_indicators_match_reference_exactly():
    candles = generate_candle_series(200)

    # 1. True Range
    tr_prod = true_range(candles)
    tr_ref = ref_ind.true_range(candles)
    assert tr_prod == tr_ref

    # 2. Wilder ATR(14)
    atr_prod = atr_wilder(candles, 14)
    atr_ref = ref_ind.atr_wilder(candles, 14)
    assert atr_prod == atr_ref

    # 3. EMA(20) and EMA(50)
    closes = [c["c"] for c in candles]
    assert ema(closes, 20) == ref_ind.ema(closes, 20)
    assert ema(closes, 50) == ref_ind.ema(closes, 50)

    # 4. ADX(14)
    adx_prod = adx_wilder(candles, 14)
    adx_ref = ref_ind.adx_wilder(candles, 14)
    assert adx_prod["adx"] == adx_ref["adx"]
    assert adx_prod["plus_di"] == adx_ref["plus_di"]
    assert adx_prod["minus_di"] == adx_ref["minus_di"]


def test_atr_cross_check_ta_library():
    candles = generate_candle_series(300, seed=7)
    df = pd.DataFrame(candles)
    ref = ta.volatility.AverageTrueRange(df["h"], df["l"], df["c"], window=14).average_true_range()
    mine = atr_wilder(candles, 14)

    # Wilder smoothing converges regardless of initial seed; compare after warm-up
    for i in range(200, 300):
        assert mine[i] == pytest.approx(ref.iloc[i], rel=1e-3)


def test_adx_cross_check_ta_library():
    candles = generate_candle_series(300, seed=7)
    df = pd.DataFrame(candles)
    ref = ta.trend.ADXIndicator(df["h"], df["l"], df["c"], window=14).adx()
    mine = adx_wilder(candles, 14)["adx"]

    for i in range(220, 300):
        assert mine[i] == pytest.approx(ref.iloc[i], rel=5e-3)


def test_ewma_realized_vol():
    candles = generate_candle_series(100)
    vols = ewma_realized_vol(candles, decay=0.94, min_periods=30)
    assert len(vols) == len(candles)
    assert vols[0] is None
    assert vols[29] is None
    assert vols[30] is not None
    assert vols[30] > 0

    # Test round trip annualization
    ann = annualize_vol_5m(vols[-1])
    deann = deannualize_vol_to_5m(ann)
    assert deann == pytest.approx(vols[-1], rel=1e-9)

    # Test extreme regime check
    is_ext, cur, thresh = check_vol_regime_extreme(vols, multiplier=3.0)
    assert not is_ext  # Random normal walk has steady vol
    assert cur > 0
    assert thresh > cur


def test_bollinger_bands_and_bandwidth():
    candles = generate_candle_series(100)
    closes = [c["c"] for c in candles]
    bb = bollinger_bands(closes, period=20, num_std=2.0)

    assert len(bb["middle"]) == 100
    assert bb["middle"][18] is None
    assert bb["middle"][19] is not None

    for i in range(19, 100):
        u, m, l, bw = bb["upper"][i], bb["middle"][i], bb["lower"][i], bb["bandwidth"][i]
        assert u > m > l
        assert bw == pytest.approx((u - l) / m, rel=1e-6)

    # Test lowest quartile check
    is_low, cur_bw, thresh = is_bandwidth_in_lowest_quartile(bb["bandwidth"], lookback=50, percentile=0.25)
    assert cur_bw > 0
    assert thresh > 0


def test_session_vwap_anchors_to_utc_midnight():
    # Construct candles spanning 2 UTC days:
    # Day 1: epoch 1700000000 -> 1700000000 // 86400 = 19675
    day1_start = 19675 * 86400
    day2_start = day1_start + 86400

    candles = [
        {"t": day1_start + 0, "h": 105, "l": 95, "c": 100, "v": 10.0},
        {"t": day1_start + 300, "h": 110, "l": 100, "c": 105, "v": 20.0},
        # Day 2 candle (resets cumulative volume)
        {"t": day2_start + 0, "h": 205, "l": 195, "c": 200, "v": 5.0},
    ]

    vwap = session_vwap(candles)
    # Day 1 first candle TP = 100, v = 10 -> VWAP = 100.0
    assert vwap[0] == pytest.approx(100.0)
    # Day 1 second candle TP = 105, v = 20 -> cum_pv = 1000 + 2100 = 3100, cum_v = 30 -> VWAP = 103.333
    assert vwap[1] == pytest.approx(3100.0 / 30.0)
    # Day 2 first candle TP = 200, v = 5 -> RESET, VWAP = 200.0
    assert vwap[2] == pytest.approx(200.0)
