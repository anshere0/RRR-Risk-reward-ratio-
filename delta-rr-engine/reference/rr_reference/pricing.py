"""Black-Scholes pricing, Greeks and implied-vol solver (European options, r configurable).

Conventions (must match the production engine):
  * S, K       : USD per 1 BTC (index price and strike)
  * T          : years, computed as seconds / (365 * 24 * 3600)
  * sigma      : annualised volatility as a decimal (0.45 = 45%)
  * premium    : USD per 1 BTC of underlying (NOT per contract). One Delta lot = contract_value BTC (0.001).
  * theta      : per calendar day, in premium units
  * vega       : per 1.00 change in sigma (divide by 100 for "per vol point")
"""
from __future__ import annotations

import math

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


def years_from_seconds(seconds: float) -> float:
    return max(seconds, 0.0) / SECONDS_PER_YEAR


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def intrinsic(S: float, K: float, is_call: bool) -> float:
    return max(S - K, 0.0) if is_call else max(K - S, 0.0)


def bs_price(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = 0.0) -> float:
    if S <= 0 or K <= 0:
        raise ValueError("S and K must be positive")
    if T <= 0 or sigma <= 0:
        disc = math.exp(-r * max(T, 0.0))
        return max(S - K * disc, 0.0) if is_call else max(K * disc - S, 0.0)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    disc = math.exp(-r * T)
    if is_call:
        return S * _ncdf(d1) - K * disc * _ncdf(d2)
    return K * disc * _ncdf(-d2) - S * _ncdf(-d1)


def bs_greeks(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = 0.0) -> dict:
    if T <= 0 or sigma <= 0:
        itm = (S > K) if is_call else (S < K)
        d = 1.0 if itm else 0.0
        return {"delta": d if is_call else -d, "gamma": 0.0, "vega": 0.0, "theta_per_day": 0.0}
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    disc = math.exp(-r * T)
    pdf = _npdf(d1)
    delta = _ncdf(d1) if is_call else _ncdf(d1) - 1.0
    gamma = pdf / (S * sigma * sqrtT)
    vega = S * pdf * sqrtT
    if is_call:
        theta_year = -S * pdf * sigma / (2.0 * sqrtT) - r * K * disc * _ncdf(d2)
    else:
        theta_year = -S * pdf * sigma / (2.0 * sqrtT) + r * K * disc * _ncdf(-d2)
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta_per_day": theta_year / 365.0}


class ImpliedVolError(ValueError):
    """No volatility reproduces the observed premium -> the model cannot be anchored -> NO_TRADE."""


def implied_vol(price: float, S: float, K: float, T: float, is_call: bool, r: float = 0.0,
                lo: float = 1e-4, hi: float = 10.0, tol: float = 1e-10, max_iter: int = 300) -> float:
    """Bisection (guaranteed convergence). Slower than Newton but cannot diverge."""
    if T <= 0:
        raise ImpliedVolError("T <= 0: option at/after expiry")
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
