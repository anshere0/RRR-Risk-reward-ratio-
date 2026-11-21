# 05 — Validation and Go-Live

"Accurate" has two parts: (1) the **maths is right** (testable now), and (2) the **verdicts help you make money** (only provable with data).

## Part 1 — Maths correctness (before any live use)
1. `reference/` tests pass (38). Production code passes the same golden vectors (`tests/golden`), tolerance: price 1e-6, RR 1e-9 relative.
2. Property tests (Hypothesis or loops): put-call parity; monotonic option value in spot; risk <= premium + fee; RR falls when spread widens, vol shock worsens, or hold lengthens; IV round-trips.
3. Indicator cross-check vs `ta` on recorded candles (ATR/ADX within 0.5 %).
4. Replay determinism: the journal stores every input; `scripts/replay.py <id>` must reproduce the stored output exactly.
5. Contract tests with **recorded real Delta messages** (from the probe): the parser must produce the same state from the recording every time.
6. Chaos tests: kill the connection mid-evaluation; send a crossed book; send a stale ticker; send a candle gap -> expect the right NO_TRADE code, never a number.
7. Manual spot check (do this by hand, 5 times): for 5 live evaluations, compute the option price at the target by looking at Delta's option chain... or, on testnet, place small orders and compare the HUD's predicted option price at stop/target vs what the option actually showed when BTC touched those levels. Record errors; the average absolute error of `V_tp`/`V_sl` should be within ~1.5 x the spread. Larger -> adjust the vol-shock/decay assumptions, do not "fix" by eye.

## Part 2 — Do the verdicts work?
**Backtest A (spot only, years of data, free)**: using historical 5m index candles, generate the engine's stop/target for every candle in both directions;
record whether target or stop is hit first within the window. Output: hit rates by bucket (ADX band x EMA alignment x time of day x ATR percentile).
This tells you whether TRADE-state setups hit more often than the break-even rate. Avoid look-ahead: use only candles closed before the decision time.
**Backtest B (option P&L, approximate)**: price the option with the model using a vol proxy; useful for shape, **not** for proof (no real spreads).
**Forward test C (the real proof)**: paper-trade with live quotes (journal every evaluation, mark real bid/ask at entry and at the exit time; testnet or tiny real size).

**Calibration**: predicted p_tp vs realised frequency in 10 bins (reliability table); report Brier score. Target: bins within +/-7 points over >= 200 evaluations.

**Go-live criteria (all required)**
* >= 200 logged TRADE verdicts that were paper-traded (or taken at minimum size) over >= 4 weeks covering different market regimes.
* Net expectancy per trade after real fees > 0, and the 80 % lower confidence bound of the mean > 0 (bootstrap).
* Realised win rate >= break-even win rate the engine displayed + 3 points on average.
* Max drawdown in the test <= 10 R (R = planned risk per trade).
* Fee estimate error < 5 %; spread estimate error < 20 %.
* No unresolved data-integrity incidents in the last 2 weeks.
If not met: the tool stays an **RR calculator / NO TRADE filter** only. That is still valuable; it simply makes no claim of edge.

**Kill switches (permanent rules)**: stop using it for the day after the daily loss limit; stop and review if 5 consecutive losses or a drawdown of 5 R; revalidate after any code/config change to gates or pricing.

## Tuning discipline
Change one parameter at a time; keep a changelog; evaluate on data not used for tuning (walk-forward: tune on weeks 1-3, test on week 4).
Never tune thresholds to make the last few losing trades disappear.
