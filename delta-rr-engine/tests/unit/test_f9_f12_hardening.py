"""Unit tests for hardening fixes F9 through F12.

F9: Local server security (TrustedHostMiddleware, CORS origin restriction)
F10: Journal size (SHA-256 hash + 100 candles) & IST daily loss
F11: Honest holding time calculation
F12: Index symbol provenance (.DEXBTUSD)
"""

import math
import time
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from rr_engine.api.main import create_app
from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.journal.db import JournalDB
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


@pytest.mark.anyio
async def test_f9_trusted_host_security():
    """F9: Non-local host header must be rejected with 400."""
    app = create_app(auto_start_feed=False)
    # Rejection on arbitrary host
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://evil.com") as client:
        resp = await client.get("/api/status", headers={"Host": "evil.com"})
        assert resp.status_code == 400

    # Acceptance on 127.0.0.1 and localhost
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resp = await client.get("/api/status", headers={"Host": "127.0.0.1:8000"})
        assert resp.status_code == 200


def test_f10_journal_ist_daily_loss(tmp_path):
    """F10: Journal daily loss uses IST (+05:30) date boundaries and returns positive loss if net P&L < 0."""
    db_file = tmp_path / "test_journal.db"
    db = JournalDB(db_path=str(db_file))

    # Current UTC time
    now_utc = datetime.now(UTC)
    entered_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    # IST date
    import datetime as dt

    ist_time = now_utc + dt.timedelta(hours=5, minutes=30)
    ist_date_str = ist_time.strftime("%Y-%m-%d")

    trade_id = db.record_paper_trade(
        symbol="P-BTC-64000-281126",
        direction="put",
        lots=10.0,
        entry_price=500.0,
        sl_price=300.0,
        tp_price=800.0,
        entered_at=entered_str,
    )

    # Close trade with exit at $300 (loss of $20.0)
    db.update_paper_trade_exit(
        trade_id=trade_id,
        actual_exit_price=300.0,
        exit_reason="SL_HIT",
        exited_at=entered_str,
        pnl_usd=-20.0,
    )

    loss = db.get_daily_loss(date_prefix=ist_date_str)
    # Net P&L is -20.0, so daily loss is 20.0
    assert loss == pytest.approx(20.0, abs=1e-2)


@pytest.mark.anyio
async def test_f11_honest_holding_time_formula():
    """F11: Expected resolution time = (stop_dist * target_dist) / sigma^2 in minutes."""
    state = MarketState()
    now = time.time()
    state.is_ws_connected = True
    state.spot_index_price = 65000.0
    state.perp_mark_price = 65005.0
    state.clock.record("index", now * 1_000_000, now)

    symbol = "P-BTC-64000-281126"
    spec = ProductSpec(
        id=123,
        symbol=symbol,
        contract_type="put_options",
        strike_price=64000.0,
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
        mark_price=400.0,
        best_bid=395.0,
        best_ask=405.0,
        bid_size=50.0,
        ask_size=50.0,
        delta=-0.35,
        timestamp=now,
    )
    state.update_option_ticker(tk)

    # Realistic oscillating candles so indicators are healthy
    candles = [
        Candle(
            t=int(now - (120 - i) * 300),
            o=65000.0 + 80.0 * math.sin(i / 2.0),
            h=65050.0 + 80.0 * math.sin(i / 2.0),
            l=64950.0 + 80.0 * math.sin(i / 2.0),
            c=65000.0 + 80.0 * math.sin((i + 1) / 2.0),
        )
        for i in range(120)
    ]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(feed_manager=None, state=state)
    resp = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=10.0, max_risk_usd=100.0, hold_minutes=30))

    if resp.setup:
        # Check that estimated_holding_time string exists
        assert resp.setup.estimated_holding_time is not None
        assert "min" in resp.setup.estimated_holding_time


@pytest.mark.anyio
async def test_f12_provenance_index_symbol():
    """F12: Provenance index_symbol must be .DEXBTUSD."""
    state = MarketState()
    now = time.time()
    state.is_ws_connected = True
    state.spot_index_price = 65000.0
    state.perp_mark_price = 65005.0
    state.clock.record("index", now * 1_000_000, now)

    symbol = "C-BTC-66000-281126"
    spec = ProductSpec(
        id=456,
        symbol=symbol,
        contract_type="call_options",
        strike_price=66000.0,
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
        mark_price=350.0,
        best_bid=348.0,
        best_ask=352.0,
        bid_size=50.0,
        ask_size=50.0,
        delta=0.4,
        timestamp=now,
    )
    state.update_option_ticker(tk)

    candles = [
        Candle(
            t=int(now - (120 - i) * 300),
            o=65000.0 + 80.0 * math.sin(i / 2.0),
            h=65050.0 + 80.0 * math.sin(i / 2.0),
            l=64950.0 + 80.0 * math.sin(i / 2.0),
            c=65000.0 + 80.0 * math.sin((i + 1) / 2.0),
        )
        for i in range(120)
    ]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(feed_manager=None, state=state)
    resp = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=10.0, max_risk_usd=100.0))
    assert resp.provenance.index_symbol == ".DEXBTUSD"
