"""Tests for F7 (Manual entry validation) and F8 (Lots vs Budget)."""

import math
import time

import pytest

from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


@pytest.fixture
def clean_engine():
    state = MarketState()
    now = time.time()
    state.is_ws_connected = True
    spot = 64000.0
    state.spot_index_price = spot
    state.clock.record("index", now * 1_000_000, now)
    state.clock.record("option_ticker", now * 1_000_000, now)

    symbol = "C-BTC-64000-281126"
    spec = ProductSpec(
        id=64000,
        symbol=symbol,
        contract_type="call_options",
        strike_price=64000.0,
        contract_value=0.001,
        tick_size=0.1,
        settlement_time_iso="2026-11-28T12:00:00Z",
        settlement_time_epoch=now + 86400 * 5,
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
        bid_size=50.0,
        ask_size=50.0,
        delta=0.52,
        timestamp=now,
    )
    state.update_option_ticker(tk)

    # 100 candles with healthy volatility
    candles = [
        Candle(
            t=int(now - (100 - i) * 300),
            o=64000 + 80 * math.sin(i / 1.5),
            h=64120 + 80 * math.sin(i / 1.5),
            l=63880 + 80 * math.sin(i / 1.5),
            c=64000 + 80 * math.sin(i / 1.5),
        )
        for i in range(100)
    ]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(state=state, enable_journal=False)
    return engine, symbol


@pytest.mark.anyio
async def test_f7_entry_override_validation(clean_engine):
    engine, symbol = clean_engine

    # Ticker bid=760.0, ask=765.0, tick_size=0.1
    # Allowable range: [760.0 - 0.2, 765.0 + 0.2] = [759.8, 765.2]

    # 1. Valid override within corridor: 762.0
    req_valid = EvaluateRequest(symbol=symbol, lots=1.0, entry_override_option=762.0)
    resp_valid = await engine.evaluate(req_valid)
    assert not any(r.code == "ENTRY_OVERRIDE_INVALID" for r in resp_valid.reasons)

    # 2. Invalid override far outside (e.g. 62765.0 or 750.0)
    req_invalid = EvaluateRequest(symbol=symbol, lots=1.0, entry_override_option=62765.0)
    resp_invalid = await engine.evaluate(req_invalid)
    assert resp_invalid.verdict == "NO_TRADE"
    assert resp_invalid.setup is None
    codes = [r.code for r in resp_invalid.reasons]
    assert "ENTRY_OVERRIDE_INVALID" in codes


@pytest.mark.anyio
async def test_f8_lots_versus_budget(clean_engine):
    engine, symbol = clean_engine

    # When budget is $0.10, but 1 lot risk is ~$0.20:
    # max_lots must be floor(0.10 / 0.20) = 0!
    # And RISK_OVER_BUDGET must fire with message containing "one lot risks $X, above your budget $Y"
    req = EvaluateRequest(symbol=symbol, lots=1.0, max_risk_usd=0.10)
    resp = await engine.evaluate(req)

    assert resp.verdict == "NO_TRADE"
    codes = [r.code for r in resp.reasons]
    assert "RISK_OVER_BUDGET" in codes
    reason = next(r for r in resp.reasons if r.code == "RISK_OVER_BUDGET")
    assert reason.severity == "hard"
    assert "above your budget $0.10" in reason.message
    assert "one lot risks" in reason.message

    if resp.setup:
        assert resp.setup.money.max_lots_for_risk_budget == 0
