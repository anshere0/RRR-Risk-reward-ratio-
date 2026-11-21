"""Unit tests for Phase 5 HUD static files and endpoints."""

import time
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from rr_engine.api.main import create_app
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


@pytest.fixture
def hud_test_app():
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

    engine = RREngine(feed_manager=None, state=state)
    app = create_app(engine=engine, auto_start_feed=False)
    app.state.feed_manager.state = state
    # Mock manager.rest.list_products for /api/options
    app.state.feed_manager.rest.list_products = AsyncMock(
        return_value=[
            {
                "symbol": symbol,
                "contract_type": "call_options",
                "strike_price": "64800",
                "settlement_time": "2026-11-28T12:00:00Z",
            }
        ]
    )
    return app, symbol


@pytest.mark.anyio
async def test_root_redirect_to_hud(hud_test_app):
    app, _ = hud_test_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code in (301, 302, 307)
        assert "/hud/" in resp.headers.get("location", "")


@pytest.mark.anyio
async def test_hud_static_html(hud_test_app):
    app, _ = hud_test_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/hud/")
        assert resp.status_code == 200
        html = resp.text
        assert "DELTA BTC RR" in html
        assert "UNVALIDATED ENGINE" in html
        assert "Decision support, not financial advice" in html
        assert "hud.js" in html
        assert "hud.css" in html


@pytest.mark.anyio
async def test_hud_static_assets(hud_test_app):
    app, _ = hud_test_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        css_resp = await client.get("/hud/hud.css")
        assert css_resp.status_code == 200
        assert "--green-bright" in css_resp.text

        js_resp = await client.get("/hud/hud.js")
        assert js_resp.status_code == 200
        assert "evaluateViaRest" in js_resp.text


@pytest.mark.anyio
async def test_api_options_endpoint(hud_test_app):
    app, symbol = hud_test_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/options")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == symbol
        assert data[0]["strike_price"] == 64800.0


def test_hud_websocket_stream(hud_test_app):
    app, symbol = hud_test_app
    client = TestClient(app)
    with client.websocket_connect("/ws/hud") as ws:
        # First push from engine
        data = ws.receive_json()
        assert data["verdict"] in ("TRADE", "NO_TRADE", "CAUTION")
        assert data["validation_status"] == "UNVALIDATED"
        assert "provenance" in data

        # Send client update message (e.g. change lots)
        ws.send_json({"symbol": symbol, "lots": 5.0, "max_risk_usd": 100.0})
        updated = ws.receive_json()
        assert updated["verdict"] in ("TRADE", "NO_TRADE", "CAUTION")


@pytest.mark.anyio
async def test_journal_api_endpoints(hud_test_app):
    app, symbol = hud_test_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Trigger an evaluation
        eval_resp = await client.post("/api/evaluate", json={"symbol": symbol, "lots": 5.0})
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        eval_id = eval_data["provenance"]["evaluation_id"]
        assert eval_id is not None

        # Check journal stats
        stats_resp = await client.get("/api/journal/stats")
        assert stats_resp.status_code == 200
        assert stats_resp.json()["evaluations_count"] >= 1

        # Record paper trade
        trade_resp = await client.post(
            "/api/journal/paper-trade",
            json={
                "evaluation_id": eval_id,
                "actual_fill_price": 702.0,
            },
        )
        assert trade_resp.status_code == 200
        assert trade_resp.json()["success"] is True
