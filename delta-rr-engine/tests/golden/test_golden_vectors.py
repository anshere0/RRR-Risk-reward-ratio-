"""Golden vector tests: production code must match reference/tests/golden_vectors.json.

These tolerances are tight because money is involved:
  - Prices: absolute 1e-6
  - RR ratio: relative 1e-9
  - Greeks: relative 1e-6
  - IV: absolute 1e-7

If any of these fail, the production code has a bug. The reference is the oracle.
"""

import json
from pathlib import Path

import pytest

# Production code imports
from rr_engine.engine import RRParams, compute_rr, scenario_ladder
from rr_engine.pricing import bs_greeks, bs_price, implied_vol

VECTORS_PATH = Path(__file__).parent.parent.parent / "reference" / "tests" / "golden_vectors.json"


@pytest.fixture(scope="module")
def vectors():
    with open(VECTORS_PATH) as f:
        return json.load(f)


class TestPricingVectors:
    """BS price, greeks, and IV must match the golden vectors."""

    def test_prices(self, vectors):
        for v in vectors["pricing"]:
            price = bs_price(v["S"], v["K"], v["T_years"], v["sigma"], v["is_call"])
            assert price == pytest.approx(v["price"], abs=v["tolerance"]["price_abs"]), (
                f"Price mismatch for S={v['S']}, K={v['K']}, call={v['is_call']}"
            )

    def test_greeks(self, vectors):
        for v in vectors["pricing"]:
            g = bs_greeks(v["S"], v["K"], v["T_years"], v["sigma"], v["is_call"])
            tol = v["tolerance"]["greeks_rel"]
            for key in ("delta", "gamma", "vega", "theta_per_day"):
                assert g[key] == pytest.approx(v["greeks"][key], rel=tol), (
                    f"Greek {key} mismatch for S={v['S']}, K={v['K']}"
                )

    def test_implied_vol(self, vectors):
        for v in vectors["pricing"]:
            iv = implied_vol(v["price"], v["S"], v["K"], v["T_years"], v["is_call"])
            assert iv == pytest.approx(v["implied_vol_from_price"], abs=v["tolerance"]["iv_abs"]), (
                f"IV mismatch for S={v['S']}, K={v['K']}"
            )


class TestRRVectors:
    """Risk-reward computation must match the golden vectors."""

    def test_rr_output(self, vectors):
        for rv in vectors["rr"]:
            inp = rv["inputs"]
            p = RRParams(**{k: v for k, v in inp.items() if k != "extra"})
            result = compute_rr(p)
            tol_abs = rv["tolerance"]["money_abs"]
            tol_rel = rv["tolerance"]["rr_rel"]
            expected = rv["output"]

            assert result["sigma_anchor"] == pytest.approx(expected["sigma_anchor"], abs=1e-6)
            assert result["entry_price"] == pytest.approx(expected["entry_price"], abs=tol_abs)
            assert result["tp_option_model"] == pytest.approx(expected["tp_option_model"], abs=tol_abs)
            assert result["sl_option_model"] == pytest.approx(expected["sl_option_model"], abs=tol_abs)
            assert result["risk_usd"] == pytest.approx(expected["risk_usd"], abs=tol_abs)
            assert result["reward_usd"] == pytest.approx(expected["reward_usd"], abs=tol_abs)
            assert result["net_rr"] == pytest.approx(expected["net_rr"], rel=tol_rel)
            assert result["breakeven_win_probability"] == pytest.approx(
                expected["breakeven_win_probability"], rel=tol_rel
            )
            assert result["worst_case_loss_usd"] == pytest.approx(expected["worst_case_loss_usd"], abs=tol_abs)

    def test_ladder(self, vectors):
        for rv in vectors["rr"]:
            inp = rv["inputs"]
            p = RRParams(**{k: v for k, v in inp.items() if k != "extra"})
            offsets = [row["offset"] for row in rv["ladder"]]
            ladder = scenario_ladder(p, offsets)
            for got, expected in zip(ladder, rv["ladder"], strict=True):
                assert got["spot"] == pytest.approx(expected["spot"], abs=1e-6)
                assert got["option_model"] == pytest.approx(expected["option_model"], abs=1e-6)

    def test_fees_match(self, vectors):
        for rv in vectors["rr"]:
            inp = rv["inputs"]
            p = RRParams(**{k: v for k, v in inp.items() if k != "extra"})
            result = compute_rr(p)
            expected_fees = rv["output"]["fees_usd"]
            assert result["fees_usd"]["entry"] == pytest.approx(expected_fees["entry"], abs=1e-6)
            assert result["fees_usd"]["exit_tp"] == pytest.approx(expected_fees["exit_tp"], abs=1e-6)
            assert result["fees_usd"]["exit_sl"] == pytest.approx(expected_fees["exit_sl"], abs=1e-6)
