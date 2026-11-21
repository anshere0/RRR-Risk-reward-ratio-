"""Spread calculations and spread stress logic."""

from __future__ import annotations


def calculate_spread(bid: float, ask: float) -> tuple[float, float, float]:
    """Calculate absolute spread, half spread, and spread percentage.

    Returns (spread, half_spread, spread_pct).
    """
    if bid <= 0 or ask < bid:
        return 0.0, 0.0, float("inf")
    spread = ask - bid
    half_spread = 0.5 * spread
    mid = 0.5 * (bid + ask)
    spread_pct = (spread / mid * 100.0) if mid > 0 else float("inf")
    return spread, half_spread, spread_pct


def spread_stress(half_spread: float, stress_factor: float = 1.5) -> float:
    """Apply spread widening factor (default 1.5x) at stop loss scenario."""
    return half_spread * stress_factor
