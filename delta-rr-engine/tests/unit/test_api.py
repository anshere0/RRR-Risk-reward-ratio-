"""Unit tests for FastAPI endpoints: POST /api/evaluate and GET /api/status."""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from rr_engine.api.main import create_app
from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


@pytest.fixture
def test_app():
    state = MarketState()
    state.is_ws_connected = True
    now = time.time()
    state.spot_index_price = 64850.7
    state.perp_mark_price = 64855.0
    state.clock.record("index", now * 1_000_000, now)

    symbol = "C-BTC-64800-281126"
    spec = ProductSpec(
        id=999,
        symbol=symbol,
        contract_type="call_options",
        strike_price=64800.0,
        contract_value=0.001,
        tick_size=0.1,
        settlement_time_iso="2026-11-28T12:00:00Z",
        settlement_time_epoch=now + 86400 * 3,
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

    tk = OptionTicker(
        symbol=symbol,
        mark_price=700.0,
        best_bid=698.0,
        best_ask=702.0,
        bid_size=100.0,
        ask_size=100.0,
        delta=0.52,
        timestamp=now,
    )
    state.update_option_ticker(tk)
    state.active_symbol = symbol

    candles = [Candle(t=int(now - (100 - i) * 300), o=64800, h=64900, l=64750, c=64850) for i in range(100)]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(feed_manager=None, state=state)
    app = create_app(engine=engine, auto_start_feed=False)
    app.state.feed_manager.state = state
    return app, symbol


@pytest.mark.anyio
async def test_get_status_endpoint(test_app):
    app, _ = test_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded", "disconnected")
        assert data["version"] is not None
        assert data["config_hash"] is not None


@pytest.mark.anyio
async def test_post_evaluate_endpoint(test_app):
    app, symbol = test_app
    req_body = EvaluateRequest(symbol=symbol, lots=10.0, hold_minutes=30).model_dump()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/evaluate", json=req_body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] in ("TRADE", "NO_TRADE", "CAUTION")
        assert data["validation_status"] == "UNVALIDATED"
        assert "provenance" in data
        assert "reasons" in data


@pytest.mark.anyio
async def test_get_option_chain_endpoint(test_app):
    app, _ = test_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/option-chain")
        assert resp.status_code == 200
        data = resp.json()
        assert "spot_price" in data
        assert "expiries" in data
        assert "strikes" in data
