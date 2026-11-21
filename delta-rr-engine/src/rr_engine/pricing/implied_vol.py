"""Implied Volatility solver via bisection (guaranteed convergence).

Matches reference/rr_reference/pricing.py.
"""

from __future__ import annotations

import math

from rr_engine.pricing.black_scholes import bs_price


class ImpliedVolError(ValueError):
    """No volatility reproduces the observed premium -> model cannot anchor -> NO_TRADE."""


def implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    is_call: bool,
    r: float = 0.0,
    lo: float = 1e-4,
    hi: float = 10.0,
    tol: float = 1e-10,
    max_iter: int = 300,
) -> float:
    """Solve sigma* such that bs_price(S, K, T, sigma*, is_call, r) == price.

    Bisection algorithm: guaranteed convergence, impossible to diverge or oscillate.
    """
    if T <= 0:
        raise ImpliedVolError("T <= 0: option at or after expiry")

    disc = math.exp(-r * T)
    floor = max(S - K * disc, 0.0) if is_call else max(K * disc - S, 0.0)
    if price < floor - 1e-9:
        raise ImpliedVolError(f"price {price} below intrinsic {floor}")

    p_lo = bs_price(S, K, T, lo, is_call, r)
    p_hi = bs_price(S, K, T, hi, is_call, r)
    if price < p_lo - 1e-9 or price > p_hi + 1e-9:
        raise ImpliedVolError(f"price {price} outside attainable range [{p_lo}, {p_hi}]")

    a, b = lo, hi
    sigma = 0.5 * (a + b)
    for _ in range(max_iter):
        sigma = 0.5 * (a + b)
        p = bs_price(S, K, T, sigma, is_call, r)
        if abs(p - price) < tol * max(1.0, price):
            return sigma
        if p > price:
            b = sigma
        else:
            a = sigma
        if b - a < 1e-15:
            break

    return sigma
