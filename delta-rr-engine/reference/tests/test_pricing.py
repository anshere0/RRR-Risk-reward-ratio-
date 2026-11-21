import math
import pytest
from rr_reference.pricing import bs_price, bs_greeks, implied_vol, ImpliedVolError, years_from_seconds


def test_textbook_value_r0():
    # S=K=100, T=1, sigma=0.2, r=0 -> C = P = 100*(2*N(0.1)-1) = 7.9655674
    assert bs_price(100, 100, 1, 0.2, True) == pytest.approx(7.9655674, abs=1e-6)
    assert bs_price(100, 100, 1, 0.2, False) == pytest.approx(7.9655674, abs=1e-6)


@pytest.mark.parametrize("S,K,T,sig,r", [(64850.7, 64800, 0.0027, 0.45, 0.0), (100, 90, 0.5, 0.3, 0.05), (100, 120, 2, 0.8, 0.02)])
def test_put_call_parity(S, K, T, sig, r):
    c = bs_price(S, K, T, sig, True, r)
    p = bs_price(S, K, T, sig, False, r)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-7 * S)


@pytest.mark.parametrize("is_call", [True, False])
@pytest.mark.parametrize("S,K,T,sig", [(64850.7, 64800, 0.0027, 0.45), (64850.7, 65500, 0.01, 0.6), (64850.7, 63000, 0.002, 0.35)])
def test_iv_round_trip(S, K, T, sig, is_call):
    price = bs_price(S, K, T, sig, is_call)
    assert implied_vol(price, S, K, T, is_call) == pytest.approx(sig, abs=1e-7)


def test_iv_rejects_below_intrinsic():
    with pytest.raises(ImpliedVolError):
        implied_vol(10.0, 64000, 60000, 0.003, True)   # intrinsic is 4000


def test_iv_rejects_expired():
    with pytest.raises(ImpliedVolError):
        implied_vol(10.0, 64000, 64000, 0.0, True)


def test_greeks_match_finite_differences():
    S, K, T, sig = 64850.7, 64800, 0.0027, 0.45
    for is_call in (True, False):
        g = bs_greeks(S, K, T, sig, is_call)
        h = 1.0
        d_fd = (bs_price(S + h, K, T, sig, is_call) - bs_price(S - h, K, T, sig, is_call)) / (2 * h)
        g_fd = (bs_price(S + h, K, T, sig, is_call) - 2 * bs_price(S, K, T, sig, is_call) + bs_price(S - h, K, T, sig, is_call)) / h ** 2
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
    assert all(b > a for a, b in zip(calls, calls[1:]))
    assert all(b < a for a, b in zip(puts, puts[1:]))


def test_expiry_value_is_intrinsic():
    assert bs_price(65000, 64800, 0.0, 0.5, True) == 200
    assert bs_price(65000, 64800, 0.0, 0.5, False) == 0


def test_years_from_seconds():
    assert years_from_seconds(365 * 24 * 3600) == 1.0
    assert years_from_seconds(-5) == 0.0
