"""Tests for F6: Structural integrity of stop and target.

Enforces:
  1. Stop comes from structure, not ratio (no 1.15 ATR clamp).
  2. If structural stop > stop_max_atr -> STOP_TOO_WIDE (hard).
  3. Target is nearest opposing structure (no retail skipping over barriers).
  4. If wall < target_min_atr -> TARGET_BLOCKED (hard).
  5. Standard pivots anchored to previous UTC day (00:00-24:00 UTC), no R3/S3 targets.
  6. 63000-put scenario returns STOP_TOO_WIDE or TARGET_BLOCKED instead of stretched RR.
"""

import math
import time

import pytest

from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec
from rr_engine.structure.levels import standard_pivot_points


@pytest.mark.anyio
async def test_63000_put_scenario_rejects_with_stop_too_wide_or_target_blocked():
    """Reproduce the 63000-put scenario from live trade screenshot."""
    state = MarketState()
    now = time.time()
    state.is_ws_connected = True
    spot = 62765.5
    state.spot_index_price = spot
    state.clock.record("index", now * 1_000_000, now)
    state.clock.record("option_ticker", now * 1_000_000, now)

    symbol = "P-BTC-63000-201126"
    spec = ProductSpec(
        id=63000,
        symbol=symbol,
        contract_type="put_options",
        strike_price=63000.0,
        contract_value=0.001,
        tick_size=0.1,
        settlement_time_iso="2026-11-20T12:00:00Z",
        settlement_time_epoch=now + 86400 * 2,
        state="live",
        trading_status="operational",
        taker_commission_rate=0.0003,
        maker_commission_rate=0.0001,
        premium_commission_rate=0.10,
        spot_index_symbol=".DEXBTUSD",
        underlying_asset_symbol="BTC",
        fetched_at=now,
    )
    state.products.set(spec)

    tk = OptionTicker(
        symbol=symbol,
        mark_price=765.0,
        best_bid=760.0,
        best_ask=765.0,
        bid_size=10.0,
        ask_size=10.0,
        delta=-0.52,
        timestamp=now,
    )
    state.update_option_ticker(tk)

    # 120 candles with ATR ~ 180, but swing high at 63400 (which is (63400 - 62765.5)/180 ~ 3.5 ATR away)
    # and a wall at 62700 (0.36 ATR away)
    candles = []
    for i in range(120):
        t = int(now - (120 - i) * 300)
        if i == 100:
            c = Candle(t=t, o=63300.0, h=63450.0, l=63200.0, c=63350.0)
        elif i > 100:
            base = 62700.0 + 30.0 * math.sin(i / 1.5)
            c = Candle(t=t, o=base - 20, h=base + 30, l=base - 30, c=base)
        else:
            base = 62765.5 + 80.0 * math.sin(i / 1.5)
            c = Candle(t=t, o=base - 30, h=base + 90, l=base - 90, c=base)
        candles.append(c)

    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(state=state, enable_journal=False)
    req = EvaluateRequest(symbol=symbol, lots=10.0, max_risk_usd=100.0)
    resp = await engine.evaluate(req)

    # Must be NO_TRADE
    assert resp.verdict == "NO_TRADE"
    hard_codes = {r.code for r in resp.reasons if r.severity == "hard"}
    # Must fail on STOP_TOO_WIDE or TARGET_BLOCKED, never a stretched 1:2.65!
    assert ("STOP_TOO_WIDE" in hard_codes) or ("TARGET_BLOCKED" in hard_codes)


def test_standard_pivots_anchor_to_prior_utc_day_no_r3_s3():
    """Verify standard pivots anchor to prior UTC 00:00-24:00 day and exclude R3/S3."""
    # Construct 2 days of 5m candles (288 candles per day)
    day_seconds = 86400
    prior_day_start = 1700006400 // day_seconds * day_seconds  # e.g. 00:00 UTC
    cur_day_start = prior_day_start + day_seconds

    candles = []
    # Prior UTC day candles: High 65000, Low 60000, Close 62000
    for i in range(288):
        t = prior_day_start + i * 300
        h = 65000.0 if i == 50 else 61000.0
        l = 60000.0 if i == 150 else 60500.0
        c = 62000.0 if i == 287 else 60800.0
        candles.append({"t": t, "o": 60800.0, "h": h, "l": l, "c": c})

    # Current UTC day (partial): 50 candles
    for i in range(50):
        t = cur_day_start + i * 300
        candles.append({"t": t, "o": 62000.0, "h": 66000.0, "l": 61900.0, "c": 64000.0})

    pivots = standard_pivot_points(candles)
    assert "P" in pivots
    assert "R1" in pivots
    assert "S1" in pivots
    assert "R2" in pivots
    assert "S2" in pivots
    # F6: R3 and S3 must NEVER be present as target levels
    assert "R3" not in pivots
    assert "S3" not in pivots

    # Prior day H=65000, L=60000, C=62000 -> P = (65000 + 60000 + 62000) / 3 = 62333.3
    assert pivots["P"] == pytest.approx(62333.3, abs=0.1)
