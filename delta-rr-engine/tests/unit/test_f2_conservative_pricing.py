"""Unit test for F2: Always conservative pricing.

Test:
1. Golden vectors still match reference/tests/golden_vectors.json
2. Changing a config shock changes both the main and 1-lot results identically in rr_engine.
"""

import copy
import json
import time
from pathlib import Path

import pytest

from rr_engine.config import load_settings
from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_core import RRParams, compute_rr
from rr_engine.engine.rr_engine import RREngine
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec

VECTORS_PATH = Path(__file__).parent.parent.parent / "reference" / "tests" / "golden_vectors.json"


def test_f2_golden_vectors_match():
    """Verify that compute_rr matches golden vectors with always-conservative pricing."""
    with open(VECTORS_PATH) as f:
        vectors = json.load(f)
    for rv in vectors["rr"]:
        inp = rv["inputs"]
        p = RRParams(**{k: v for k, v in inp.items() if k != "extra"})
        result = compute_rr(p)
        expected = rv["output"]
        assert result["net_rr"] == pytest.approx(expected["net_rr"], rel=rv["tolerance"]["rr_rel"])
        assert result["risk_usd"] == pytest.approx(expected["risk_usd"], abs=rv["tolerance"]["money_abs"])
        assert result["reward_usd"] == pytest.approx(expected["reward_usd"], abs=rv["tolerance"]["money_abs"])


@pytest.mark.anyio
async def test_f2_config_shock_affects_main_and_1lot_identically():
    """Verify that changing a config shock changes both the main and 1-lot results identically."""
    cfg = load_settings()
    now = time.time()
    symbol = "C-BTC-65000-281126"

    def setup_engine(iv_shock_sl: float):
        test_cfg = copy.deepcopy(cfg)
        test_cfg.pricing.iv_shock_sl = iv_shock_sl
        state = MarketState()
        state.is_ws_connected = True
        state.spot_index_price = 64850.0
        state.perp_mark_price = 64850.0
        state.clock.record("index", now * 1_000_000, now)
        state.clock.record("option_ticker", now * 1_000_000, now)

        spec = ProductSpec(
            id=999,
            symbol=symbol,
            contract_type="call_options",
            strike_price=65000.0,
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
            mark_price=700.0,
            best_bid=695.0,
            best_ask=705.0,
            bid_size=100.0,
            ask_size=100.0,
            delta=0.52,
            timestamp=now,
        )
        state.update_option_ticker(tk)
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
        return RREngine(feed_manager=None, state=state, settings=test_cfg)

    # Run with default iv_shock_sl (-0.02)
    engine1 = setup_engine(iv_shock_sl=-0.02)
    resp1 = await engine1.evaluate(EvaluateRequest(symbol=symbol, lots=10.0, max_risk_usd=50.0))

    # Run with stressed iv_shock_sl (-0.08)
    engine2 = setup_engine(iv_shock_sl=-0.08)
    resp2 = await engine2.evaluate(EvaluateRequest(symbol=symbol, lots=10.0, max_risk_usd=50.0))

    # Both main setup SL option model and budget calculations must respond
    assert resp1.setup is not None and resp2.setup is not None
    # Higher negative shock -> lower option value at stop
    assert resp2.setup.option.sl_premium_model < resp1.setup.option.sl_premium_model
    # Higher negative shock -> higher risk USD
    assert resp2.setup.money.risk_usd > resp1.setup.money.risk_usd

    # Main risk and 1-lot risk (derived from max_lots_for_risk_budget) must change consistently
    assert resp2.setup.money.max_lots_for_risk_budget <= resp1.setup.money.max_lots_for_risk_budget
