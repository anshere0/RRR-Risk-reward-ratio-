"""Black-Scholes analytical pricing for European options.

Conventions:
  * S, K       : USD per 1 BTC (index price and strike)
  * T          : years, computed as seconds / (365 * 24 * 3600)
  * sigma      : annualised volatility as a decimal (0.45 = 45%)
  * premium    : USD per 1 BTC of underlying. 1 Delta lot = 0.001 BTC.
  * r          : risk-free interest rate (default 0.0)
"""

from __future__ import annotations

import math

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


def years_from_seconds(seconds: float) -> float:
    """Convert duration in seconds to years (365 days / year). Negative seconds clamp to 0."""
    return max(seconds, 0.0) / SECONDS_PER_YEAR


def _ncdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def intrinsic(S: float, K: float, is_call: bool) -> float:
    """Option intrinsic value at spot price S and strike K."""
    return max(S - K, 0.0) if is_call else max(K - S, 0.0)


def bs_price(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = 0.0) -> float:
    """Black-Scholes analytical price for European Call or Put.

    Matches reference/rr_reference/pricing.py exactly.
    """
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
