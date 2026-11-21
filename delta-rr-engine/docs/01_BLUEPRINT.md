# 01 — Blueprint

## 1. Goals and non-goals
**Goals**
* G1. Given an option symbol chosen by the user, output entry / stop / target **in option premium**, the **net risk-reward**,
  and TRADE / NO_TRADE, using live data, in under 200 ms after the user presses Evaluate.
* G2. Every number is reproducible: same inputs -> same outputs; every evaluation is logged with all inputs.
* G3. Fail safe: any missing/stale/odd data -> NO_TRADE with a reason. Never output a number it cannot defend.
* G4. Measure itself: log suggestions vs outcomes to calibrate and to prove (or disprove) usefulness.

**Non-goals (v1)**
* No order placement, no auto-trading, no API keys with Trading permission. Market data is public (no key needed).
* No option selling / spreads (undefined risk). v1 supports **long call / long put only**.
* No screen scraping of prices. (Optional DOM read of the symbol only, Phase 2.)
* No prediction of direction. The user chooses call/put. The bot judges whether that choice has acceptable geometry and context.

## 2. Architecture
```
 Delta public WebSocket (ticker, candlestick_5m, ob_l1/ob_l2, spot_price)     Delta REST (products, tickers, history candles)
            |                                                                         |
            +-------------------------> feed/ (connection manager, reconnect, heartbeat, staleness clock) <---+
                                                   |
                                          state/ (in-memory market state: candles, quotes, product specs)
                                                   |
        +------------------------------------------+----------------------------------------+
        |                      |                           |                                |
   indicators/            structure/                  pricing/ (BS, IV solve)          costs/ (spread, fees)
   ATR EMA ADX VWAP       swings, S/R, targets        reprice at scenarios             taker fee model
        |                      |                           |                                |
        +----------------------+-------------+-------------+--------------------------------+
                                             |
                                   engine/ (rr_engine.py: orchestrates, gates, verdict)
                                             |
                       montecarlo/ (probability, optional) ---- journal/ (SQLite log of every evaluation)
                                             |
                            api/ (FastAPI: POST /api/evaluate, GET /api/status, WS /ws/hud)
                                             |
                    hud/ (static web page, always-on-top small window)  <--- Phase 2: chrome extension overlay
```

### Why this shape
* **Data feed, not pixels**: exact, millisecond-fresh, includes Greeks/IV/quotes.
* **Local engine**: no cloud latency, no secrets leave the machine. Delta hosts are in AWS Tokyo; run on a stable connection.
* **Engine is pure functions + a thin I/O shell**: every maths function is unit-testable with fixed inputs (see `reference/`).
* **HUD first, extension second**: a plain web page in a small always-on-top window works on day 1 and cannot break when Delta
  changes its page layout. The extension is an optional convenience (auto-detect symbol, overlay on the tab).

## 3. Technology stack (decided — do not substitute without asking)
| Layer | Choice | Reason |
|-------|--------|--------|
| Language | Python 3.11+ | Numerics, tests, async |
| Web/API | FastAPI + uvicorn | Simple, typed, WebSocket support |
| Feed | `websockets` (async) + `httpx` | Full control of reconnect/heartbeat |
| Maths | stdlib `math` for pricing; `numpy` for Monte Carlo | Pricing stays dependency-free and testable |
| Models | `pydantic` v2 | Validated inputs/outputs |
| Money/prices | `decimal.Decimal` at the edges (API strings), `float` inside maths, round to tick on output | Delta sends decimals as strings |
| Storage | SQLite (`journal.db`) | Zero setup, queryable |
| Tests | pytest (+ `ta` cross-check) | Golden vectors from `reference/` |
| Lint/format | ruff | Consistency |
| HUD | One static HTML+JS page served by FastAPI | No build step |
| Extension (Phase 2) | Manifest V3, vanilla JS | See `extension_spec/` |

## 4. Target repository layout
```
delta-rr-engine/
  AGENTS.md  .agent/  README.md  pyproject.toml  .env.example  config/settings.yaml
  src/rr_engine/
    config.py            # loads settings.yaml + env, validates
    feed/ rest.py ws.py manager.py clock.py
    state/ market_state.py candles.py products.py
    indicators/ atr.py ema.py adx.py vwap.py realized_vol.py
    structure/ pivots.py levels.py stop_target.py
    pricing/ black_scholes.py implied_vol.py greeks.py
    costs/ fees.py spread.py
    engine/ rr_engine.py gates.py verdict.py models.py
    montecarlo/ simulate.py
    journal/ db.py schema.sql
    api/ main.py routes.py
    hud/ index.html hud.js hud.css
  tests/ unit/ golden/ replay/ contract/
  reference/            # copied from this pack; never edited, only used as the oracle in tests
  scripts/ probe_delta.py replay_candles.py backtest_spot.py calibrate.py
```

## 5. Build phases (each ends with a runnable demo + passing tests)
| Phase | Deliverable | Done when |
|-------|-------------|-----------|
| 0 | Probe + reference tests green | `probe_delta.py` output saved to `docs/probe_output.json`; `pytest reference` green |
| 1 | Feed layer + market state | Live index price, option bid/ask/mark/greeks printed with data-age; survives a forced disconnect |
| 2 | Indicators + structure | On recorded candles, matches `ta` library; pivots never repaint |
| 3 | Pricing + costs + RR core | Matches `golden_vectors.json`; property tests pass |
| 4 | Gates + verdict + API | All gates in `04_NO_TRADE_RULES.md` unit-tested; `/api/evaluate` < 200 ms |
| 5 | HUD | Always-on-top window shows verdict, prices, reasons, data age, UNVALIDATED banner |
| 6 | Journal + replay + calibration | Every evaluation stored; replay tool reproduces any past result bit-for-bit |
| 7 | Monte Carlo probability | Matches analytic gambler's-ruin within MC error on driftless test |
| 8 | Chrome extension (optional) | Per `extension_spec/` |
| 9 | Paper trading period | `05_VALIDATION_AND_GO_LIVE.md` criteria |

## 6. User flow
1. Start: `python -m rr_engine` (opens API on 127.0.0.1:8000 and the feed). HUD at `http://127.0.0.1:8000/hud`.
2. In the HUD the user types/pastes the option symbol (or Phase 2 auto-fills it), sets **lots** and optional **max risk $**,
   and presses **Evaluate** (keyboard: `E`).
3. HUD shows the verdict card and refreshes it every second until dismissed, always showing "computed X s ago".
4. User places the trade manually on Delta. User may press "I took it" so the journal links the real fill later (optional).

## 7. Performance and reliability budgets
* Evaluate: p95 < 200 ms (all inputs already in memory). Monte Carlo runs async and must never block the verdict
  (verdict returns first; probability fields fill in <1 s).
* Feed: auto-reconnect with backoff 1s,2s,4s... max 30s; re-subscribe and re-warm candles after reconnect; the engine marks
  data STALE during the gap.
* Clock: use server timestamps from messages; warn if local clock differs from Delta time by > 1 s.
* No unbounded memory: ring buffers (2,000 x 5m candles, 10,000 x 1m).
