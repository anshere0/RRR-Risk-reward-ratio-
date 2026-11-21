"""Monte Carlo probability simulation via Filtered Historical Simulation (FHS).

Requirements (docs/01 §4, docs/02 §6):
  - Filtered historical simulation with N=20,000 paths.
  - Standardised index return residuals from history (bootstrap).
  - Scaled by current EWMA volatility, simulated minute-by-minute for holding window.
  - Counts which barrier (stop/target) is touched first, or timeout.
  - Reports p_tp, p_sl, p_timeout with Monte Carlo standard error.
  - Runs asynchronously and NEVER blocks the primary verdict.
  - Validated against analytic Gambler's Ruin formula on driftless Gaussian test.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import numpy as np

from rr_engine.engine.models import ProbabilityEstimate


def extract_standardized_residuals(candles: list[dict[str, Any]]) -> np.ndarray:
    """Extract and standardize 5m or 1m log return residuals from closed candles."""
    if not candles or len(candles) < 10:
        return np.array([], dtype=float)

    closes = np.array([float(c["c"]) for c in candles if "c" in c], dtype=float)
    if len(closes) < 10:
        return np.array([], dtype=float)

    log_returns = np.diff(np.log(closes))
    std = float(np.std(log_returns))
    if std <= 1e-12:
        return np.array([], dtype=float)

    mean = float(np.mean(log_returns))
    standardized = (log_returns - mean) / std
    return standardized


def simulate_paths_vectorized(
    spot: float,
    sl_spot: float,
    tp_spot: float,
    direction: str,
    vol_1m: float,
    hold_minutes: int = 30,
    n_paths: int = 20_000,
    residuals: np.ndarray | None = None,
    seed: int | None = None,
    driftless: bool = True,
) -> tuple[float, float, float, float]:
    """Pure vectorized Monte Carlo path simulation using NumPy.

    Returns:
        (p_tp, p_sl, p_timeout, mc_std_error)
    """
    if spot <= 0 or sl_spot <= 0 or tp_spot <= 0:
        return 0.0, 0.0, 1.0, 0.0

    rng = np.random.default_rng(seed)

    # 1. Generate standardized shock matrix: shape (n_paths, hold_minutes)
    if residuals is not None and len(residuals) >= 20:
        indices = rng.integers(0, len(residuals), size=(n_paths, hold_minutes))
        z = residuals[indices]
    else:
        # Fallback to standard Gaussian residuals
        z = rng.standard_normal(size=(n_paths, hold_minutes))

    # 2. Cumulative path construction
    if driftless:
        # In driftless model, price is an exact martingale satisfying Doob's OST
        # S_t = S_0 + sum(S_0 * vol_1m * z)
        delta_s = spot * vol_1m * z
        paths = spot + np.cumsum(delta_s, axis=1)
    else:
        # Geometric Brownian Motion with Ito drift correction
        drift = -0.5 * (vol_1m**2)
        returns = drift + (vol_1m * z)
        paths = spot * np.exp(np.cumsum(returns, axis=1))

    # 4. Barrier crossing masks
    is_call = "call" in direction.lower()
    if is_call:
        # Bullish: target > spot > stop
        tp_mask = paths >= tp_spot
        sl_mask = paths <= sl_spot
    else:
        # Bearish: stop > spot > target
        tp_mask = paths <= tp_spot
        sl_mask = paths >= sl_spot

    # 5. Determine earliest barrier touched per path
    tp_hit = np.any(tp_mask, axis=1)
    sl_hit = np.any(sl_mask, axis=1)

    # When a barrier was hit, argmax gives the first index where True appears
    tp_step = np.where(tp_hit, np.argmax(tp_mask, axis=1), hold_minutes + 1)
    sl_step = np.where(sl_hit, np.argmax(sl_mask, axis=1), hold_minutes + 1)

    hit_tp_first = tp_hit & (tp_step < sl_step)
    hit_sl_first = sl_hit & (sl_step < tp_step)
    timeout = ~hit_tp_first & ~hit_sl_first

    # 6. Empirical probabilities
    p_tp = float(np.sum(hit_tp_first) / n_paths)
    p_sl = float(np.sum(hit_sl_first) / n_paths)
    p_timeout = float(np.sum(timeout) / n_paths)

    # Standard error for proportion: sqrt(p * (1 - p) / N)
    mc_std_error = float(math.sqrt(max(0.0, p_tp * (1.0 - p_tp)) / n_paths))

    return p_tp, p_sl, p_timeout, mc_std_error


async def run_monte_carlo_async(
    spot: float,
    sl_spot: float,
    tp_spot: float,
    direction: str,
    annual_vol: float,
    hold_minutes: int = 30,
    n_paths: int = 20_000,
    residuals: np.ndarray | None = None,
    seed: int | None = None,
) -> ProbabilityEstimate:
    """Non-blocking async runner executing Monte Carlo in a worker thread."""
    # Convert annualized EWMA volatility to 1-minute volatility
    # Minutes per standard trading year = 365 * 24 * 60 = 525,600
    vol_1m = annual_vol / math.sqrt(525_600.0)

    p_tp, p_sl, p_timeout, mc_se = await asyncio.to_thread(
        simulate_paths_vectorized,
        spot=spot,
        sl_spot=sl_spot,
        tp_spot=tp_spot,
        direction=direction,
        vol_1m=vol_1m,
        hold_minutes=hold_minutes,
        n_paths=n_paths,
        residuals=residuals,
        seed=seed,
    )

    return ProbabilityEstimate(
        p_tp=round(p_tp, 4),
        p_sl=round(p_sl, 4),
        p_timeout=round(p_timeout, 4),
        mc_std_error=round(mc_se, 4),
    )
