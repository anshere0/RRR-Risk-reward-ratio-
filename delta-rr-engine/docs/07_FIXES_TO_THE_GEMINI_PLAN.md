# 07 — What was changed from the earlier plans, and why

Both earlier plans (my first answer and the Gemini output) had the right overall idea: **use Delta's data feed, not screen reading**.
These are the points that were wrong, unsupported, or too weak for a money-handling tool.

| # | Earlier claim | Problem | What this pack does |
|---|---------------|---------|---------------------|
| 1 | Index is `.DEXBTUSD` in one place and `.DEBTCUSD` / `.DE_USD` in another | Delta's docs show `.DEXBTUSD` (your screenshot too). The other names were not found in the docs | Read the index symbol from the product's `spot_index.symbol` at runtime; never hard-code |
| 2 | Taylor series (delta + gamma + theta) as the main pricing method | Accurate only for small moves; stops/targets of 0.5-2 ATR are not small; API greek units are unverified | Full Black-Scholes re-price anchored to the live mark (IV solved), Taylor kept as a cross-check only |
| 3 | "Win probability by simulation -> expected value" | With no drift, **every** stop/target has expected value ~ minus costs, so the filter either rejects everything or is fooled by model assumptions | Probability reported, but the verdict uses geometry gates until real logged evidence exists (`edge_mode`) ; banner UNVALIDATED |
| 4 | Chart RR used almost directly | Chart RR 1.82 became option RR 0.59 in the worked example | Conservative frictions: ask entry, spread stress, vol shock at stop, no vol credit at target, max decay, fees + GST |
| 5 | Fees "subtract taker fees" with no formula | The fee formula and tax matter at small sizes | Formula from product fields + `gst` setting + mandatory calibration against a real fill |
| 6 | VWAP / volume of the index | The index has no volume | VWAP and volume from the BTCUSD perpetual |
| 7 | DOM scraping as the main way to detect the symbol | Delta can change its page at any time; silent breakage is dangerous | HUD with manual symbol input first; extension only auto-fills the symbol and the HUD **always shows** the symbol it analysed, so a mismatch is visible |
| 8 | "Generate an API key (read-only)" | Not needed for public market data; every key is extra risk | No key. If ever added: never Trading permission |
| 9 | "Quant-fund precision", "maximum profit" | Not a promise anyone can keep | Honest framing: accurate maths + measured results; UNVALIDATED until proven |
| 10 | Pivots on the forming candle | Repaints -> a stop that moves under you | Confirmed pivots only, closed candles only |
| 11 | One threshold set, not configurable | Different regimes need tuning | Every threshold in `settings.yaml`, every gate unit-tested |
| 12 | No stale-data protection | A frozen feed silently gives wrong answers | DATA_STALE gate, data-age always on screen, reconnect logic, heartbeat |
| 13 | My first answer's quick example (stop ~567 / target ~747) | Back-of-envelope using delta only | Replaced by the tested worked example in `02` |

Correct in the earlier plans (kept): Python + FastAPI engine, WebSocket first, REST as backup, 5m candles, ATR + swing structure,
NO TRADE filter list, backtest -> paper trade -> live, small sizing, testnet validation, new channel names `ob_l1/ob_l2/ob_updates` after 31 Oct 2026.
