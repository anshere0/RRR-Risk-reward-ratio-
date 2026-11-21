# Fix prompt — paste into Antigravity (Planning mode)

Read AGENTS.md first. This is a money-related tool: fail closed, never show a number built on bad data, show test output before saying done.
Work through the fixes **in order**. After each one: add the test named below, run the full test suite, show me the output, then continue.
Do not change `reference/`. Do not add order placement of any kind.

## P0 — wrong or dangerous output today

**F1. Make NO TRADE real (defaults + verdict).**
- `EvaluateRequest.strict_mode` default -> `True`. Remove `strict_mode` from `hud.js` and `extension/content.js` payloads (clients must not weaken the engine).
- Delete the "advisory downgrade" branch in `verdict.aggregate_verdict` for sections B-F. Hard gates stay hard in every mode. Keep `strict_mode` only as a debug flag that is never exposed in the UI.
- Add a third verdict `CAUTION` = no hard gate fired but soft warnings exist; `TRADE` = nothing fired. Update `RREvaluateResponse`, the JSON schema and tests.
- Test: re-run `sample_evaluate_response.json` inputs -> must be `NO_TRADE` (NET_RR_TOO_LOW, BREAKEVEN_TOO_HIGH, CHOPPY_MARKET, TARGET_UNREACHABLE, FEES_EAT_REWARD all hard).

**F2. Always conservative pricing.**
- Remove `apply_decay` from the request model, `rr_core.compute_rr`, HUD and extension. Decay to end of window, `iv_shock_sl`, `iv_shock_tp`, and `sl_spread_stress` are ALWAYS applied.
- `rr_1lot_params` in `rr_engine.py` must pass the same `r`, `sl_spread_stress`, `iv_shock_*` from config (currently they fall back to dataclass defaults).
- Test: golden vectors still match; changing a config shock changes both the main and 1-lot results identically.

**F3. Extension must show the verdict, not a flattering number.**
- Hero = `verdict` (big, colour coded: TRADE green, CAUTION amber, NO_TRADE red) + `net_rr`. `clean_rr` is shown small and labelled "before costs and time decay".
- Render `severity=="hard"` reasons in red and soft ones in amber (currently every reason uses the same `warn` class).
- Always show: `validation_status` badge (non-dismissible UNVALIDATED), `data_age_seconds` (red above 3 s), the symbol and index price the engine analysed, and `computed_at`.
- If `verdict == NO_TRADE`, hide entry/stop/target numbers behind a "show anyway" link (default hidden).

**F4. Closed candles only (bug confirmed by test).**
- `CandleSeries.on_candle_update` currently appends the still-forming WebSocket candle to the closed list. Fix: only the candle whose start time is older than the current 5-minute bucket may be closed; keep the newest candle in `_forming`. Pick ONE writer (the `candlestick_5m` stream) and remove the second writer (`on_tick` from the index price) for the series used by indicators.
- Test: feed a forming candle update -> `len(closed)` unchanged and `forming` updated; feed the next bucket -> exactly one candle closes with the final OHLC; pivots confirmed earlier never change when new candles arrive.

**F5. Find out why volatility is ~0 in live data.**
- The sample response implies `sigma_5m ~ 9e-7` (target reach limit 0.2 points). On normal data the same function returns ~0.0022. Add a debug endpoint `GET /api/debug/candles` returning: count, last 10 closed candles, `sigma_5m`, ATR, ADX, any candle with `c == 0`, repeated closes, duplicate or missing timestamps.
- Replace silent defaults (`atr=100.0`, `adx=15.0`, `sigma_5m=0.003`, missing delta -> diff 0) with `NO_TRADE` reasons `INDICATOR_UNAVAILABLE` / `GREEKS_UNAVAILABLE`.
- Add a data-sanity gate: `sigma_5m` outside [1e-4, 2e-2] or ATR/price outside [0.02 %, 1 %] -> `DATA_SANITY_FAILED` (hard).
- Test: flat-close series and zero-close series must produce that gate, never a number.

**F6. Stop and target must come from structure, not from the ratio.**
- Remove the 1.15-ATR stop clamp. If the structural stop is wider than `stop_max_atr`, return `STOP_TOO_WIDE` (hard). Optionally add a second labelled setup "tactical stop" with its own probability and RR, clearly marked "not structural invalidation".
- Remove the retail-mode target replacement block. Target = nearest opposing level (reference `select_target`). A wall closer than `target_min_atr` -> `TARGET_BLOCKED` (hard); never jump over it.
- Standard pivots: anchor to the previous UTC day (00:00-24:00 UTC H/L/C), not the rolling last 288 candles, and never use R3/S3 as targets.
- Test: reproduce the 63000-put scenario; the engine must return STOP_TOO_WIDE or TARGET_BLOCKED instead of a stretched 1:2.65.

**F7. Remove DOM price scraping from the money path.**
- Delete `readDeltaPagePrices()` use in `syncLivePriceAndEvaluate`. Entry comes from the engine's own ask. A manual entry override is allowed only if it lies within [bid - 2 ticks, ask + 2 ticks] (validate in the engine, return `ENTRY_OVERRIDE_INVALID` otherwise) and the UI shows "entry source: manual".
- Optional cross-check only: if a scraped price differs from the engine mark by more than 3 % show an amber "possible symbol mismatch" banner; never use it in calculations.

**F8. Lots versus budget.**
- Engine and UI: `max_lots = floor(budget / risk_per_lot)`, which may be 0. If 0 -> `RISK_OVER_BUDGET` (hard) with message "one lot risks $X, above your budget $Y". Remove every `Math.max(1, ...)` / `max(1, ...)` on this path (`rr_engine.py`, `content.js` lines near 773-795).
- Recompute risk at the final lot count (fees are not exactly linear).
- Test: budget 3, risk per lot 3.8 -> 0 lots + gate fired.

## P1 — hardening

**F9. Local server security.** Remove delta.exchange origins and `allow_credentials` from CORS (the extension calls through its service worker). Add `TrustedHostMiddleware` (127.0.0.1, localhost). In `background.js` allow only URLs that start with `http://127.0.0.1:8000/`, check `sender.id === chrome.runtime.id`, and reject anything else. Add an optional per-session token header.

**F10. Journal size and meaning.** Do not store 2 x 2,000 candles per evaluation: store a hash plus the last 100 candles, and dedupe full snapshots by hash. Journal only on a button press (not on auto refresh). Add "I took it" and "closed at price" buttons in the extension that call the paper-trade endpoints. Daily loss = net P&L for the IST day (not only the sum of losing trades).

**F11. Honest holding time.** Remove the invented `est_bars = dist/atr*1.8` string. Show expected resolution time = (stop distance x target distance) / (sigma per candle in points)^2 candles, and warn if it exceeds `hold_minutes`.

**F12. Probability and expected value.** Run Monte Carlo on every evaluate (non-blocking, never delays the verdict) and show P(target first), P(stop first), break-even win rate and expected value per trade in R. Fix provenance `index_symbol` (shows "BTC", should be `.DEXBTUSD`) and display infinite RR as "no risk computed", not 0.0.

## Definition of done
All tests green (show output); `sample_evaluate_response.json` scenario returns NO_TRADE; a forming candle never appears in closed candles; no code path can produce a number from DOM-scraped prices; extension shows verdict, data age and UNVALIDATED badge; a 5-line plain-English summary of what changed.
