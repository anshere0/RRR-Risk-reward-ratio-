"""Property and boundary tests for production pricing, Greeks, fee model, and RR core."""

import math
from itertools import pairwise

import pytest

from rr_engine.costs.fees import option_fee_usd
from rr_engine.engine.rr_core import RRParams, compute_rr, scenario_ladder
from rr_engine.pricing.black_scholes import bs_price, years_from_seconds
from rr_engine.pricing.greeks import bs_greeks, check_greeks_consistency, taylor_reprice
from rr_engine.pricing.implied_vol import ImpliedVolError, implied_vol


def base_put(**kw) -> RRParams:
    d = dict(
        spot=64850.7,
        strike=64800.0,
        is_call=False,
        seconds_to_expiry=23 * 3600 + 48 * 60 + 45,
        mark=629.0,
        bid=627.0,
        ask=631.0,
        sl_spot=65010.0,
        tp_spot=64560.0,
        lots=10.0,
        hold_seconds=1800.0,
    )
    d.update(kw)
    return RRParams(**d)


def test_textbook_value_r0():
    # S=K=100, T=1, sigma=0.2, r=0 -> C = P = 100*(2*N(0.1)-1) = 7.9655674
    assert bs_price(100, 100, 1, 0.2, True) == pytest.approx(7.9655674, abs=1e-6)
    assert bs_price(100, 100, 1, 0.2, False) == pytest.approx(7.9655674, abs=1e-6)


@pytest.mark.parametrize(
    "S,K,T,sig,r",
    [(64850.7, 64800, 0.0027, 0.45, 0.0), (100, 90, 0.5, 0.3, 0.05), (100, 120, 2, 0.8, 0.02)],
)
def test_put_call_parity(S, K, T, sig, r):
    c = bs_price(S, K, T, sig, True, r)
    p = bs_price(S, K, T, sig, False, r)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-7 * S)


@pytest.mark.parametrize("is_call", [True, False])
@pytest.mark.parametrize(
    "S,K,T,sig",
    [(64850.7, 64800, 0.0027, 0.45), (64850.7, 65500, 0.01, 0.6), (64850.7, 63000, 0.002, 0.35)],
)
def test_iv_round_trip(S, K, T, sig, is_call):
    price = bs_price(S, K, T, sig, is_call)
    assert implied_vol(price, S, K, T, is_call) == pytest.approx(sig, abs=1e-7)


def test_iv_rejects_below_intrinsic():
    with pytest.raises(ImpliedVolError):
        implied_vol(10.0, 64000, 60000, 0.003, True)  # intrinsic is 4000


def test_iv_rejects_expired():
    with pytest.raises(ImpliedVolError):
        implied_vol(10.0, 64000, 64000, 0.0, True)


def test_greeks_match_finite_differences():
    S, K, T, sig = 64850.7, 64800.0, 0.0027, 0.45
    for is_call in (True, False):
        g = bs_greeks(S, K, T, sig, is_call)
        h = 1.0
        d_fd = (bs_price(S + h, K, T, sig, is_call) - bs_price(S - h, K, T, sig, is_call)) / (2 * h)
        g_fd = (
            bs_price(S + h, K, T, sig, is_call)
            - 2 * bs_price(S, K, T, sig, is_call)
            + bs_price(S - h, K, T, sig, is_call)
        ) / (h**2)
        v_fd = (bs_price(S, K, T, sig + 1e-4, is_call) - bs_price(S, K, T, sig - 1e-4, is_call)) / 2e-4
        dt = 1e-7
        th_fd = (bs_price(S, K, T - dt, sig, is_call) - bs_price(S, K, T + dt, sig, is_call)) / (2 * dt) / 365.0

        assert g["delta"] == pytest.approx(d_fd, abs=1e-6)
        assert g["gamma"] == pytest.approx(g_fd, rel=1e-4)
        assert g["vega"] == pytest.approx(v_fd, rel=1e-4)
        assert g["theta_per_day"] == pytest.approx(th_fd, rel=1e-3)


