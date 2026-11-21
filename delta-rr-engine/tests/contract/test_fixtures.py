"""Contract tests validating recorded Delta Exchange API and WebSocket payload shapes.

These fixtures were recorded by scripts/probe_delta.py against live Delta Exchange India.
"""

import json
from decimal import Decimal
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "probe_output.json"


def test_probe_output_exists_and_valid():
    assert FIXTURE_PATH.exists(), "probe_output.json fixture missing"
    with open(FIXTURE_PATH) as f:
        data = json.load(f)

    assert data["region"] == "india"
    assert data["probed_symbol"].startswith(("C-BTC-", "P-BTC-"))


def test_rest_product_contract():
    with open(FIXTURE_PATH) as f:
        data = json.load(f)

    prod = data["rest"]["product"]
    assert prod["state"] == "live"
    assert prod["trading_status"] == "operational"
    assert prod["spot_index"]["symbol"] == ".DEXBTUSD"

    # Price / tick / fees are parseable to Decimal
    assert Decimal(prod["strike_price"]) > 0
    assert Decimal(prod["contract_value"]) == Decimal("0.001")
    assert Decimal(prod["tick_size"]) > 0
    assert Decimal(prod["taker_commission_rate"]) > 0
    assert prod["product_specs"]["premium_commission_rate"] > 0


def test_rest_ticker_contract():
    with open(FIXTURE_PATH) as f:
        data = json.load(f)

    ticker = data["rest"]["ticker"]
    assert Decimal(ticker["mark_price"]) > 0
    assert Decimal(ticker["spot_price"]) > 0
    assert Decimal(ticker["quotes"]["best_bid"]) > 0
    assert Decimal(ticker["quotes"]["best_ask"]) >= Decimal(ticker["quotes"]["best_bid"])

    # Timestamps are microsecond integers (> 1e15)
    assert ticker["timestamp"] > 1e15


def test_ws_channels_contract():
    with open(FIXTURE_PATH) as f:
        data = json.load(f)

    ws_data = data["ws"]["variant_0"]
    assert "heartbeat" in ws_data
    assert "spot_price" in ws_data
    assert "ob_l1" in ws_data
    assert "ticker" in ws_data

    # Check ob_l1 shape
    ob = ws_data["ob_l1"][0]
    assert "bp" in ob and "ap" in ob and "bs" in ob and "as" in ob
    assert Decimal(ob["bp"]) > 0
    assert Decimal(ob["ap"]) >= Decimal(ob["bp"])

    # Check spot_price shape
    sp = ws_data["spot_price"][0]
    assert sp["sy"] == ".DEXBTUSD"
    assert float(sp["p"]) > 0

    # Check ticker compact shape
    tk = ws_data["ticker"][0]
    assert "sp" in tk
    assert "d" in tk and len(tk["d"]) > 0
    d0 = tk["d"][0]
    assert "m" in d0  # mark price
    assert "q" in d0  # quotes array
    assert "g" in d0  # greeks array
    assert len(d0["g"]) == 5  # delta, gamma, rho, theta, vega
