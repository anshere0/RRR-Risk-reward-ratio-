# 03 — Delta Exchange API Notes (checked against docs.delta.exchange on 18 Nov 2026)

## 1. Which Delta?
You trade from India, so the likely platform is **Delta Exchange India**. Delta Global and Delta India are separate systems
with separate hosts and **separate API keys/accounts**. The engine takes `region: india | global` from config.
**Test before building**: the probe script tries the symbol on the configured region and reports "not found" if it is the wrong one.

| | India (default) | Global |
|--|-----------------|--------|
| REST | `https://api.india.delta.exchange` | `https://api.delta.exchange` |
| Public WebSocket (market data) | `wss://public-socket.india.delta.exchange` | verify in `docs-global.delta.exchange` (older docs: `wss://socket.delta.exchange`) |
| Private WebSocket | `wss://socket.india.delta.exchange` (not needed) | not needed |
| Testnet (India demo account) | REST `https://cdn-ind.testnet.deltaex.org` | — |

Market-data endpoints need **no API key**. The engine must work with zero credentials. Do not create a key with Trading permission for this tool.

## 2. Facts verified from the official docs
* Option symbol: `C-BTC-90000-310125` / `P-BTC-64800-181126` = Type-Underlying-Strike-expiry `ddMMYY`.
* BTC spot index symbol is **`.DEXBTUSD`** (the docs call out that BTCUSD does not follow the `.DE<A><B>` pattern). Read it from `product.spot_index.symbol` at runtime.
  (The earlier Gemini plan said `.DEBTCUSD` / `.DE_USD` — **not supported by the docs; do not use**.)
* Product fields used: `symbol, id, product_id, contract_type (call_options/put_options), strike_price, contract_value ("0.001"), tick_size,
  settlement_time, state, trading_status, taker_commission_rate, maker_commission_rate, product_specs.premium_commission_rate, spot_index.symbol, underlying_asset.symbol`.
* Ticker (REST `GET /v2/tickers/{symbol}`; up to 10 comma-separated symbols; and WebSocket `ticker`): `mark_price, mark_vol, spot_price, strike_price,
  greeks{delta,gamma,rho,theta,vega}, quotes{best_bid,best_ask,bid_iv,ask_iv,bid_size,ask_size}, price_band{lower_limit,upper_limit}, oi, volume, timestamp, symbol, product_id`.
* Option chain: `GET /v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols=BTC&expiry_date=DD-MM-YYYY`.
* Live products list: `GET /v2/products?contract_types=call_options,put_options&states=live`.
* WebSocket subscribe message: `{"type":"subscribe","payload":{"channels":[{"name":"<channel>","symbols":["..."]}]}}` (verify exact nesting with the probe).
* Public channels: `ticker`, `candlesticks` (use name **`candlestick_5m`**; pass symbols — no symbols = no data), `spot_price`, `mark_price`,
  `ob_l1`, `ob_l2`, `ob_updates`, `trades`, `funding_rate`, `product_updates`, `system_status`.
  Mark-price candles: symbol prefix `MARK:` (e.g. `MARK:BTCUSD`). Index candles: pass the index symbol (`.DEXBTUSD`) — **verify with probe**.
* **Deprecations (took effect 31 Oct 2026)**: `l1_orderbook`, `l2_orderbook`, `l2_update` removed -> use `ob_l1`, `ob_l2`, `ob_updates` on the new public endpoint.
  Old WebSocket auth also removed (irrelevant: we use no auth). The older docs mention `v2/ticker`; the current docs name the channel `ticker` — probe tries `ticker`, then `v2/ticker`.
* Connection drop detection: enable server heartbeat; treat 40 s silence as dead; client ping every 30 s, expect pong within 5 s.
* REST rate limit: 20,000 weight per 5-minute window per IP (unauthenticated); tickers/orderbook/candles cost weight 3 each. HTTP 429 -> honour `X-RATE-LIMIT-RESET` (ms).
  Budget: at most 1 REST ticker call/second as a fallback while WebSocket is healthy -> 300 calls x 3 = 900 per window. Fine.
* Numbers come as **strings** (big decimals) -> parse with `Decimal`. Timestamps: docs say microseconds in many places, sample ticker shows seconds -> detect unit by magnitude
  (`>1e15` us, `>1e12` ms, else s).
* Data centre: AWS Tokyo.
* An official MCP server exists (`uvx delta-exchange-mcp`) — optional, for asking an AI about the account; not used by this engine.

## 3. Candle history (warm-up and backtests)
`GET /v2/history/candles?symbol=.DEXBTUSD&resolution=5m&start=<unix s>&end=<unix s>` (weight 3). Docs list this under "Historical OHLC Candles/Sparklines".
Verify: (a) index symbol is accepted, (b) max candles per call (page by time range), (c) whether the current forming candle is included (the engine drops it).