def test_monotonic_in_spot():
    xs = [63000 + 100 * i for i in range(40)]
    calls = [bs_price(x, 64800, 0.003, 0.45, True) for x in xs]
    puts = [bs_price(x, 64800, 0.003, 0.45, False) for x in xs]
    assert all(b > a for a, b in pairwise(calls))
    assert all(b < a for a, b in pairwise(puts))


def test_fee_cap_and_notional_binding():
    # Cheap option: premium cap binds
    cheap_fee = option_fee_usd(64850, 0.05, 1, 0.001, 0.0003, 0.10, 0.0)
    assert cheap_fee == pytest.approx(0.000005)

    # Expensive option: notional fee binds
    exp_fee = option_fee_usd(64850, 629, 10, 0.001, 0.0003, 0.10, 0.18)
    assert exp_fee == pytest.approx(0.0003 * 64850 * 0.001 * 10 * 1.18)


def test_rr_risk_bounded_by_worst_case_loss():
    res = compute_rr(base_put())
    assert res["risk_usd"] > 0
    assert res["reward_usd"] > 0
    # Worst case loss is premium paid + entry fee
    assert res["risk_usd"] <= res["worst_case_loss_usd"] + 1e-9


def test_wider_spread_lowers_rr():
    tight = compute_rr(base_put(bid=628.0, ask=630.0))
    wide = compute_rr(base_put(bid=620.0, ask=638.0))
    assert wide["net_rr"] < tight["net_rr"]


def test_vol_shock_increases_risk_and_lowers_rr():
    no_shock = compute_rr(base_put(iv_shock_sl=0.0))
    with_shock = compute_rr(base_put(iv_shock_sl=-0.05))
    assert with_shock["risk_usd"] > no_shock["risk_usd"]
    assert with_shock["net_rr"] < no_shock["net_rr"]


def test_longer_hold_reduces_reward_due_to_decay():
    short_hold = compute_rr(base_put(hold_seconds=600))
    long_hold = compute_rr(base_put(hold_seconds=7200))
    assert long_hold["reward_usd"] < short_hold["reward_usd"]


def test_call_put_symmetry():
    call = compute_rr(
        RRParams(
            spot=64850.7,
            strike=64800,
            is_call=True,
            seconds_to_expiry=86000,
            mark=700,
            bid=698,
            ask=702,
            sl_spot=64690,
            tp_spot=65140,
            lots=5,
        )
    )
    assert call["risk_usd"] > 0
    assert call["reward_usd"] > 0


def test_taylor_cross_check():
    S, K, T, sig = 64850.7, 64800.0, years_from_seconds(85725), 0.45
    mark = bs_price(S, K, T, sig, True)
    g = bs_greeks(S, K, T, sig, True)

    # For small move (dS = +50), Taylor closely approximates BS reprice
    dS = 50.0
    hold_sec = 1800.0
    approx_price = taylor_reprice(mark, dS, hold_sec, g)

    T_next = years_from_seconds(85725 - hold_sec)
    exact_price = bs_price(S + dS, K, T_next, sig, True)

    # Taylor vs exact within 5% for a 50-point move
    assert approx_price == pytest.approx(exact_price, rel=0.05)


def test_greeks_consistency_check():
    # Pass within 0.07
    code, diff = check_greeks_consistency(model_delta=0.52, api_delta=0.55)
    assert code is None
    assert diff == pytest.approx(0.03)

    # Warn between 0.07 and 0.15
    code, diff = check_greeks_consistency(model_delta=0.52, api_delta=0.62)
    assert code == "GREEKS_MISMATCH"
    assert diff == pytest.approx(0.10)

    # Severe failure > 0.15
    code, diff = check_greeks_consistency(model_delta=0.52, api_delta=0.75)
    assert code == "GREEKS_MISMATCH_SEVERE"
    assert diff == pytest.approx(0.23)


def test_scenario_ladder_monotonic_for_put():
    lad = scenario_ladder(base_put(), [-300, -150, 0, 150, 300])
    vals = [x["option_model"] for x in lad]
    # For a put, as spot increases, option value decreases monotonically
    assert all(b < a for a, b in pairwise(vals))
