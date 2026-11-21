"""Unit tests for every NO_TRADE gate in docs/04_NO_TRADE_RULES.md.

Mandatory requirement: Every gate has a passing test and a failing test.
"""

from rr_engine.engine import gates

# ============================================================================
# Section A: Data Integrity
# ============================================================================


def test_gate_feed_disconnected():
    assert not gates.gate_feed_disconnected(True).fired
    res = gates.gate_feed_disconnected(False)
    assert res.fired
    assert res.severity == "hard"
    assert res.code == "FEED_DISCONNECTED"


def test_gate_data_stale():
    assert not gates.gate_data_stale(1.5, limit_seconds=3.0).fired
    res = gates.gate_data_stale(3.5, limit_seconds=3.0)
    assert res.fired
    assert res.code == "DATA_STALE"


def test_gate_candle_gap():
    assert not gates.gate_candle_gap(False).fired
    res = gates.gate_candle_gap(True, "Missing 300s candle at 1700000000")
    assert res.fired
    assert res.code == "CANDLE_GAP"


def test_gate_product_not_tradable():
    assert not gates.gate_product_not_tradable("live", "operational").fired
    assert gates.gate_product_not_tradable("expired", "operational").fired
    assert gates.gate_product_not_tradable("live", "maintenance").fired


def test_gate_symbol_invalid():
    assert not gates.gate_symbol_invalid("C-BTC-65000-281126", is_btc_option=True).fired
    res = gates.gate_symbol_invalid("ETH-PERP", is_btc_option=False)
    assert res.fired
    assert res.code == "SYMBOL_INVALID"


def test_gate_quote_invalid():
    assert not gates.gate_quote_invalid(bid=620.0, ask=630.0).fired
    assert gates.gate_quote_invalid(bid=0.0, ask=630.0).fired  # Zero bid
    assert gates.gate_quote_invalid(bid=640.0, ask=630.0).fired  # Crossed book
    assert gates.gate_quote_invalid(bid=620.0, ask=0.0).fired  # Zero ask


def test_gate_index_perp_divergence():
    assert not gates.gate_index_perp_divergence(0.10, limit_pct=0.25).fired
    res = gates.gate_index_perp_divergence(0.35, limit_pct=0.25)
    assert res.fired
    assert res.code == "INDEX_PERP_DIVERGENCE"


def test_gate_model_calibration_failed():
    assert not gates.gate_model_calibration_failed(None).fired
    res = gates.gate_model_calibration_failed("Price below intrinsic")
    assert res.fired
    assert res.code == "MODEL_CALIBRATION_FAILED"


def test_gate_greeks_mismatch_severe():
    assert not gates.gate_greeks_mismatch_severe(0.05, limit=0.15).fired
    res = gates.gate_greeks_mismatch_severe(0.18, limit=0.15)
    assert res.fired
    assert res.code == "GREEKS_MISMATCH_SEVERE"


def test_gate_clock_skew():
    assert not gates.gate_clock_skew(0.8, limit_seconds=2.0).fired
    res = gates.gate_clock_skew(2.5, limit_seconds=2.0)
    assert res.fired
    assert res.code == "CLOCK_SKEW"


# ============================================================================
# Section B: Option Quality
# ============================================================================


def test_gate_expiry_too_close():
    # 2 hours left, 30 min hold -> pass
    assert not gates.gate_expiry_too_close(7200, min_minutes=60, hold_minutes=30).fired
    # 45 minutes left (< 60m min) -> fail
    res = gates.gate_expiry_too_close(2700, min_minutes=60, hold_minutes=30)
    assert res.fired
    assert res.code == "EXPIRY_TOO_CLOSE"
    # 65 minutes left, but 60m hold + 10m buffer = 70m required -> fail
    assert gates.gate_expiry_too_close(3900, min_minutes=60, hold_minutes=60, buffer_minutes=10).fired


def test_gate_spread_too_wide():
    assert not gates.gate_spread_too_wide(0.8, limit_pct=1.5).fired
    res = gates.gate_spread_too_wide(2.1, limit_pct=1.5)
    assert res.fired
    assert res.code == "SPREAD_TOO_WIDE"


def test_gate_spread_eats_reward():
    assert not gates.gate_spread_eats_reward(spread_round_trip_usd=1.0, gross_reward_usd=10.0, limit_share=0.20).fired
    res = gates.gate_spread_eats_reward(spread_round_trip_usd=3.0, gross_reward_usd=10.0, limit_share=0.20)
    assert res.fired
    assert res.code == "SPREAD_EATS_REWARD"


def test_gate_depth_too_thin():
    # 10 lots -> need ask >= 15, bid >= 10
    assert not gates.gate_depth_too_thin(ask_size=50, bid_size=50, lots=10).fired
    res = gates.gate_depth_too_thin(ask_size=10, bid_size=50, lots=10)
    assert res.fired
    assert res.code == "DEPTH_TOO_THIN"


def test_gate_premium_too_low():
    assert not gates.gate_premium_too_low(150.0, min_premium=20.0).fired
    res = gates.gate_premium_too_low(15.0, min_premium=20.0)
    assert res.fired
    assert res.code == "PREMIUM_TOO_LOW"


