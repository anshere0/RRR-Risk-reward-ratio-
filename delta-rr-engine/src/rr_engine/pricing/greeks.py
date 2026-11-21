"""Black-Scholes analytical Greeks, Taylor series cross-check, and API consistency check.

Conventions:
  * delta         : dV/dS (Call in [0, 1], Put in [-1, 0])
  * gamma         : d^2V/dS^2
  * vega          : dV/d(sigma), per 1.00 change in sigma (divide by 100 for per vol-point)
  * theta_per_day : dV/dt in calendar days, in premium units
"""

from __future__ import annotations

import math

from rr_engine.pricing.black_scholes import _ncdf, _npdf


def bs_greeks(
    S: float,
    K: float,
    T: float,
    sigma: float,
    is_call: bool,
    r: float = 0.0,
) -> dict[str, float]:
    """Analytical Black-Scholes Greeks. Matches reference/rr_reference/pricing.py."""
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

    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta_per_day": theta_year / 365.0,
    }


def taylor_reprice(
    entry_price: float,
    dS: float,
    hold_seconds: float,
    greeks: dict[str, float],
) -> float:
    """Second-order Taylor series approximation (Delta + Gamma + Theta).

    Kept as a cross-check only. Stops/targets of 0.5-2 ATR are too large for Taylor alone.
    """
    days = hold_seconds / 86400.0
    delta = greeks.get("delta", 0.0)
    gamma = greeks.get("gamma", 0.0)
    theta_per_day = greeks.get("theta_per_day", 0.0)

    est_change = delta * dS + 0.5 * gamma * (dS**2) + theta_per_day * days
    return max(0.0, entry_price + est_change)


def check_greeks_consistency(
    model_delta: float,
    api_delta: float | None,
    warn_limit: float = 0.07,
    hard_limit: float = 0.15,
) -> tuple[str | None, float]:
    """Compare model delta with Delta API delta.

    Returns (failure_code, diff).
    None -> pass.
    'GREEKS_MISMATCH' -> warning (> 0.07).
    'GREEKS_MISMATCH_SEVERE' -> hard gate (> 0.15).
    """
    if api_delta is None:
        return None, 0.0
    diff = abs(model_delta - api_delta)
    if diff > hard_limit:
        return "GREEKS_MISMATCH_SEVERE", diff
    if diff > warn_limit:
        return "GREEKS_MISMATCH", diff
    return None, diff
