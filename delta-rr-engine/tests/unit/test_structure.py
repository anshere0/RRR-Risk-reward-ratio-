"""Unit tests for Phase 2 structure: pivots, levels, stop and target selection, and no-repaint proof."""

import pytest

from reference.rr_reference import structure as ref_struct
from rr_engine.structure.levels import build_extra_levels, prior_day_high_low, round_numbers
from rr_engine.structure.pivots import confirmed_pivots
from rr_engine.structure.stop_target import check_target_reachability, select_stop, select_target


def mk_candles(highs, lows, closes=None, base_t=1700000000):
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows, strict=True)]
    return [
        {"t": base_t + i * 300, "o": c, "h": h, "l": l, "c": c}
        for i, (h, l, c) in enumerate(zip(highs, lows, closes, strict=True))
    ]


def test_structure_matches_reference_exactly():
    hs = [100, 102, 105, 101, 100, 99, 98, 99, 98, 97]
    ls = [98, 99, 103, 99, 98, 97, 96, 97, 96, 95]
    c = mk_candles(hs, ls)

    # 1. Pivots match reference
    p_h, p_l = confirmed_pivots(c, k=2)
    ref_h, ref_l = ref_struct.confirmed_pivots(c, k=2)
    assert p_h == ref_h
    assert p_l == ref_l

    # 2. Select stop matches reference (put & call)
    stop_put = select_stop(-1, 96.5, c, atr=2.0)
    ref_stop_put = ref_struct.select_stop(-1, 96.5, c, atr=2.0)
    assert stop_put == ref_stop_put

    stop_call = select_stop(1, 98.0, c, atr=2.0)
    ref_stop_call = ref_struct.select_stop(1, 98.0, c, atr=2.0)
    assert stop_call == ref_stop_call

    # 3. Select target matches reference
    tp_put = select_target(-1, 100.0, c, atr=2.0, extra_levels=[(99.0, "vwap")])
    ref_tp_put = ref_struct.select_target(-1, 100.0, c, atr=2.0, extra_levels=[(99.0, "vwap")])
    assert tp_put == ref_tp_put


def test_no_repaint_proof():
    """CRITICAL TEST: Proves that confirmed pivots NEVER repaint.

    Appending new candles to the series must never alter, move, or remove
    any pivot that was already confirmed.
    """
    initial_highs = [100, 102, 108, 104, 101, 98, 96, 99, 105, 101, 97]
    initial_lows = [98, 99, 105, 100, 98, 95, 93, 96, 102, 98, 94]
    candles = mk_candles(initial_highs, initial_lows)

    k = 2
    # Confirmed pivots in initial series
    initial_piv_h, initial_piv_l = confirmed_pivots(candles, k=k)
    assert len(initial_piv_h) > 0 or len(initial_piv_l) > 0

    # Record all confirmed pivots as sets of (index, price)
    confirmed_h_set = set(initial_piv_h)
    confirmed_l_set = set(initial_piv_l)

    # Now append 25 subsequent candles with extreme volatile price action
    future_highs = [95 + (i * 3) % 20 for i in range(25)]
    future_lows = [h - 4 for h in future_highs]
    future_candles = mk_candles(future_highs, future_lows, base_t=candles[-1]["t"] + 300)

    # Incrementally append candles one by one (simulating live streaming market)
    for next_candle in future_candles:
        candles.append(next_candle)
        new_piv_h, new_piv_l = confirmed_pivots(candles, k=k)

        # Every previously confirmed pivot MUST still be present identically!
        for h in confirmed_h_set:
            assert h in new_piv_h, f"Swing high at index {h[0]} repainted!"
        for l in confirmed_l_set:
            assert l in new_piv_l, f"Swing low at index {l[0]} repainted!"

        # Any newly confirmed pivots also become permanent
        confirmed_h_set.update(new_piv_h)
        confirmed_l_set.update(new_piv_l)


def test_stop_noise_floor_widening():
    hs = [100, 101, 97.2, 96.8, 96.5, 96.3, 96.2, 96.1, 96.0, 96.05]
    ls = [98, 99, 96.6, 96.4, 96.2, 96.0, 95.9, 95.8, 95.7, 95.75]
    c = mk_candles(hs, ls)
    s = select_stop(-1, 96.0, c, atr=2.0, k=2)
    assert s["dist"] >= 0.5 * 2.0 - 1e-9

    # If fallback or pivot is tighter than noise floor, it widens
    s_tight = select_stop(-1, 96.0, c, atr=2.0, k=2, fallback_atr=0.2)
    assert s_tight["widened_to_noise_floor"]
    assert s_tight["dist"] == pytest.approx(1.0)
    assert s_tight["sl_spot"] == pytest.approx(97.0)


def test_stop_too_wide_gate():
    hs = [100, 101, 140, 101, 100, 99, 98, 99, 98, 97]
    ls = [98, 99, 138, 99, 98, 97, 96, 97, 96, 95]
    c = mk_candles(hs, ls)
    s = select_stop(-1, 96.5, c, atr=2.0, k=2, max_atr=2.0)

    assert s["gate"] == "STOP_TOO_WIDE"


def test_target_blocked_gate():
    c = mk_candles([100, 101, 102, 103, 104, 105], [98, 99, 100, 101, 102, 103])
    t = select_target(-1, 100.0, c, atr=2.0, min_tp_atr=0.75, extra_levels=[(99.0, "round_500")])

    assert t["gate"] == "TARGET_BLOCKED"
    assert t["label"] == "round_500"


def test_target_open_air_fallback():
    c = mk_candles([100, 101, 102, 103, 104, 105], [98, 99, 100, 101, 102, 103])
    t = select_target(1, 110.0, c, atr=2.0, fallback_atr=1.5)

    assert t["source"] == "atr_fallback"
    assert t["tp_spot"] == pytest.approx(113.0)
    assert t["gate"] is None


def test_target_reachability():
    # Spot 64000, 5m sigma = 0.003 (0.3% per candle)
    # Holding 6 candles (30m) -> 1.5 * 64000 * 0.003 * sqrt(6) ~ 705.4 price points
    is_reach, dist, max_r = check_target_reachability(
        target_dist=500.0, spot=64000.0, sigma_5m=0.003, holding_candles=6
    )
    assert is_reach
    assert dist < max_r

    # Target 1500 points away -> unreachable in 30 minutes
    is_reach, dist, max_r = check_target_reachability(
        target_dist=1500.0, spot=64000.0, sigma_5m=0.003, holding_candles=6
    )
    assert not is_reach
    assert dist > max_r


def test_build_extra_levels():
    # Build candles spanning previous day and current day
    day1_t = 19675 * 86400
    day2_t = (19675 + 1) * 86400

    candles = [
        {"t": day1_t + 0, "h": 65500, "l": 64500, "c": 65000},
        {"t": day1_t + 300, "h": 65800, "l": 64800, "c": 65200},
        {"t": day2_t + 0, "h": 65100, "l": 64900, "c": 65000},
    ]

    pdh, pdl = prior_day_high_low(candles)
    assert pdh == 65800
    assert pdl == 64500

    rounds = round_numbers(65020, steps=[500, 1000])
    prices = [p for p, _ in rounds]
    assert 65000 in prices
    assert 65500 in prices
    assert 66000 in prices

    levels = build_extra_levels(candles, vwap=64950.0, spot=65000.0)
    labels = [lab for _, lab in levels]
    assert "prior_day_high" in labels
    assert "prior_day_low" in labels
    assert "session_vwap" in labels
    assert "round_500" in labels
