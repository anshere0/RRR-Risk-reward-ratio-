# 02 — Risk-Reward Engine Specification (the maths)

All formulas here are implemented and tested in `reference/rr_reference/`. The production code must reproduce
`reference/tests/golden_vectors.json`. If production and reference disagree, **the reference wins** until proven wrong
by an independent source.

## 0. Conventions
* Time: UTC internally; display IST (UTC+05:30).
* `S` = index price (`.DEXBTUSD`, from the product's `spot_index.symbol` — never hard-code). `K` = strike.
* Option premium is quoted **per 1 BTC**; one lot = `contract_value` BTC (0.001, read from the product). Dollar P&L = (premium change) x contract_value x lots.
* `T` years = seconds / (365 x 24 x 3600). Expiry instant = product `settlement_time` (do not assume a clock time).
* Direction: call -> +1 (bullish), put -> -1 (bearish). The user is **long** the option.
* Only **closed** candles feed indicators/structure. The forming candle is used only for the live price.

## 1. Anchor the model to the live market (calibration)
1. Reference price for the model: `mark_price` from the ticker (fall back to mid of bid/ask if mark missing; if both missing -> NO_TRADE).
2. Solve implied volatility `sigma*` such that `BS(S, K, T, sigma*) = mark` (bisection, bounds 1e-4 to 10).
   Failure (price below intrinsic / out of range / T<=0) -> NO_TRADE `MODEL_CALIBRATION_FAILED`.
3. Consistency check: compare model delta with ticker `greeks.delta`. If |difference| > 0.07 -> warning `GREEKS_MISMATCH` (units/model differences);
   > 0.15 -> NO_TRADE. (Do not use API gamma/theta numerically until their units are verified with the probe.)
4. Rate `r = 0`, no dividends (config). Basis between index and forward is absorbed by anchoring to the mark.

Why not just use Delta/Gamma Taylor expansion? It is only accurate for small moves. A 0.5-1 ATR stop/target move
is exactly where the curvature and decay terms matter. The full pricing formula re-priced at the scenario is used as primary;
the Taylor estimate is computed as a cross-check (warn if it differs from the full reprice by > 15% of the move).

## 2. Spot structure -> stop and target (on the index chart)
Indicators on closed 5m candles: ATR(14) Wilder; EMA(20), EMA(50) (seed = SMA); ADX(14) Wilder; realised vol (EWMA lambda 0.94);
VWAP is computed on the BTCUSD perpetual (the index has no volume) anchored to 00:00 UTC.

**Pivots**: fractal with k=2 on each side. A pivot is *confirmed* only k candles after it forms (never repaints).

**Stop** (`select_stop`):
* Call: the most recent confirmed swing low below spot within the last 36 candles; stop = low - 0.25 x ATR.
* Put: the most recent confirmed swing high above spot; stop = high + 0.25 x ATR.
* No pivot found -> ATR fallback: 1.0 x ATR from spot.
* Noise floor: if stop distance < 0.5 x ATR, widen to 0.5 x ATR (a stop inside normal noise gets hit by randomness).
* If stop distance > 2.0 x ATR -> NO_TRADE `STOP_TOO_WIDE`.

**Target** (`select_target`): the **nearest** opposing level in the trade direction among: confirmed swing points (last 144 candles),
prior-day high/low (UTC), session VWAP, and round numbers (multiples of 500 and 1000). If the nearest level is closer than 0.75 x ATR
-> NO_TRADE `TARGET_BLOCKED` (we never stretch the target past a wall). No level at all -> ATR fallback 1.5 x ATR ("open air").
Reachability: target distance must be <= 1.5 x sigma_5m x sqrt(N) where N = holding window in candles; else `TARGET_UNREACHABLE_IN_WINDOW`.

The user may override stop/target (HUD fields). Overrides are validated (right side of spot) and clearly marked `user_override`.

## 3. Re-price the option at the scenarios
Holding window `H` (default 30 min). Both scenarios are valued at the **end** of the window (maximum time decay = conservative):
* `V_tp = BS(S_tp, K, T - H, sigma* + iv_shock_tp)`, `iv_shock_tp = 0` (no credit for volatility gains)
* `V_sl = BS(S_sl, K, T - H, sigma* + iv_shock_sl)`, `iv_shock_sl = -0.02` (assume vol falls 2 points at the stop)

## 4. Frictions (all conservative)
* Entry at **ask** (taker). Exit at `V - half_spread` (taker at bid); at the stop use `1.5 x half_spread` (spreads widen in fast moves).
* Fees per leg: `min(taker_rate x S x contract_value x lots, premium_rate x premium x contract_value x lots) x (1 + gst)`,
  with `taker_rate`, `premium_rate` read from the product and `gst` = 0.18 on Delta India (**verify with a real fill**, see `03`).
* Worst-case loss for a long option = premium paid + entry fee. `risk_usd` can never exceed it (tested).

## 5. Outputs
```
net_pnl_tp = (exit_tp - entry) x unit - fee_in - fee_out_tp        unit = contract_value x lots
net_pnl_sl = (exit_sl - entry) x unit - fee_in - fee_out_sl
risk = -net_pnl_sl ; reward = net_pnl_tp ; net_rr = reward / risk
breakeven_win_probability = risk / (risk + reward)
```
Suggested option prices for the HUD: entry = ask (rounded UP to tick); **TP premium** = `V_tp` (what the screen should show when spot hits the target,
rounded down to tick); **SL premium** = `V_sl` (what the screen should show at the stop, rounded up). Also show the **spot** stop/target
because the spot level is the real invalidation. The HUD must make clear that the actual exit fills below the model price by the spread.

Position sizing helper (not advice): `max_lots = floor(max_risk_usd / risk_per_lot)` where `risk_per_lot` is computed at lots=1
(fees are ~linear in lots). Show it only if the user entered a risk budget.

## 6. Probability (Phase 7, optional but recommended)
Filtered historical simulation, N=20,000 paths: standardised 1-minute index return residuals from the last 7 days (bootstrap),
scaled by current EWMA vol, path simulated minute by minute for the holding window. Count which barrier (stop/target) is touched
first, or timeout. Report `p_tp`, `p_sl`, `p_timeout` with Monte Carlo standard error. Timeout outcomes are valued at the model price
at the window end minus half-spread, minus fees.
**Unit test**: with Gaussian residuals and no drift, `p_tp ~= d_sl / (d_sl + d_tp)` (gambler's ruin) when the window is long.

**Important honesty rule**: a driftless model gives expected value ~ -costs for *every* stop/target. Therefore:
* `edge_mode: none` (default until validated): report geometry + probability, verdict uses geometry gates only, banner = UNVALIDATED.
* `edge_mode: empirical` (after Phase 9): verdict also requires the Wilson 80% lower bound of the setup's historical hit rate to exceed
  `breakeven_win_probability + margin`. Never enable without logged evidence.

## 7. Worked example (hypothetical levels, assumed 17:30 IST expiry, assumed fee parameters)
Inputs resemble your screenshot: index 64,850.7, put 64,800 strike, bid 627 / ask 631, mark 629, 23h48m45s to expiry, 10 lots,
30-minute window, stop 65,010 (chart risk 159.3), target 64,560 (chart reward 290.7 -> **1.82 : 1 on the chart**).
* Anchored IV = 48.5%.  Model option value at target = 772.77, at stop = 521.86 (with -2 vol-point shock).
* Entry 631 (ask). Net exit at target 770.77, at stop 518.86 (after spread / stress spread).
* Fees ~ $0.23 per leg (assumed). **Risk $1.58, reward $0.94 -> net RR 0.59 : 1.** Break-even win rate 62.7%. => NO_TRADE `NET_RR_TOO_LOW`.
Reproduce: `cd reference && python make_golden_vectors.py`.

Lesson: the chart RR (1.82) is not the option RR (0.59). Decay, vol shock, spread and fees remove ~two-thirds of the apparent edge for this size.
