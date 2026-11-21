#!/usr/bin/env python3
"""Live terminal monitor for Delta Exchange feeds.

Prints a one-line status update every second showing:
  - BTC Spot Index price (.DEXBTUSD)
  - Active Option quotes (Bid / Ask / Mark / Delta / IV)
  - Data age & connection status
  - Number of closed candles in memory

Usage:
    python scripts/live_monitor.py
    python scripts/live_monitor.py --symbol C-BTC-61400-211126 --region india
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import sys

from rr_engine.config import Settings
from rr_engine.feed.manager import FeedManager


async def monitor(symbol: str | None, region: str) -> None:
    settings = Settings(region=region)
    manager = FeedManager(settings=settings)

    print("=" * 80)
    print(f"Starting Delta RR Feed Monitor ({region.upper()})...")
    print("Press Ctrl+C to stop.")
    print("=" * 80)

    try:
        await manager.start(initial_symbol=symbol)
        active_sym = manager.active_symbol or "Discovering..."
        print(f"Connected! Monitoring active symbol: {active_sym}\n")

        while True:
            await asyncio.sleep(1.0)
            now_utc = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")

            state = manager.state
            spot_str = f"${state.spot_index_price:,.1f}" if state.spot_index_price else "Waiting..."
            sym = manager.active_symbol or "None"
            tk = state.get_ticker(sym)

            if tk and tk.best_bid > 0 and tk.best_ask > 0:
                quote_str = f"Bid: {tk.best_bid:,.1f} | Ask: {tk.best_ask:,.1f} | Mark: {tk.mark_price:,.1f}"
                delta_str = f"Δ: {tk.delta:+.3f}" if tk.delta is not None else "Δ: N/A"
                iv_str = f"IV: {tk.mark_iv * 100:.1f}%" if tk.mark_iv is not None else "IV: N/A"
            else:
                quote_str = "Bid: -- | Ask: -- | Mark: --"
                delta_str = "Δ: --"
                iv_str = "IV: --"

            age = state.clock.max_data_age(("index", "option_ticker"))
            age_str = f"{age:.1f}s" if age != float("inf") else "Init"

            conn_str = "CONNECTED" if state.is_ws_connected else "RECONNECTING"
            candles_count = len(state.candles_5m)

            line = (
                f"[{now_utc}] Spot: {spot_str} | {sym} | "
                f"{quote_str} | {delta_str} | {iv_str} | "
                f"Age: {age_str} | 5m Candles: {candles_count} | [{conn_str}]"
            )

            # Print status line (overwriting or clean print)
            sys.stdout.write("\r" + line)
            sys.stdout.flush()

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopping monitor...")
    finally:
        await manager.stop()
        print("Monitor stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Delta Exchange Live Feed Monitor")
    parser.add_argument("--symbol", default=None, help="Option symbol to track (auto-discovers if omitted)")
    parser.add_argument("--region", choices=["india", "global"], default="india")
    args = parser.parse_args()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(monitor(args.symbol, args.region))


if __name__ == "__main__":
    main()