## 4. How the engine consumes data
| Need | Source | Notes |
|------|--------|-------|
| Index price live | `spot_price` channel (symbol `.DEXBTUSD`) and `spot_price` field in the option ticker | cross-check the two; if they differ > 0.05 % warn |
| 5m candles | REST history (warm-up 500) + `candlestick_5m` (live) | candle is closed when the next candle's start time arrives |
| 1m candles (Monte Carlo pool) | REST history, last 7 days, refreshed hourly | |
| Option quotes/greeks/mark | `ticker` for the chosen symbol | subscribe only to the active symbol(s) |
| Depth | `ob_l1` (top of book) / `ob_l2` (a few levels) | |
| Perp volume/VWAP | `candlestick_5m` for `BTCUSD` | index has no volume |
| Product specs | REST `/v2/products/{symbol}` | cache; refresh every 10 min |

## 5. Fee facts to verify (do not trust defaults)
Delta's published options fee structure (for the model): taker fee = a % of **underlying notional**, capped at a % of the **premium**; India adds **GST** on fees.
The engine reads `taker_commission_rate` and `product_specs.premium_commission_rate` from the product and uses `gst` from config.
**Calibration step**: after your first small real/testnet fills, compare the engine's fee estimate with the order's `paid_commission` (REST fills). Correct the config until they match to the cent.

## 6. MUST-VERIFY list (run `scripts/probe_delta.py`, save output, report back to the agent)
1. Which region your account is on, and that the symbol resolves there.
2. Exact WebSocket subscribe payload shape and the exact `ticker` / `candlestick_5m` message shape (field names, string vs number).
3. Whether `.DEXBTUSD` is accepted as a candle symbol on both REST and WebSocket.
4. Units of `greeks.theta`, `greeks.gamma`, and `mark_vol` (percent vs decimal; per-day vs per-year). The engine does **not** rely on them numerically until verified; it computes its own from the pricing model.
5. Option `settlement_time` (expiry clock time) for the symbol, and `tick_size`.
6. Actual fee parameters (`taker_commission_rate`, `premium_commission_rate`) and GST behaviour.
7. Whether stop/bracket orders are accepted on options and which trigger method (`mark_price`, `last_traded_price`, `spot_price` exist in the order API) — relevant for how you place the SL manually; does not affect the engine.
8. Timestamp unit in ticker messages.


## 7. Verified Facts from Live Probe (19 Nov 2026, Delta India)
* **Region & Symbol**: Delta India (`https://api.india.delta.exchange`, `wss://public-socket.india.delta.exchange`). Discovered 660 active BTC options. Probed symbol `C-BTC-61400-211126`.
* **Index Symbol**: `.DEXBTUSD` confirmed directly from `product.spot_index.symbol`.
* **Settlement Time**: ISO format UTC string (e.g. `"2026-11-21T12:00:00Z"`).
* **Tick Size & Contract Value**: `tick_size = "0.1"`, `contract_value = "0.001"` (1 lot = 0.001 BTC).
* **Fee Structure**: `taker_commission_rate = "0.0001"` (0.01% notional), `product_specs.premium_commission_rate = 0.035` (3.5% of premium cap).
* **Timestamps**:
  * Ticker REST & WS timestamps: Microseconds integer (`1795081999485555`).
  * Candle timestamps: Seconds integer (`1795081800`).
* **WebSocket Message Shapes (Verified Live)**:
  * `heartbeat`: `{"type": "heartbeat", "ts_origin": <us>, "ts_publish": <us>}`
  * `subscriptions`: `{"type": "subscriptions", "channels": [...]}`
  * `spot_price`: `{"type": "spot_price", "sy": ".DEXBTUSD", "p": 63900.1, "ts": <us>}`
  * `ob_l1`: `{"type": "ob_l1", "sy": "<symbol>", "bp": "<bid>", "bs": "<bid_size>", "ap": "<ask>", "as": "<ask_size>", "ts": <us>}`
  * `ticker`: `{"type": "ticker", "sy": "<symbol>", "sp": "<spot>", "ts": <us>, "d": [{"m": "<mark>", "q": ["<ask>", "<ask_size>", "<bid>", "<bid_size>", ...], "qiv": ["<ask_iv>", "<bid_iv>", "<mark_iv>"], "g": ["<delta>", "<gamma>", "<rho>", "<theta>", "<vega>"]}]}`
* **Candle History**: REST `GET /v2/history/candles?symbol=.DEXBTUSD&resolution=5m` returned 200 OK with OHLC candles and `volume: null`.
