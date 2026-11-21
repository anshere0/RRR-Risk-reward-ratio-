"""Tests for F5: Data-sanity gate, indicator fail-closed defaults, and debug endpoint."""

import math
import time

import pytest
from starlette.testclient import TestClient

from rr_engine.api.main import create_app
from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


@pytest.fixture
def base_state():
    state = MarketState()
    now = time.time()
    state.spot_index_price = 64000.0
    state.is_ws_connected = True
    state.clock.record("index", now * 1_000_000, now)
    state.clock.record("option_ticker", now * 1_000_000, now)

    spec = ProductSpec(
        id=123,
        symbol="C-BTC-64000-281126",
        contract_type="call_options",
        strike_price=64000.0,
        contract_value=0.001,
        tick_size=0.1,
        settlement_time_iso="2026-11-28T12:00:00Z",
        settlement_time_epoch=now + 86400 * 7,
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
        symbol=spec.symbol,
        mark_price=1200.0,
        best_bid=1190.0,
        best_ask=1210.0,
        bid_size=10.0,
        ask_size=10.0,
        delta=0.50,
        timestamp=now,
    )
    state.update_option_ticker(tk)
    return state, spec


@pytest.mark.anyio
async def test_flat_close_series_triggers_data_sanity_failed(base_state):
    state, spec = base_state
    # 100 identical candles -> EWMA vol sigma_5m is 0.0, ATR is 0.0
    candles = [Candle(t=1700000000 + i * 300, o=64000.0, h=64000.0, l=64000.0, c=64000.0) for i in range(100)]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(state=state, enable_journal=False)
    req = EvaluateRequest(symbol=spec.symbol, lots=1.0)
    resp = await engine.evaluate(req)

    assert resp.verdict == "NO_TRADE"
    assert resp.setup is None, "Setup must be None: never show a number built on bad data"
    codes = [r.code for r in resp.reasons]
    assert "DATA_SANITY_FAILED" in codes
    assert any(r.severity == "hard" for r in resp.reasons if r.code == "DATA_SANITY_FAILED")


@pytest.mark.anyio
async def test_zero_close_series_triggers_data_sanity_failed(base_state):
    state, spec = base_state
    # Candles where one or more closes are 0.0
    candles = [
        Candle(t=1700000000 + i * 300, o=64000.0, h=64050.0, l=63950.0, c=64000.0 if i != 50 else 0.0)
        for i in range(100)
    ]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(state=state, enable_journal=False)
    req = EvaluateRequest(symbol=spec.symbol, lots=1.0)
    resp = await engine.evaluate(req)

    assert resp.verdict == "NO_TRADE"
    assert resp.setup is None, "Setup must be None when zero close detected"
    codes = [r.code for r in resp.reasons]
    assert "DATA_SANITY_FAILED" in codes


@pytest.mark.anyio
async def test_missing_delta_triggers_greeks_unavailable(base_state):
    state, spec = base_state
    now = time.time()
    candles = [
        Candle(
            t=int(now) - (100 - i) * 300,
            o=64000.0 + 50.0 * math.sin(i / 5),
            h=64100.0 + 50.0 * math.sin(i / 5),
            l=63900.0 + 50.0 * math.sin(i / 5),
            c=64000.0 + 50.0 * math.sin(i / 5),
        )
        for i in range(100)
    ]
    state.candles_5m.set_closed_candles(candles)

    # Ticker with missing delta
    tk = OptionTicker(
        symbol=spec.symbol,
        mark_price=1200.0,
        best_bid=1190.0,
        best_ask=1210.0,
        delta=None,  # MISSING DELTA
        timestamp=now,
    )
    state.update_option_ticker(tk)

    engine = RREngine(state=state, enable_journal=False)
    req = EvaluateRequest(symbol=spec.symbol, lots=1.0)
    resp = await engine.evaluate(req)

    assert resp.verdict == "NO_TRADE"
    codes = [r.code for r in resp.reasons]
    assert "GREEKS_UNAVAILABLE" in codes


def test_debug_candles_endpoint(base_state):
    state, _ = base_state
    candles = [
        Candle(t=1700000000 + i * 300, o=64000.0 + i, h=64050.0 + i, l=63950.0 + i, c=64000.0 + i) for i in range(50)
    ]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(state=state, enable_journal=False)
    app = create_app(engine=engine, auto_start_feed=False)

    client = TestClient(app)
    response = client.get("/api/debug/candles")
    assert response.status_code == 200
    data = response.json()

    assert data["count"] == 50
    assert len(data["last_10"]) == 10
    assert "sigma_5m" in data
    assert "atr" in data
    assert "adx" in data
    assert data["zero_closes_count"] == 0
    assert data["duplicate_timestamps_count"] == 0
    assert data["missing_timestamps_count"] == 0
