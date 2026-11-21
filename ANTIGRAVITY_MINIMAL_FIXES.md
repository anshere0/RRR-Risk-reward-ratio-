# Minimal fixes — paste into Antigravity

Keep the current design. DO NOT change: retail mode (`strict_mode=false`), the 1.15 ATR stop clamp, pivot-based targets, `clean_rr` as the hero number,
DOM price reading, the lots/budget sync, the layout, or any config values. Only apply the 7 small fixes below. After each, add the named test and show the test output.

**M1. Forming candle must not enter the closed list (bug).**
In `state/candles.py` `on_candle_update`: if `candle.t` is the current 5-minute bucket (`candle.t >= current_bucket_start`), store it in `_forming` and return False. Only close a candle when a candle with a newer start time arrives. Use only ONE writer for the indicator series (the `candlestick_5m` stream).
Test: a forming-candle update leaves `len(closed)` unchanged; a pivot confirmed earlier never changes when new candles arrive.

**M2. Find why `sigma_5m` is about 9e-7 in live data (bug).**
Add `GET /api/debug/candles` returning: closed count, last 10 closed candles, `sigma_5m`, ATR, ADX, and counts of candles with `c == 0`, repeated closes, duplicate/missing timestamps. Do not change any thresholds. Report the output to me.
Test: endpoint returns the fields; a zero-close series is reported.

**M3. Entry-price sanity check (keep DOM reading).**
In `rr_engine.py`, if `entry_override_option` is outside `[bid * 0.97, ask * 1.03]` ignore the override, use the engine's ask, and add a warning reason `ENTRY_OVERRIDE_IGNORED` ("page price does not match this option's quote"). In `content.js` show the same warning in the Market Notes list.
Test: override 62765 on a 765 option is ignored with the warning.

**M4. Never hide an over-budget lot.**
Keep `max(1, floor(...))`. But when `risk_per_lot > max_risk_usd`, set `max_lots_for_risk_budget = 1` AND add a warning reason `ONE_LOT_ABOVE_BUDGET` with the real numbers; in `content.js` show the amber text "1 lot risks $X, above your $Y budget" next to the lots box.
Test: budget 3, risk per lot 3.8 produces the warning.

**M5. Make failed checks visible in the extension (no behaviour change).**
In `renderAnalytics`: (a) show a small badge near the hero: "N checks failed" in red if any reason has a message starting `[Advisory]` or severity `hard`, otherwise "All checks passed" in green; (b) show `net_rr` next to the hero in a slightly larger font than now (keep `clean_rr` as the hero); (c) show `validation_status` ("UNVALIDATED") and `provenance.data_age_seconds` (red above 3 s) in the footer.
Test: with the sample response the badge reads "5 checks failed".

**M6. Silent defaults must not hide bad data.**
In `rr_engine.py` replace the fallbacks (`atr = 100.0`, `adx = 15.0`, `sigma_5m = 0.003`, missing delta counted as 0 difference) with a hard `INDICATOR_UNAVAILABLE` reason that returns NO_TRADE (these only trigger when data is missing, not in normal use).
Test: missing indicators return NO_TRADE.

**M7. Lock the local bridge (invisible to normal use).**
In `background.js` only accept `url.startsWith("http://127.0.0.1:8000/")` and `sender.id === chrome.runtime.id`. In `api/main.py` remove `allow_credentials=True` and add `TrustedHostMiddleware(["127.0.0.1", "localhost"])`.
Test: a non-local URL is rejected by the background proxy.

## Done when
All tests pass (show output), nothing in the "DO NOT change" list was modified, and the debug endpoint output (M2) is pasted back to me.
