"""scripts/backtest_spot.py: Spot-only historical backtest with zero look-ahead bias.

Requirements (docs/01 §4, docs/05 §Part 2 Backtest A):
  - Uses historical 5m index candles.
  - Generates engine's stop and target for every candle in both directions (Call/Put).
  - Strictly avoids look-ahead: uses only candles closed before the decision time.
  - Simulates forward over holding window: records whether TP or SL is hit first, or timeout.
  - Conservative intra-bar rule: if both TP and SL are touched in the same bar, SL is hit first.
  - Aggregates hit rates by bucket:
      * ADX band (chop <18, transition 18-25, trending >25)
      * EMA alignment (trend-following vs counter-trend)
      * ATR quartile (volatility regime)
      * Time of day (Asian, European, US sessions)
  - Compares empirical hit-rate against required break-even win rate.

Usage:
  python scripts/backtest_spot.py [--candles file.json] [--hold 6] [--warmup 60] [--synthetic]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rr_engine.indicators import (
    adx_wilder,
    atr_wilder,
    ema,
)
from rr_engine.structure import (
    check_target_reachability,
    select_stop,
    select_target,
)


@dataclass
class BacktestTrade:
    bar_index: int
    timestamp: int
    direction: str  # 'call' | 'put'
    spot: float
    sl: float
    tp: float
    atr: float
    adx: float
    ema_aligned: bool
    atr_quartile: str
    session: str
    outcome: str  # 'TP_HIT' | 'SL_HIT' | 'TIMEOUT'
    duration_bars: int


def classify_session(utc_hour: int) -> str:
    """Classify UTC hour into standard trading sessions."""
    if 0 <= utc_hour < 8:
        return "Asian (00-08 UTC)"
    if 8 <= utc_hour < 14:
        return "European (08-14 UTC)"
    if 14 <= utc_hour < 22:
        return "US (14-22 UTC)"
    return "Off-Hours (22-24 UTC)"


def generate_synthetic_btc_candles(count: int = 1000, start_price: float = 64000.0, seed: int = 42) -> list[dict[str, Any]]:
    """Generate realistic synthetic 5m BTC candles with trending, mean-reverting, and noisy regimes."""
    rng = np.random.default_rng(seed)
    candles = []
    current_price = start_price
    t0 = 1732000000  # epoch seconds

    for i in range(count):
        # Regime switching noise
        regime = (i // 150) % 3
        if regime == 0:
            drift = 0.0001
            vol = 0.002
        elif regime == 1:
            drift = -0.0001
            vol = 0.003
        else:
            drift = 0.0
            vol = 0.0015

        ret = drift + rng.normal(0, vol)
        close = max(1000.0, current_price * math.exp(ret))
        high = max(current_price, close) * (1.0 + abs(rng.normal(0, vol * 0.5)))
        low = min(current_price, close) * (1.0 - abs(rng.normal(0, vol * 0.5)))
        open_p = current_price
        current_price = close

        candles.append({
            "t": t0 + (i * 300),
            "o": round(open_p, 1),
            "h": round(high, 1),
            "l": round(low, 1),
            "c": round(close, 1),
            "v": round(rng.uniform(10.0, 100.0), 2),
        })
    return candles


def run_spot_backtest(
    candles: list[dict[str, Any]],
    hold_bars: int = 6,
    warmup_bars: int = 60,
    min_atr_distance: float = 0.5,
    max_atr_distance: float = 2.0,
) -> list[BacktestTrade]:
    """Execute spot backtest with strict no look-ahead indexing."""
    total_len = len(candles)
    if total_len < warmup_bars + hold_bars:
        return []

    # Precalculate ATR percentiles for quartile classification
    all_closes = [float(c["c"]) for c in candles]
    atrs_all = atr_wilder(candles, n=14)
    valid_atrs = [a for a in atrs_all if a is not None]
    q25, q50, q75 = (
        np.percentile(valid_atrs, [25, 50, 75])
        if len(valid_atrs) >= 20
        else (50.0, 100.0, 150.0)
    )

    trades: list[BacktestTrade] = []

    # Slide step-by-step strictly without look-ahead
    for t in range(warmup_bars, total_len - hold_bars):
        # 1. Closed candles available strictly up to t-1
        closed = candles[:t]
        current_bar = closed[-1]
        spot = float(current_bar["c"])
        ts = int(current_bar["t"])
        utc_hour = (ts // 3600) % 24
        session_label = classify_session(utc_hour)

        # 2. Indicators at decision time t
        atr = atrs_all[t - 1]
        if atr is None or atr <= 0:
            continue

        # ATR Quartile
        if atr <= q25:
            atr_q = "Q1 (Low Vol)"
        elif atr <= q50:
            atr_q = "Q2 (Medium-Low)"
        elif atr <= q75:
            atr_q = "Q3 (Medium-High)"
        else:
            atr_q = "Q4 (High Vol)"

        closes_t = all_closes[:t]
        ema20_series = ema(closes_t, 20)
        ema50_series = ema(closes_t, 50)
        ema20 = ema20_series[-1] or spot
        ema50 = ema50_series[-1] or spot

        adx_dict = adx_wilder(closed, n=14)
        adx = adx_dict["adx"][-1] if adx_dict["adx"][-1] is not None else 15.0

        # Evaluate both Call (bullish) and Put (bearish) setups
        for direction, dir_name in [(1, "call"), (-1, "put")]:
            is_call = (direction == 1)
            ema_aligned = (ema20 > ema50) if is_call else (ema20 < ema50)

            # Structure stop and target
            sl_info = select_stop(
                direction=direction,
                spot=spot,
                candles=closed,
                atr=atr,
                k=2,
                lookback=36,
                min_atr=min_atr_distance,
                max_atr=max_atr_distance,
            )
            sl_price = sl_info["sl_spot"]

            tp_info = select_target(
                direction=direction,
                spot=spot,
                candles=closed,
                atr=atr,
                k=2,
                lookback=144,
                fallback_atr=1.5,
            )
            tp_price = tp_info["tp_spot"]

            # Reachability check
            reach_ok, _, _ = check_target_reachability(
                target_dist=abs(tp_price - spot),
                spot=spot,
                sigma_5m=0.003,
                holding_candles=hold_bars,
            )
            if not reach_ok:
                continue

            # 3. Simulate forward over future holding window: [t, t + hold_bars - 1]
            outcome = "TIMEOUT"
            duration = hold_bars

            for step, future_idx in enumerate(range(t, t + hold_bars)):
                f_bar = candles[future_idx]
                f_high = float(f_bar["h"])
                f_low = float(f_bar["l"])

                if is_call:
                    hit_tp = (f_high >= tp_price)
                    hit_sl = (f_low <= sl_price)
                else:
                    hit_tp = (f_low <= tp_price)
                    hit_sl = (f_high >= sl_price)

                if hit_tp and hit_sl:
                    # Conservative rule: if both barriers touched in same bar, SL wins
                    outcome = "SL_HIT"
                    duration = step + 1
                    break
                elif hit_sl:
                    outcome = "SL_HIT"
                    duration = step + 1
                    break
                elif hit_tp:
                    outcome = "TP_HIT"
                    duration = step + 1
                    break

            trades.append(BacktestTrade(
                bar_index=t,
                timestamp=ts,
                direction=dir_name,
                spot=spot,
                sl=sl_price,
                tp=tp_price,
                atr=atr,
                adx=adx,
                ema_aligned=ema_aligned,
                atr_quartile=atr_q,
                session=session_label,
                outcome=outcome,
                duration_bars=duration,
            ))

    return trades


def print_backtest_report(trades: list[BacktestTrade], break_even_target: float = 0.40) -> None:
    """Print structured hit-rate tables by bucket."""
    total = len(trades)
    if total == 0:
        print("No trades evaluated.")
        return

    tp_count = sum(1 for t in trades if t.outcome == "TP_HIT")
    sl_count = sum(1 for t in trades if t.outcome == "SL_HIT")
    timeout_count = sum(1 for t in trades if t.outcome == "TIMEOUT")
    decided = tp_count + sl_count
    overall_win_rate = (tp_count / decided) if decided > 0 else 0.0

    print("==================================================================")
    print(" SPOT-ONLY HISTORICAL BACKTEST REPORT (Backtest A - docs/05)")
    print("==================================================================")
    print(f" Total Setups Evaluated : {total}")
    print(f" Target Hits (TP)       : {tp_count} ({tp_count / total * 100:.1f}%)")
    print(f" Stop Hits (SL)         : {sl_count} ({sl_count / total * 100:.1f}%)")
    print(f" Timeouts               : {timeout_count} ({timeout_count / total * 100:.1f}%)")
    print(f" Decided Hit Rate       : {overall_win_rate * 100:.2f}% (Target BE: {break_even_target * 100:.1f}%)")
    print("------------------------------------------------------------------")

    def print_bucket_table(title: str, bucket_fn: Any) -> None:
        groups: dict[str, list[BacktestTrade]] = defaultdict(list)
        for t in trades:
            groups[bucket_fn(t)].append(t)

        print(f"\n--- {title} ---")
        print(f" {'Bucket':<24} | {'Setups':<7} | {'TP':<5} | {'SL':<5} | {'Hit Rate':<10} | {'Status'}")
        print(" " + "-" * 65)

        for name in sorted(groups.keys()):
            subset = groups[name]
            tp = sum(1 for x in subset if x.outcome == "TP_HIT")
            sl = sum(1 for x in subset if x.outcome == "SL_HIT")
            dec = tp + sl
            hr = (tp / dec) if dec > 0 else 0.0
            status = "✓ EDGE" if hr >= break_even_target else "✗ BELOW BE"
            print(f" {name:<24} | {len(subset):<7} | {tp:<5} | {sl:<5} | {hr * 100:6.1f}%    | {status}")

    # 1. ADX Bands
    def adx_bucket(t: BacktestTrade) -> str:
        if t.adx < 18:
            return "ADX < 18 (Chop)"
        elif t.adx <= 25:
            return "ADX 18-25 (Transition)"
        else:
            return "ADX > 25 (Trending)"

    print_bucket_table("1. HIT RATE BY ADX REGIME", adx_bucket)

    # 2. EMA Trend Alignment
    print_bucket_table("2. HIT RATE BY EMA TREND ALIGNMENT", lambda t: "Trend-Following (Aligned)" if t.ema_aligned else "Counter-Trend (Divergent)")

    # 3. ATR Quartiles
    print_bucket_table("3. HIT RATE BY ATR VOLATILITY REGIME", lambda t: t.atr_quartile)

    # 4. Trading Session / Time of Day
    print_bucket_table("4. HIT RATE BY TIME OF DAY (UTC SESSION)", lambda t: t.session)

    print("\n==================================================================")
    print(" SUMMARY CONCLUSION:")
    if overall_win_rate >= break_even_target:
        print(f" ✓ Structural trade filters show positive hit rate ({overall_win_rate*100:.1f}% >= {break_even_target*100:.1f}%).")
    else:
        print(f" ℹ Raw setups average {overall_win_rate*100:.1f}%; trade only when ADX and trend aligned.")
    print("==================================================================")


def main() -> None:
    parser = argparse.ArgumentParser(description="Spot-only historical backtest (no look-ahead).")
    parser.add_argument("--candles", help="Path to JSON file with 5m candles.")
    parser.add_argument("--hold", type=int, default=6, help="Holding duration in bars (default: 6 bars = 30m).")
    parser.add_argument("--warmup", type=int, default=60, help="Minimum warmup candles.")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic realistic BTC candles.")
    args = parser.parse_args()

    if args.candles and Path(args.candles).exists():
        with open(args.candles) as f:
            data = json.load(f)
            candles = data if isinstance(data, list) else data.get("result", [])
    else:
        print("[*] Generating 1,000 synthetic 5m BTC candles with trending and choppy regimes...")
        candles = generate_synthetic_btc_candles(count=1000)

    trades = run_spot_backtest(candles=candles, hold_bars=args.hold, warmup_bars=args.warmup)
    print_backtest_report(trades)


if __name__ == "__main__":
    main()