def test_gate_fees_eat_reward():
    assert not gates.gate_fees_eat_reward(total_fees_usd=1.0, gross_reward_usd=10.0, limit_share=0.30).fired
    res = gates.gate_fees_eat_reward(total_fees_usd=4.0, gross_reward_usd=10.0, limit_share=0.30)
    assert res.fired
    assert res.code == "FEES_EAT_REWARD"


# ============================================================================
# Section C: Market Context
# ============================================================================


def test_gate_choppy_market():
    # ADX high -> not choppy
    assert not gates.gate_choppy_market(adx=28.0, is_lowest_quartile_bw=True, adx_limit=18.0).fired
    # Low bandwidth but ADX high -> not choppy
    assert not gates.gate_choppy_market(adx=22.0, is_lowest_quartile_bw=False, adx_limit=18.0).fired
    # Both ADX low AND lowest quartile bandwidth -> choppy!
    res = gates.gate_choppy_market(adx=14.0, is_lowest_quartile_bw=True, adx_limit=18.0)
    assert res.fired
    assert res.code == "CHOPPY_MARKET"


def test_gate_against_trend():
    # Bullish (call, +1) with EMA20 > EMA50 -> trend aligned
    assert not gates.gate_against_trend(direction=1, ema20=65000, ema50=64000, adx=30).fired
    # Bullish call when EMA20 < EMA50 and ADX > 25 -> AGAINST TREND!
    res = gates.gate_against_trend(direction=1, ema20=64000, ema50=65000, adx=30)
    assert res.fired
    assert res.code == "AGAINST_TREND"
    # Bearish put when EMA20 > EMA50 and ADX > 25 -> AGAINST TREND!
    assert gates.gate_against_trend(direction=-1, ema20=65000, ema50=64000, adx=30).fired


def test_gate_spike_just_happened():
    # Calm candle
    assert not gates.gate_spike_just_happened(
        candles_since_spike=10, last_candle_range=50, atr=50, spike_mult=2.5
    ).fired
    # Massive candle range > 2.5x ATR
    res = gates.gate_spike_just_happened(candles_since_spike=10, last_candle_range=200, atr=50, spike_mult=2.5)
    assert res.fired
    assert res.code == "SPIKE_JUST_HAPPENED"


def test_gate_vol_regime_extreme():
    assert not gates.gate_vol_regime_extreme(is_extreme=False, current_vol=0.003, threshold=0.009).fired
    res = gates.gate_vol_regime_extreme(is_extreme=True, current_vol=0.012, threshold=0.009)
    assert res.fired
    assert res.code == "VOL_REGIME_EXTREME"


def test_gate_event_window():
    events = [{"start_utc": 1000, "end_utc": 2000, "label": "US CPI"}]
    assert not gates.gate_event_window(now_epoch=500, event_windows=events).fired
    res = gates.gate_event_window(now_epoch=1500, event_windows=events)
    assert res.fired
    assert res.code == "EVENT_WINDOW"
    assert "US CPI" in res.message


# ============================================================================
# Section D: Trade Geometry & Warnings
# ============================================================================


def test_gate_stop_too_wide():
    assert not gates.gate_stop_too_wide(1.2, max_atr=2.0).fired
    res = gates.gate_stop_too_wide(2.4, max_atr=2.0)
    assert res.fired
    assert res.code == "STOP_TOO_WIDE"


def test_gate_target_blocked():
    assert not gates.gate_target_blocked(1.5, min_atr=0.75).fired
    res = gates.gate_target_blocked(0.4, min_atr=0.75, barrier_label="VWAP")
    assert res.fired
    assert res.code == "TARGET_BLOCKED"


def test_gate_target_unreachable_in_window():
    assert not gates.gate_target_unreachable_in_window(is_reachable=True, target_dist=300, max_reach=500).fired
    res = gates.gate_target_unreachable_in_window(is_reachable=False, target_dist=800, max_reach=500)
    assert res.fired
    assert res.code == "TARGET_UNREACHABLE_IN_WINDOW"


def test_gate_net_rr_too_low():
    assert not gates.gate_net_rr_too_low(net_rr=2.1, min_rr=1.5).fired
    res = gates.gate_net_rr_too_low(net_rr=0.85, min_rr=1.5, risk_usd=1.5, reward_usd=1.2)
    assert res.fired
    assert res.code == "NET_RR_TOO_LOW"


def test_gate_breakeven_too_high():
    assert not gates.gate_breakeven_too_high(0.35, max_breakeven=0.45).fired
    res = gates.gate_breakeven_too_high(0.55, max_breakeven=0.45)
    assert res.fired
    assert res.code == "BREAKEVEN_TOO_HIGH"


def test_gate_risk_over_budget():
    assert not gates.gate_risk_over_budget(risk_usd=10.0, max_risk_usd=20.0).fired
    res = gates.gate_risk_over_budget(risk_usd=30.0, max_risk_usd=20.0, max_lots=5)
    assert res.fired
    assert res.code == "RISK_OVER_BUDGET"


def test_gate_strike_not_atm_warning():
    # ATM delta ~ 0.50 -> no warning
    assert not gates.gate_strike_not_atm(0.52).fired
    # Deep OTM delta = 0.15 -> warning only
    res = gates.gate_strike_not_atm(0.15)
    assert res.fired
    assert res.severity == "warning"
    assert res.code == "STRIKE_NOT_ATM"
