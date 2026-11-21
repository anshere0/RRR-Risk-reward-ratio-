#!/usr/bin/env python3
"""Probe Delta Exchange API to capture real data shapes. Run FIRST — nothing is guessed.

Auto-discovers live BTC option symbols if no --symbol is given.

    pip install httpx websockets
    python scripts/probe_delta.py --region india --seconds 25
    python scripts/probe_delta.py --symbol P-BTC-64800-281126 --region india --seconds 25

Output: docs/probe_output.json (give this file to the agent / keep for contract tests).
Read-only. No API key. No orders.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import websockets

HOSTS = {
    "india": {"rest": "https://api.india.delta.exchange", "ws": "wss://public-socket.india.delta.exchange"},
    "global": {"rest": "https://api.delta.exchange", "ws": "wss://socket.delta.exchange"},
}
OUT = {"notes": [], "rest": {}, "ws": {}}


def note(msg):
    print("NOTE:", msg)
    OUT["notes"].append(msg)


def unit_of(ts):
    try:
        v = float(ts)
    except Exception:
        return "unknown"
    return "microseconds" if v > 1e15 else "milliseconds" if v > 1e12 else "seconds"


def discover_live_symbol(host: str) -> str | None:
    """Find a live ATM BTC option symbol automatically.

    Strategy: fetch live BTC options, pick the nearest expiry ATM put or call.
    """
    print("\n=== Auto-discovering live BTC option symbols ===")
    with httpx.Client(base_url=host, timeout=15) as c:
        # Get live option products
        r = c.get("/v2/products", params={
            "contract_types": "call_options,put_options",
            "states": "live",
        })
        if r.status_code != 200:
            note(f"Product discovery failed ({r.status_code}): {r.text[:300]}")
            return None

        products = r.json().get("result", [])
        # Filter to BTC options only
        btc_options = [p for p in products if (p.get("underlying_asset", {}) or {}).get("symbol") == "BTC"]

        if not btc_options:
            note("No live BTC options found!")
            return None

        print(f"Found {len(btc_options)} live BTC options")
        OUT["rest"]["discovered_products_count"] = len(btc_options)

        # Get current BTC index price to find ATM
        # Try to get a ticker for one of the options to read spot_price
        sample_sym = btc_options[0]["symbol"]
        t = c.get(f"/v2/tickers/{sample_sym}")
        spot_price = None
        if t.status_code == 200:
            ticker_data = t.json().get("result", {})
            spot_price = float(ticker_data.get("spot_price", 0)) if ticker_data.get("spot_price") else None

        if not spot_price:
            # Fallback: try the index ticker directly
            idx_sym = (btc_options[0].get("spot_index", {}) or {}).get("symbol", ".DEXBTUSD")
            t2 = c.get(f"/v2/tickers/{idx_sym}")
            if t2.status_code == 200:
                td = t2.json().get("result", {})
                spot_price = float(td.get("price", 0) or td.get("close", 0)) if td else None

        if spot_price and spot_price > 0:
            print(f"Current BTC spot price: {spot_price}")
        else:
            spot_price = 65000.0  # rough fallback
            note(f"Could not fetch spot price, using rough estimate {spot_price}")

        # Sort by settlement_time (nearest expiry first), then by distance from ATM
        for p in btc_options:
            try:
                p["_strike"] = float(p.get("strike_price", 0))
                p["_settle"] = int(p.get("settlement_time", 0)) if p.get("settlement_time") else 9999999999
                p["_atm_dist"] = abs(p["_strike"] - spot_price)
                p["_type"] = "P" if "put" in p.get("contract_type", "") else "C"
            except (ValueError, TypeError):
                p["_strike"] = 0
                p["_settle"] = 9999999999
                p["_atm_dist"] = float("inf")
                p["_type"] = "?"

        # Nearest expiry, closest to ATM
        btc_options.sort(key=lambda p: (p["_settle"], p["_atm_dist"]))

        # Pick the best ATM option (prefer put since the example was a put)
        best = btc_options[0]
        symbol = best.get("symbol", "")

        # Show top 5 candidates
        print("\nTop 5 ATM options (nearest expiry):")
        for i, p in enumerate(btc_options[:5]):
            print(f"  {i+1}. {p['symbol']}  strike={p['_strike']}  "
                  f"dist_from_ATM={p['_atm_dist']:.0f}  type={p['_type']}  "
                  f"settle={p['_settle']}")

        OUT["rest"]["discovered_top5"] = [
            {"symbol": p["symbol"], "strike": p["_strike"],
             "atm_distance": p["_atm_dist"], "type": p["_type"]}
            for p in btc_options[:5]
        ]

        print(f"\nSelected: {symbol}")
        return symbol


def rest_probe(host, symbol):
    with httpx.Client(base_url=host, timeout=15) as c:
        r = c.get(f"/v2/products/{symbol}")
        OUT["rest"]["product_status"] = r.status_code
        if r.status_code != 200:
            note(f"Product lookup failed ({r.status_code}): wrong region or expired symbol? Body: {r.text[:300]}")
            return None
        prod = r.json().get("result", {})
        keep = ["id", "symbol", "contract_type", "strike_price", "contract_value", "tick_size",
                "settlement_time", "state", "trading_status", "taker_commission_rate",
                "maker_commission_rate", "product_specs", "spot_index", "underlying_asset"]
        OUT["rest"]["product"] = {k: prod.get(k) for k in keep}
        print("\n=== Product ===")
        print(json.dumps(OUT["rest"]["product"], indent=2, default=str))

        t = c.get(f"/v2/tickers/{symbol}")
        OUT["rest"]["ticker_status"] = t.status_code
        OUT["rest"]["ticker"] = t.json().get("result") if t.status_code == 200 else t.text[:300]
        print("\n=== Ticker ===")
        print(json.dumps(OUT["rest"]["ticker"], indent=2, default=str))

        if isinstance(OUT["rest"]["ticker"], dict):
            note(f"ticker.timestamp unit looks like: {unit_of(OUT['rest']['ticker'].get('timestamp'))}")

        idx = (prod.get("spot_index") or {}).get("symbol")
        OUT["rest"]["index_symbol"] = idx
        print(f"\nIndex symbol from product: {idx}")

        end = int(time.time())
        start = end - 3 * 3600
        for sym in {idx, ".DEXBTUSD"} - {None}:
            h = c.get("/v2/history/candles", params={"symbol": sym, "resolution": "5m", "start": start, "end": end})
            body = h.json() if h.status_code == 200 else h.text[:300]
            OUT["rest"][f"candles_{sym}"] = {
                "status": h.status_code,
                "sample": (body.get("result", [])[:3] if isinstance(body, dict) else body),
            }
            print(f"\n=== Candles {sym}: status {h.status_code} ===")
            print(str(OUT["rest"][f"candles_{sym}"]["sample"])[:500])
        return prod


async def ws_probe(ws_url, symbol, index_symbol, seconds):
    variants = [
        [{"name": "ticker", "symbols": [symbol]},
         {"name": "candlestick_5m", "symbols": [index_symbol]},
         {"name": "spot_price", "symbols": [index_symbol]},
         {"name": "ob_l1", "symbols": [symbol]}],
        [{"name": "v2/ticker", "symbols": [symbol]}],
    ]
    for n, channels in enumerate(variants):
        seen = {}
        try:
            async with websockets.connect(ws_url, ping_interval=None) as ws:
                await ws.send(json.dumps({"type": "enable_heartbeat"}))
                await ws.send(json.dumps({"type": "subscribe", "payload": {"channels": channels}}))
                end_time = time.time() + seconds
                while time.time() < end_time:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except TimeoutError:
                        continue
                    msg = json.loads(raw)
                    key = msg.get("type") or msg.get("channel") or "unknown"
                    seen.setdefault(key, [])
                    if len(seen[key]) < 3:
                        seen[key].append(msg)
        except Exception as e:  # noqa: BLE001
            note(f"WebSocket variant {n} error: {e!r}")
        OUT["ws"][f"variant_{n}"] = seen
        print(f"\n=== WS variant {n}: message types seen: {list(seen)} ===")
        for k, v in seen.items():
            print(k, json.dumps(v[0], default=str)[:600])
        if any(k not in ("subscriptions", "heartbeat", "unknown") for k in seen):
            break


def main():
    ap = argparse.ArgumentParser(description="Probe Delta Exchange API — captures real data shapes")
    ap.add_argument("--symbol", default=None, help="Option symbol (e.g. P-BTC-64800-281126). "
                     "If not given, auto-discovers the nearest ATM BTC option.")
    ap.add_argument("--region", choices=list(HOSTS), default="india")
    ap.add_argument("--seconds", type=int, default=25, help="How many seconds to listen on WebSocket")
    a = ap.parse_args()

    h = HOSTS[a.region]
    symbol = a.symbol

    if symbol is None:
        symbol = discover_live_symbol(h["rest"])
        if symbol is None:
            print("\nERROR: Could not auto-discover a live BTC option. "
                  "Try passing --symbol manually with a live option symbol from Delta Exchange.")
            return

    OUT["probed_symbol"] = symbol
    OUT["region"] = a.region
    OUT["probed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    print(f"\n{'='*60}")
    print(f"Probing symbol: {symbol}  region: {a.region}")
    print(f"{'='*60}")

    prod = rest_probe(h["rest"], symbol)
    idx = ((prod or {}).get("spot_index") or {}).get("symbol") or ".DEXBTUSD"

    print(f"\n{'='*60}")
    print(f"WebSocket probe ({a.seconds}s): {h['ws']}")
    print(f"{'='*60}")
    asyncio.run(ws_probe(h["ws"], symbol, idx, a.seconds))

    out_path = Path("docs/probe_output.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(OUT, indent=2, default=str))
    print(f"\n{'='*60}")
    print(f"Saved {out_path.resolve()}")
    print(f"{'='*60}")

    # Summary of what was verified
    print("\n=== VERIFICATION SUMMARY ===")
    verified = []
    unverified = []

    if OUT["rest"].get("product_status") == 200:
        verified.append("Product fields (symbol, strike, contract_value, tick_size, settlement_time, fees)")
    else:
        unverified.append("Product lookup FAILED")

    if OUT["rest"].get("ticker_status") == 200:
        verified.append("Ticker fields (mark_price, greeks, quotes, spot_price)")
    else:
        unverified.append("Ticker lookup FAILED")

    if any("candles_" in k and OUT["rest"][k]["status"] == 200 for k in OUT["rest"]):
        verified.append("Candle history endpoint works")
    else:
        unverified.append("Candle history endpoint FAILED")

    ws_data = OUT.get("ws", {})
    ws_channels_seen = set()
    for vk in ws_data:
        ws_channels_seen.update(ws_data[vk].keys())
    ws_channels_seen -= {"subscriptions", "heartbeat", "unknown"}
    if ws_channels_seen:
        verified.append(f"WebSocket channels working: {ws_channels_seen}")
    else:
        unverified.append("No WebSocket data channels received")

    print("VERIFIED:")
    for v in verified:
        print(f"  ✓ {v}")
    print("STILL NEEDS VERIFICATION:")
    for u in unverified:
        print(f"  ✗ {u}")
    if not unverified:
        unverified_items = [
            "Greeks units (theta per-day vs per-year, gamma units)",
            "mark_vol units (percent vs decimal)",
            "Fee calibration against a real fill",
            "Exact candle close detection timing",
        ]
        print("  (Core API works, but still verify with a real trade:)")
        for item in unverified_items:
            print(f"    ? {item}")


if __name__ == "__main__":
    main()
