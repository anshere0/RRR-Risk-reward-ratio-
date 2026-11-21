"""Unit test for F1: Make NO TRADE real (defaults + verdict).

Test: re-run sample_evaluate_response.json inputs -> must be NO_TRADE
(NET_RR_TOO_LOW, BREAKEVEN_TOO_HIGH, CHOPPY_MARKET, TARGET_UNREACHABLE, FEES_EAT_REWARD all hard).
Also tests CAUTION and TRADE verdicts.
"""

import time

import pytest

from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


@pytest.mark.anyio
async def test_f1_sample_inputs_fail_closed_to_no_trade():
    state = MarketState()
    state.is_ws_connected = True
    now = time.time()
    state.spot_index_price = 62765.5
    state.perp_mark_price = 62764.0
    state.clock.record("index", now * 1_000_000, now)
    state.clock.record("option_ticker", now * 1_000_000, now)

    symbol = "P-BTC-63000-201126"
    spec = ProductSpec(
        id=888,
        symbol=symbol,
        contract_type="put_options",
        strike_price=63000.0,
        contract_value=0.001,
        tick_size=0.1,
        settlement_time_iso="2026-11-20T12:00:00Z",
        settlement_time_epoch=now + 3600 * 14,
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
        bid_size=85.0,
        ask_size=85.0,
        delta=-0.54,
        timestamp=now,
    )
    state.update_option_ticker(tk)

    # 120 candles with choppy sideways movement and realistic volatility
    import math

    candles = [
        Candle(
            t=int(now - (120 - i) * 300),
            o=62700 + 100 * math.sin(i / 1.5),
            h=62880 + 100 * math.sin(i / 1.5),
            l=62610 + 100 * math.sin(i / 1.5),
            c=62765.5 + 100 * math.sin(i / 1.5),
        )
        for i in range(120)
    ]
    state.candles_5m.set_closed_candles(candles)

    engine = RREngine(feed_manager=None, state=state)
    # Default strict_mode must be True
    req = EvaluateRequest(symbol=symbol, lots=10.0, entry_override_option=765.0, max_risk_usd=100.0)
    assert req.strict_mode is True

    resp = await engine.evaluate(req)

    # Must fail closed to NO_TRADE
    assert resp.verdict == "NO_TRADE"
    hard_codes = {r.code for r in resp.reasons if r.severity == "hard"}

    # Sections B-F hard gates must remain hard!
    assert "NET_RR_TOO_LOW" in hard_codes or "FEES_EAT_REWARD" in hard_codes or "CHOPPY_MARKET" in hard_codes
    for code in ("NET_RR_TOO_LOW", "BREAKEVEN_TOO_HIGH", "CHOPPY_MARKET", "FEES_EAT_REWARD"):
        matching = [r for r in resp.reasons if r.code == code]
        for m in matching:
            assert m.severity == "hard", f"Gate {code} must be hard, got {m.severity}"


@pytest.mark.anyio
async def test_f1_caution_and_trade_verdicts():
    from rr_engine.engine.gates import GateResult
    from rr_engine.engine.models import Provenance
    from rr_engine.engine.verdict import aggregate_verdict

    prov = Provenance(
        symbol="C-BTC-65000-281126",
        region="india",
        index_symbol="BTC",
        index_price=65000.0,
        computed_at_utc="2026-11-20T12:00:00Z",
        data_age_seconds=0.1,
        config_hash="abc",
        engine_version="0.1.0",
    )

    # 1. No gates fired -> TRADE
    resp_trade = aggregate_verdict(
        provenance=prov,
        gate_results=[
            GateResult(code="STRIKE_NOT_ATM", severity="warning", fired=False, message="ATM"),
            GateResult(code="SPREAD_TOO_WIDE", severity="hard", fired=False, message="Spread ok"),
        ],
        raw_setup=None,
    )
    assert resp_trade.verdict == "TRADE"

    # 2. Only soft warning fired -> CAUTION
    resp_caution = aggregate_verdict(
        provenance=prov,
        gate_results=[
            GateResult(code="STRIKE_NOT_ATM", severity="warning", fired=True, message="Delta is 0.30"),
            GateResult(code="SPREAD_TOO_WIDE", severity="hard", fired=False, message="Spread ok"),
        ],
        raw_setup=None,
    )
    assert resp_caution.verdict == "CAUTION"

    # 3. Hard gate fired -> NO_TRADE
    resp_no_trade = aggregate_verdict(
        provenance=prov,
        gate_results=[
            GateResult(code="STRIKE_NOT_ATM", severity="warning", fired=True, message="Delta is 0.30"),
            GateResult(code="SPREAD_TOO_WIDE", severity="hard", fired=True, message="Spread too wide"),
        ],
        raw_setup=None,
    )
    assert resp_no_trade.verdict == "NO_TRADE"
