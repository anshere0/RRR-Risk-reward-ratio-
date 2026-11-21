"""Unit tests for Phase 7 Monte Carlo probability simulation.

Requirements (docs/01 §4, docs/02 §6):
  - Filtered historical simulation with N=20,000 paths.
  - Matches analytic Gambler's Ruin within Monte Carlo standard error on driftless Gaussian test.
  - Asynchronous execution never blocks primary verdict.
"""

import math
import time

import numpy as np
import pytest

from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.montecarlo.simulate import (
    extract_standardized_residuals,
    simulate_paths_vectorized,
)
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


def test_gamblers_ruin_symmetric():
    """Gambler's Ruin test: Symmetric barriers with zero drift.

    S0 = 100, SL = 90 (d_sl = 10), TP = 110 (d_tp = 10).
    Theoretical hit probability p_tp = d_sl / (d_sl + d_tp) = 10 / 20 = 0.5000.
    """
    n_paths = 20_000
    # Use high volatility and long horizon to ensure absorption (p_timeout ~ 0)
    p_tp, p_sl, p_timeout, mc_se = simulate_paths_vectorized(
        spot=100.0,
        sl_spot=90.0,
        tp_spot=110.0,
        direction="call",
        vol_1m=0.01,
        hold_minutes=2000,
        n_paths=n_paths,
        residuals=None,  # Standard Gaussian
        seed=42,
        driftless=True,
    )

    # Probabilities must partition total paths
    assert math.isclose(p_tp + p_sl + p_timeout, 1.0, abs_tol=1e-5)

    # In long horizon, timeout is minimal
    assert p_timeout < 0.01

    # Theoretical probability = 0.50
    p_expected = 10.0 / (10.0 + 10.0)
    diff = abs(p_tp - p_expected)
    assert diff <= 3 * mc_se, (
        f"p_tp={p_tp:.4f} differs from expected {p_expected:.4f} by {diff:.4f} (limit {3 * mc_se:.4f})"
    )


def test_gamblers_ruin_asymmetric_call():
    """Gambler's Ruin test: Asymmetric barriers for CALL (bullish).

    S0 = 100, SL = 90 (d_sl = 10), TP = 120 (d_tp = 20).
    Theoretical hit probability p_tp = 10 / (10 + 20) = 1/3 ~ 0.3333.
    """
    n_paths = 20_000
    p_tp, p_sl, p_timeout, mc_se = simulate_paths_vectorized(
        spot=100.0,
        sl_spot=90.0,
        tp_spot=120.0,
        direction="call",
        vol_1m=0.01,
        hold_minutes=2500,
        n_paths=n_paths,
        residuals=None,
        seed=123,
        driftless=True,
    )

    p_expected = 10.0 / (10.0 + 20.0)
    diff = abs(p_tp - p_expected)
    assert diff <= 3 * mc_se, (
        f"p_tp={p_tp:.4f} differs from expected {p_expected:.4f} by {diff:.4f} (limit {3 * mc_se:.4f})"
    )


def test_gamblers_ruin_asymmetric_put():
    """Gambler's Ruin test: Asymmetric barriers for PUT (bearish).

    S0 = 100, SL = 110 (d_sl = 10), TP = 80 (d_tp = 20).
    Theoretical hit probability p_tp = 10 / (10 + 20) = 1/3 ~ 0.3333.
    """
    n_paths = 20_000
    p_tp, p_sl, p_timeout, mc_se = simulate_paths_vectorized(
        spot=100.0,
        sl_spot=110.0,
        tp_spot=80.0,
        direction="put",
        vol_1m=0.01,
        hold_minutes=2500,
        n_paths=n_paths,
        residuals=None,
        seed=456,
        driftless=True,
    )

    p_expected = 10.0 / (10.0 + 20.0)
    diff = abs(p_tp - p_expected)
    assert diff <= 3 * mc_se, (
        f"p_tp={p_tp:.4f} differs from expected {p_expected:.4f} by {diff:.4f} (limit {3 * mc_se:.4f})"
    )


def test_residual_standardization():
    """Verify that empirical return residuals are properly normalized to mean 0, variance 1."""
    # Synthetic trending random walk
    np.random.seed(99)
    noise = np.random.normal(0.001, 0.005, size=200)
    prices = 60000.0 * np.exp(np.cumsum(noise))
    candles = [{"c": p} for p in prices]

    res = extract_standardized_residuals(candles)
    assert len(res) == 199
    assert math.isclose(float(np.mean(res)), 0.0, abs_tol=1e-7)
    assert math.isclose(float(np.std(res)), 1.0, abs_tol=1e-7)


@pytest.mark.anyio
async def test_engine_async_mc_integration():
    """Verify that RREngine computes Monte Carlo when include_mc=True without blocking."""
    state = MarketState()
    state.is_ws_connected = True
    now = time.time()
    state.spot_index_price = 64850.7
    state.perp_mark_price = 64855.0
    state.clock.record("index", now * 1_000_000, now)
    state.clock.record("option_ticker", now * 1_000_000, now)

    symbol = "C-BTC-64800-281126"
    spec = ProductSpec(
        id=123,
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

    engine = RREngine(state=state, enable_journal=False)

    # 1. Fast evaluation: include_mc=False (default)
    t0 = time.perf_counter()
    resp_fast = await engine.evaluate(EvaluateRequest(symbol=symbol, include_mc=False))
    latency_ms = (time.perf_counter() - t0) * 1000.0
    assert latency_ms < 50.0  # Ultra fast < 50ms
    if resp_fast.setup:
        assert resp_fast.setup.probability is None

    # 2. Evaluation with Monte Carlo: include_mc=True
    resp_mc = await engine.evaluate(EvaluateRequest(symbol=symbol, include_mc=True, hold_minutes=30))
    if resp_mc.setup:
        assert resp_mc.setup.probability is not None
        prob = resp_mc.setup.probability
        assert 0.0 <= prob.p_tp <= 1.0
        assert 0.0 <= prob.p_sl <= 1.0
        assert 0.0 <= prob.p_timeout <= 1.0
        assert prob.mc_std_error >= 0.0
        assert math.isclose(prob.p_tp + prob.p_sl + prob.p_timeout, 1.0, abs_tol=1e-3)
