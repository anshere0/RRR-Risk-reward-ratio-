"""Backtest on real historical BTC candles fetched from Delta Exchange."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from backtest_spot import print_backtest_report, run_spot_backtest

from rr_engine.feed.rest import DeltaRestClient


async def main():
    print("[*] Fetching real historical 5m candles for .DEXBTUSD from Delta Exchange...")
    client = DeltaRestClient(base_url="https://api.india.delta.exchange")
    now_sec = int(time.time())
    step = 300  # 5 min
    # Fetch 1500 candles (~5.2 days of 5m candles)
    count = 1500
    start_sec = now_sec - count * step

    candles_raw = await client.get_candles(symbol=".DEXBTUSD", resolution="5m", start=start_sec, end=now_sec)
    await client.close()

    print(f"[+] Fetched {len(candles_raw)} real candles from Delta Exchange.")
    if len(candles_raw) < 100:
        print("[-] Insufficient candles received.")
        return

    candles_dict = [
        {"t": c.t, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": getattr(c, "v", 1.0)}
        for c in candles_raw
    ]

    trades = run_spot_backtest(candles_dict, hold_bars=6, warmup_bars=60)
    print_backtest_report(trades)

if __name__ == "__main__":
    asyncio.run(main())
