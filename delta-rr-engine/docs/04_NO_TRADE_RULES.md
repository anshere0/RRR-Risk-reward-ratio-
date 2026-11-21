# 04 — NO TRADE Rules

Verdict = `NO_TRADE` if **any hard gate** fires. Warnings never change the verdict but are always shown.
Every gate has: code, plain-English message, default threshold (all in `config/settings.yaml`), and a unit test (mandatory).
Gate order matters: data-integrity gates run first so the user never sees a number built on bad data.

## A. Data integrity (hard)
| Code | Fires when | Default |
|------|-----------|---------|
| `FEED_DISCONNECTED` | WebSocket not connected | — |
| `DATA_STALE` | Age of last option ticker or index price > limit | 3 s |
| `CANDLE_GAP` | Missing 5m candles in the lookback window | any gap in last 60 candles |
| `PRODUCT_NOT_TRADABLE` | product `state != live` or `trading_status != operational` | — |
| `SYMBOL_INVALID` | Symbol not parseable / not found / not a BTC option | — |
| `QUOTE_INVALID` | bid missing/zero, ask < bid, or crossed book | — |
| `INDEX_PERP_DIVERGENCE` | |index - perp mark| / index > limit (feed glitch or stress) | 0.25 % |
| `MODEL_CALIBRATION_FAILED` | IV solve fails (price below intrinsic etc.) | — |
| `GREEKS_MISMATCH_SEVERE` | |model delta - API delta| > limit | 0.15 |
| `CLOCK_SKEW` | local clock vs exchange timestamps | > 2 s |

## B. Option quality (hard)
| Code | Fires when | Default |
|------|-----------|---------|
| `EXPIRY_TOO_CLOSE` | time to expiry < limit (decay/gamma explode) | 60 min (and holding window must end >= 10 min before expiry) |
| `SPREAD_TOO_WIDE` | (ask - bid)/mid > limit | 1.5 % |
| `SPREAD_EATS_REWARD` | round-trip spread cost > share of gross reward | 20 % |
| `DEPTH_TOO_THIN` | top-of-book ask size < 1.5 x lots (entry), bid size < 1.0 x lots (exit) | — |
| `PREMIUM_TOO_LOW` | premium below minimum where tick size/fees dominate | 20 (premium units) |
| `FEES_EAT_REWARD` | total fees > share of gross reward | 30 % |

## C. Market context (hard)
| Code | Fires when | Default |
|------|-----------|---------|
| `CHOPPY_MARKET` | ADX(14) < limit AND Bollinger bandwidth in lowest quartile of last 200 candles | ADX 18 |
| `AGAINST_TREND` | Direction opposes EMA20/EMA50 alignment while ADX > limit | ADX 25 |
| `SPIKE_JUST_HAPPENED` | Last closed candle range > m x ATR, or live candle range > 2 x ATR | 2.5 x ATR; wait 2 candles |
| `VOL_REGIME_EXTREME` | sigma_5m (EWMA) > 3x its 7-day median | 3x |
| `EVENT_WINDOW` | within the user-maintained list of scheduled-event windows (CPI, FOMC...) | optional list in config |

## D. Trade geometry (hard)
| Code | Fires when | Default |
|------|-----------|---------|
| `STOP_TOO_WIDE` | stop distance > limit x ATR | 2.0 |
| `TARGET_BLOCKED` | opposing level closer than limit x ATR | 0.75 |
| `TARGET_UNREACHABLE_IN_WINDOW` | target distance > 1.5 x sigma_5m x sqrt(N candles) | 1.5 |
| `NET_RR_TOO_LOW` | net RR (after all frictions) < limit | 1.5 |
| `BREAKEVEN_TOO_HIGH` | break-even win rate > limit | 45 % |
| `RISK_OVER_BUDGET` | planned risk (lots as entered) > user's max risk — suggests `max_lots` instead | user value |
| `STRIKE_NOT_ATM` | (warning only) |delta| outside 0.35-0.65 | — |

## E. Evidence (hard, only when `edge_mode: empirical`)
| Code | Fires when |
|------|-----------|
| `PROBABILITY_BELOW_BREAKEVEN` | Wilson-80% lower bound of historical hit-rate for this setup bucket <= break-even + margin (3 pts) |
| `INSUFFICIENT_HISTORY` | fewer than 150 historical samples in the bucket |

## F. Session discipline (hard, optional, uses the journal)
| Code | Fires when | Default |
|------|-----------|---------|
| `DAILY_LOSS_LIMIT` | logged losses today (IST) exceed limit | user value |
| `COOLDOWN` | N consecutive logged losses | 3 losses -> 30 min |

## Verdict object
```
verdict: "TRADE" | "NO_TRADE"
reasons: [{code, severity: "hard"|"warning", message, value, threshold}]
validation_status: "UNVALIDATED" | "VALIDATED"   # UNVALIDATED until Phase 9 criteria are met
```
If TRADE: `reasons` still lists warnings. If NO_TRADE: list **all** failing hard gates, not just the first, ordered by section A->F.
Plain wording example: "NO TRADE — after fees and spread, you would risk $1.58 to make $0.94 (0.59 : 1). Needed at least 1.5 : 1."
