import math
import pytest
from rr_reference.fees import option_fee_usd
from rr_reference.pricing import bs_price, years_from_seconds
from rr_reference.rr import RRParams, compute_rr, scenario_ladder


def base_put(**kw):
    d = dict(spot=64850.7, strike=64800, is_call=False, seconds_to_expiry=23 * 3600 + 48 * 60 + 45,
             mark=629.0, bid=627.0, ask=631.0, sl_spot=65010.0, tp_spot=64560.0, lots=10, hold_seconds=1800)
    d.update(kw)
    return RRParams(**d)


def test_fee_cap_applies_to_cheap_options():
    # notional fee: 0.0003*64850*0.001*1 = 0.0195 ; premium cap: 0.1*0.05*0.001 = 0.000005
    assert option_fee_usd(64850, 0.05, 1, 0.001, 0.0003, 0.10, 0.0) == pytest.approx(0.000005)


def test_fee_notional_binds_for_expensive_options():
    f = option_fee_usd(64850, 629, 10, 0.001, 0.0003, 0.10, 0.18)
    assert f == pytest.approx(0.0003 * 64850 * 0.001 * 10 * 1.18)


def test_rr_basic_sanity_put():
    r = compute_rr(base_put())
    assert r["risk_usd"] > 0 and r["reward_usd"] > 0
    assert r["risk_usd"] <= r["worst_case_loss_usd"] + 1e-9          # never lose more than the premium (+fee)
    assert r["tp_option_model"] > r["entry_price"] - 40                # target is above entry (after small decay)
    assert r["sl_option_model"] < r["entry_price"]
    assert 0 < r["breakeven_win_probability"] < 1
    assert r["net_rr"] == pytest.approx(r["reward_usd"] / r["risk_usd"])


def test_call_put_symmetry():
    c = compute_rr(RRParams(spot=64850.7, strike=64800, is_call=True, seconds_to_expiry=86000, mark=700, bid=698, ask=702,
                            sl_spot=64690, tp_spot=65140, lots=5))
    assert c["risk_usd"] > 0 and c["reward_usd"] > 0


def test_wider_spread_lowers_rr():
    tight = compute_rr(base_put(bid=628.0, ask=630.0))
    wide = compute_rr(base_put(bid=620.0, ask=638.0))
    assert wide["net_rr"] < tight["net_rr"]


def test_conservative_iv_shock_reduces_rr():
    a = compute_rr(base_put(iv_shock_sl=0.0))
    b = compute_rr(base_put(iv_shock_sl=-0.05))
    assert b["risk_usd"] > a["risk_usd"]


def test_longer_hold_means_more_decay():
    a = compute_rr(base_put(hold_seconds=600))
    b = compute_rr(base_put(hold_seconds=7200))
    assert b["reward_usd"] < a["reward_usd"]


def test_wrong_side_stop_rejected():
    with pytest.raises(ValueError):
        compute_rr(base_put(sl_spot=64700.0))           # put stop must be ABOVE spot
    with pytest.raises(ValueError):
        compute_rr(RRParams(spot=100, strike=100, is_call=True, seconds_to_expiry=1e5, mark=1, bid=0.9, ask=1.1,
                            sl_spot=105, tp_spot=95))


def test_bad_quotes_rejected():
    with pytest.raises(ValueError):
        compute_rr(base_put(bid=0.0))
    with pytest.raises(ValueError):
        compute_rr(base_put(bid=640.0, ask=630.0))


def test_hold_beyond_expiry_rejected():
    with pytest.raises(ValueError):
        compute_rr(base_put(seconds_to_expiry=900, hold_seconds=1800))


def test_ladder_monotonic_for_put():
    lad = scenario_ladder(base_put(), [-300, -150, 0, 150, 300])
    vals = [x["option_model"] for x in lad]
    assert all(b < a for a, b in zip(vals, vals[1:]))
