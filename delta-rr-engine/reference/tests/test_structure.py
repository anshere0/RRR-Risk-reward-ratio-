import pytest
from rr_reference.indicators import atr_wilder, adx_wilder, ema, true_range
from rr_reference.structure import confirmed_pivots, select_stop, select_target


def mk(highs, lows, closes=None):
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    return [dict(t=i * 300, o=c, h=h, l=l, c=c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]


def test_pivots_need_right_side_confirmation():
    hs = [10, 11, 12, 11, 10, 9, 10]
    ls = [9, 10, 11, 10, 9, 8, 9]
    c = mk(hs, ls)
    H, L = confirmed_pivots(c, 2)
    assert (2, 12) in H
    # the final candles cannot be pivots yet (no repainting)
    assert all(i <= len(c) - 3 for i, _ in H + L)


def test_atr_constant_range():
    c = mk([11] * 30, [9] * 30, [10] * 30)
    a = atr_wilder(c, 14)
    assert a[14] == pytest.approx(2.0) and a[-1] == pytest.approx(2.0)
    assert a[13] is None


def test_ema_of_constant():
    assert ema([5.0] * 20, 10)[-1] == pytest.approx(5.0)


def test_adx_high_in_clean_trend_low_in_chop():
    up = mk([100 + i * 2 + 1 for i in range(80)], [100 + i * 2 - 1 for i in range(80)])
    chop = mk([101 + (i % 2) for i in range(80)], [99 + (i % 2) for i in range(80)])
    assert adx_wilder(up, 14)["adx"][-1] > 40
    assert adx_wilder(chop, 14)["adx"][-1] < 20


def test_stop_for_put_is_above_swing_high_with_buffer():
    hs = [100, 101, 105, 101, 100, 99, 98, 99, 98, 97]
    ls = [98, 99, 103, 99, 98, 97, 96, 97, 96, 95]
    c = mk(hs, ls)
    s = select_stop(-1, 96.5, c, atr=2.0, k=2, lookback=36)
    assert s["sl_spot"] == pytest.approx(105 + 0.5)
    assert s["source"] == "swing"


def test_stop_too_wide_is_gated():
    hs = [100, 101, 140, 101, 100, 99, 98, 99, 98, 97]
    ls = [98, 99, 138, 99, 98, 97, 96, 97, 96, 95]
    s = select_stop(-1, 96.5, mk(hs, ls), atr=2.0, k=2)
    assert s["gate"] == "STOP_TOO_WIDE"


def test_noise_floor_widens_tight_stop():
    hs = [100, 101, 97.2, 96.8, 96.5, 96.3, 96.2, 96.1, 96.0, 96.05]
    ls = [98, 99, 96.6, 96.4, 96.2, 96.0, 95.9, 95.8, 95.7, 95.75]
    s = select_stop(-1, 96.0, mk(hs, ls), atr=2.0, k=2)
    assert s["dist"] >= 0.5 * 2.0 - 1e-9


def test_target_blocked_when_wall_is_too_close():
    c = mk([100, 101, 102, 103, 104, 105], [98, 99, 100, 101, 102, 103])
    t = select_target(-1, 100.0, c, atr=2.0, extra_levels=[(99.0, "vwap")])
    assert t["gate"] == "TARGET_BLOCKED" and t["label"] == "vwap"


def test_target_open_air_fallback():
    c = mk([100, 101, 102, 103, 104, 105], [98, 99, 100, 101, 102, 103])
    t = select_target(+1, 110.0, c, atr=2.0)
    assert t["source"] == "atr_fallback" and t["tp_spot"] == pytest.approx(113.0)
