"""Chaos and failure mode tests.

Verifies that unexpected exceptions, stale tickers, crossed books,
missing candles, and calibration failures FAIL CLOSED to NO_TRADE.
Never output a number built on bad data.
"""

import time

import pytest

from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


@pytest.fixture
def clean_engine():
    """Sets up an in-memory engine with valid market state and 100 closed candles."""
    state = MarketState()
    state.is_ws_connected = True
    now = time.time()
    state.spot_index_price = 64850.7
    state.perp_mark_price = 64860.0
    state.clock.record("index", now * 1_000_000, now)

    # 100 closed candles
    import math

    candles = [
        Candle(
            t=int(now - (100 - i) * 300),
            o=64800 + 100 * math.sin(i / 1.5),
            h=64950 + 100 * math.sin(i / 1.5),
            l=64700 + 100 * math.sin(i / 1.5),
            c=64850 + 100 * math.sin(i / 1.5),
        )
        for i in range(100)
    ]
    state.candles_5m.set_closed_candles(candles)

    # Option product spec
    symbol = "C-BTC-65000-281126"
    spec = ProductSpec(
        id=123,
        symbol=symbol,
        contract_type="call_options",
        strike_price=65000.0,
        contract_value=0.001,
        tick_size=0.1,
        settlement_time_iso="2026-11-28T12:00:00Z",
        settlement_time_epoch=now + 86400 * 5,
        state="live",
        trading_status="operational",
        taker_commission_rate=0.0001,
        maker_commission_rate=0.0001,
        premium_commission_rate=0.035,
        spot_index_symbol=".DEXBTUSD",
        underlying_asset_symbol="BTC",
        fetched_at=now,
    )
    state.products.set(spec)

    # Option ticker
    tk = OptionTicker(
        symbol=symbol,
        mark_price=629.0,
        best_bid=627.0,
        best_ask=631.0,
        bid_size=100.0,
        ask_size=100.0,
        delta=0.52,
        timestamp=now,
    )
    state.update_option_ticker(tk)
    state.active_symbol = symbol

    engine = RREngine(feed_manager=None, state=state)
    return engine, state, symbol


@pytest.mark.anyio
async def test_chaos_stale_ticker_fails_closed(clean_engine):
    engine, state, symbol = clean_engine

    # Make ticker 5 seconds old (> 3s limit)
    old_time = time.time() - 5.0
    tk = state.get_ticker(symbol)
    assert tk is not None
    tk.timestamp = old_time
    state.clock.record("option_ticker", old_time * 1_000_000, old_time)

    resp = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=1.0))
    assert resp.verdict == "NO_TRADE"
    assert resp.setup is None
    codes = [r.code for r in resp.reasons]
    assert "DATA_STALE" in codes


@pytest.mark.anyio
async def test_chaos_crossed_book_fails_closed(clean_engine):
    engine, state, symbol = clean_engine

    # Invert quotes: ask (600) < bid (650)
    tk = state.get_ticker(symbol)
    assert tk is not None
    tk.best_bid = 650.0
    tk.best_ask = 600.0

    resp = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=1.0))
    assert resp.verdict == "NO_TRADE"
    assert resp.setup is None
    codes = [r.code for r in resp.reasons]
    assert "QUOTE_INVALID" in codes


@pytest.mark.anyio
async def test_chaos_calibration_failure_fails_closed(clean_engine):
    engine, state, symbol = clean_engine

    # Set mark below intrinsic: call strike 65000, spot 68000 (intrinsic 3000), mark price 10.0
    state.spot_index_price = 68000.0
    state.perp_mark_price = 68000.0  # Keep perp aligned to prevent divergence gate
    tk = state.get_ticker(symbol)
    assert tk is not None
    tk.mark_price = 10.0
    tk.best_bid = 9.0
    tk.best_ask = 11.0

    resp = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=1.0))
    assert resp.verdict == "NO_TRADE"
    assert resp.setup is None
    codes = [r.code for r in resp.reasons]
    assert "MODEL_CALIBRATION_FAILED" in codes


@pytest.mark.anyio
async def test_chaos_engine_exception_fails_closed(clean_engine):
    engine, state, symbol = clean_engine

    # Corrupt internal state to trigger unhandled exception
    state.candles_5m = None  # type: ignore[assignment]

    resp = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=1.0))
    assert resp.verdict == "NO_TRADE"
    assert resp.setup is None
    codes = [r.code for r in resp.reasons]
    assert "ENGINE_ERROR" in codes


@pytest.mark.anyio
async def test_chaos_feed_disconnected_fails_closed(clean_engine):
    engine, state, symbol = clean_engine

    state.is_ws_connected = False
    resp = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=1.0))
    assert resp.verdict == "NO_TRADE"
    assert resp.setup is None
    codes = [r.code for r in resp.reasons]
    assert "FEED_DISCONNECTED" in codes
