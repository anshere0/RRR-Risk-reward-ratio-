"""JSON schema validation and latency benchmark for RREvaluateResponse."""

import json
import time
from pathlib import Path

import jsonschema
import pytest

from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec

SCHEMA_PATH = Path(__file__).parent.parent.parent / "schemas" / "rr_response.schema.json"


@pytest.fixture(scope="module")
def schema():
    with open(SCHEMA_PATH) as f:
        return json.load(f)


@pytest.fixture
def mock_ready_engine():
    """Sets up a healthy engine configured to produce a valid TRADE response."""
    state = MarketState()
    state.is_ws_connected = True
    now = time.time()
    state.spot_index_price = 64850.7
    state.perp_mark_price = 64855.0
    state.clock.record("index", now * 1_000_000, now)

    # 100 closed candles forming swing structure
    candles = []
    base_p = 64500.0
    for i in range(100):
        o = base_p + i * 5
        h = o + 20
        l = o - 20
        c = o + 10
        candles.append(Candle(t=int(now - (100 - i) * 300), o=o, h=h, l=l, c=c))
    state.candles_5m.set_closed_candles(candles)

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

    return RREngine(feed_manager=None, state=state), symbol


@pytest.mark.anyio
async def test_schema_conformance_trade_and_notrade(schema, mock_ready_engine):
    engine, symbol = mock_ready_engine

    # 1. Evaluate valid setup -> should validate against schema
    resp_trade = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=5.0))
    data_dict = resp_trade.model_dump()
    jsonschema.validate(instance=data_dict, schema=schema)
    assert resp_trade.provenance.config_hash is not None
    assert resp_trade.provenance.engine_version is not None

    # 2. Evaluate NO_TRADE state (simulate stale data) -> should still validate against schema
    engine.state.clock.record("index", 100.0, 100.0)
    resp_notrade = await engine.evaluate(EvaluateRequest(symbol=symbol, lots=5.0))
    assert resp_notrade.verdict == "NO_TRADE"
    data_dict_nt = resp_notrade.model_dump()
    jsonschema.validate(instance=data_dict_nt, schema=schema)


@pytest.mark.anyio
async def test_p95_latency_benchmark_under_200ms(mock_ready_engine):
    engine, symbol = mock_ready_engine
    latencies_ms = []

    # Run 50 evaluations in memory
    for _ in range(50):
        t0 = time.perf_counter()
        await engine.evaluate(EvaluateRequest(symbol=symbol, lots=10.0))
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    latencies_ms.sort()
    idx_p95 = int(len(latencies_ms) * 0.95)
    p95_ms = latencies_ms[idx_p95]

    print(f"\nEvaluation Latency: median={latencies_ms[25]:.2f}ms, p95={p95_ms:.2f}ms")
    # Performance budget: p95 < 200 ms
    assert p95_ms < 200.0, f"p95 latency {p95_ms:.2f}ms exceeded 200ms budget"
