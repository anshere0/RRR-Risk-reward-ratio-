"""In-memory market state: quotes, candles, product specifications, and stream clocks.

Thread-safe and async-safe container accessed by the risk-reward engine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from rr_engine.feed.clock import StalenessClock, normalize_timestamp_to_seconds
from rr_engine.state.candles import CandleSeries
from rr_engine.state.products import ProductCache


@dataclass
class OptionTicker:
    symbol: str
    mark_price: float
    best_bid: float
    best_ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    mark_iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    spot_price_in_ticker: float | None = None
    timestamp: float = 0.0

    @property
    def spread(self) -> float:
        return max(0.0, self.best_ask - self.best_bid)

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0 if (self.best_bid > 0 and self.best_ask > 0) else self.mark_price

    @property
    def spread_pct(self) -> float:
        mid = self.mid_price
        return (self.spread / mid * 100.0) if mid > 0 else float("inf")

    @property
    def is_crossed(self) -> bool:
        return self.best_ask < self.best_bid

    @classmethod
    def from_rest(cls, raw: dict[str, Any]) -> OptionTicker:
        """Parse REST ticker result."""
        quotes = raw.get("quotes") or {}
        greeks = raw.get("greeks") or {}

        def _flt(val: Any, default: float = 0.0) -> float:
            if val is None or val == "":
                return default
            return float(Decimal(str(val)))

        def _opt_flt(val: Any) -> float | None:
            if val is None or val == "":
                return None
            return float(Decimal(str(val)))

        ts = normalize_timestamp_to_seconds(raw.get("timestamp"))

        return cls(
            symbol=str(raw.get("symbol") or ""),
            mark_price=_flt(raw.get("mark_price")),
            best_bid=_flt(quotes.get("best_bid")),
            best_ask=_flt(quotes.get("best_ask")),
            bid_size=_flt(quotes.get("bid_size")),
            ask_size=_flt(quotes.get("ask_size")),
            mark_iv=_opt_flt(quotes.get("mark_iv") or raw.get("mark_vol")),
            delta=_opt_flt(greeks.get("delta")),
            gamma=_opt_flt(greeks.get("gamma")),
            theta=_opt_flt(greeks.get("theta")),
            vega=_opt_flt(greeks.get("vega")),
            spot_price_in_ticker=_opt_flt(raw.get("spot_price")),
            timestamp=ts,
        )

    @classmethod
    def from_ws(cls, raw: dict[str, Any]) -> OptionTicker:
        """Parse WebSocket compact ticker.

        Format verified by probe:
          {"type": "ticker", "sy": "<sym>", "sp": "<spot>", "ts": <us>,
           "d": [{"m": "<mark>", "q": ["<ask>", "<ask_size>", "<bid>", "<bid_size>", ...],
                  "qiv": ["<ask_iv>", "<bid_iv>", "<mark_iv>"],
                  "g": ["<delta>", "<gamma>", "<rho>", "<theta>", "<vega>"]}]}
        """
        symbol = str(raw.get("sy") or "")
        ts = normalize_timestamp_to_seconds(raw.get("ts"))
        sp = float(Decimal(str(raw.get("sp")))) if raw.get("sp") else None

        d_list = raw.get("d") or [{}]
        d0 = d_list[0] if d_list else {}

        def _flt(val: Any, default: float = 0.0) -> float:
            if val is None or val == "":
                return default
            return float(Decimal(str(val)))

        def _opt_flt(val: Any) -> float | None:
            if val is None or val == "":
                return None
            return float(Decimal(str(val)))

        mark = _flt(d0.get("m"))
        q = d0.get("q") or []
        # q: [ask_price, ask_size, bid_price, bid_size, impact_mid]
        ask = _flt(q[0]) if len(q) > 0 else 0.0
        ask_sz = _flt(q[1]) if len(q) > 1 else 0.0
        bid = _flt(q[2]) if len(q) > 2 else 0.0
        bid_sz = _flt(q[3]) if len(q) > 3 else 0.0

        qiv = d0.get("qiv") or []
        # qiv: [ask_iv, bid_iv, mark_iv]
        mark_iv = _opt_flt(qiv[2]) if len(qiv) > 2 else None

        g = d0.get("g") or []
        # g: [delta, gamma, rho, theta, vega]
        delta = _opt_flt(g[0]) if len(g) > 0 else None
        gamma = _opt_flt(g[1]) if len(g) > 1 else None
        theta = _opt_flt(g[3]) if len(g) > 3 else None
        vega = _opt_flt(g[4]) if len(g) > 4 else None

        return cls(
            symbol=symbol,
            mark_price=mark,
            best_bid=bid,
            best_ask=ask,
            bid_size=bid_sz,
            ask_size=ask_sz,
            mark_iv=mark_iv,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            spot_price_in_ticker=sp,
            timestamp=ts,
        )


@dataclass
class TopOfBook:
    symbol: str
    best_bid: float
    bid_size: float
    best_ask: float
    ask_size: float
    timestamp: float

    @classmethod
    def from_ws(cls, raw: dict[str, Any]) -> TopOfBook:
        """Parse ob_l1 message: {"bp": "...", "bs": "...", "ap": "...", "as": "...", "sy": "...", "ts": ...}"""
        return cls(
            symbol=str(raw.get("sy") or ""),
            best_bid=float(Decimal(str(raw.get("bp") or 0))),
            bid_size=float(Decimal(str(raw.get("bs") or 0))),
            best_ask=float(Decimal(str(raw.get("ap") or 0))),
            ask_size=float(Decimal(str(raw.get("as") or 0))),
            timestamp=normalize_timestamp_to_seconds(raw.get("ts")),
        )


class MarketState:
    """Consolidated market state updated continuously by the feed layer."""

    def __init__(self) -> None:
        self.spot_index_price: float | None = None
        self.perp_mark_price: float | None = None
        self.option_tickers: dict[str, OptionTicker] = {}
        self.orderbooks: dict[str, TopOfBook] = {}
        self.candles_5m = CandleSeries(step_seconds=300, maxlen=2000)
        self.perp_candles_5m = CandleSeries(step_seconds=300, maxlen=2000)
        self.clock = StalenessClock()
        self.products = ProductCache()
        self.is_ws_connected: bool = False
        self.active_symbol: str | None = None
        self.last_disconnect_time: float | None = None

    def update_spot_price(self, price: float, exchange_ts: float | int | str | None) -> None:
        self.spot_index_price = price
        now = time.time()
        self.clock.record("index", exchange_ts, now)
        # Single writer policy (F4): candles_5m is written ONLY by candlestick_5m stream,
        # never by tick updates to avoid forming candle pollution.

    def update_perp_price(
        self, price: float, exchange_ts: float | int | str | None, volume: float | None = None
    ) -> None:
        self.perp_mark_price = price
        now = time.time()
        self.clock.record("perp_5m", exchange_ts, now)
        self.perp_candles_5m.on_tick(price, normalize_timestamp_to_seconds(exchange_ts) or now, volume)

    def update_option_ticker(self, ticker: OptionTicker) -> None:
        self.option_tickers[ticker.symbol] = ticker
        self.clock.record("option_ticker", ticker.timestamp)
        # If ticker carried spot_price and index was empty, update it
        if ticker.spot_price_in_ticker and self.spot_index_price is None:
            self.spot_index_price = ticker.spot_price_in_ticker

    def update_orderbook(self, ob: TopOfBook) -> None:
        self.orderbooks[ob.symbol] = ob
        self.clock.record("ob_l1", ob.timestamp)
        # Also synchronize top-of-book bid/ask with existing ticker if present
        tk = self.option_tickers.get(ob.symbol)
        if tk:
            tk.best_bid = ob.best_bid
            tk.best_ask = ob.best_ask
            tk.bid_size = ob.bid_size
            tk.ask_size = ob.ask_size

    def get_ticker(self, symbol: str) -> OptionTicker | None:
        return self.option_tickers.get(symbol)

    def get_orderbook(self, symbol: str) -> TopOfBook | None:
        return self.orderbooks.get(symbol)

    def check_index_perp_divergence(self, limit_pct: float = 0.25) -> tuple[bool, float]:
        """Check |index - perp| / index > limit_pct (percent). Returns (is_divergent, diff_pct)."""
        if self.spot_index_price is None or self.perp_mark_price is None or self.spot_index_price <= 0:
            return False, 0.0
        diff_pct = abs(self.spot_index_price - self.perp_mark_price) / self.spot_index_price * 100.0
        return (diff_pct > limit_pct), diff_pct
