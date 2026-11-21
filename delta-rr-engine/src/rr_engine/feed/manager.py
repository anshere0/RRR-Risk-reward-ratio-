"""FeedManager: orchestrates REST candle warm-ups, WebSocket streaming, and state updates.

Requirements:
  - Subscribe only to the active option symbol, its index (spot_index.symbol) and BTCUSD perp 5m candles.
  - Re-warm 500 closed candles via REST on startup and after reconnect.
  - Maintain data staleness tracking.
"""

from __future__ import annotations

import asyncio
import logging

from rr_engine.config import Settings, get_settings
from rr_engine.feed.rest import DeltaRestClient
from rr_engine.feed.ws import DeltaWsClient
from rr_engine.state.market_state import MarketState
from rr_engine.state.products import ProductSpec

logger = logging.getLogger("delta_feed.manager")


class FeedManager:
    """High-level feed orchestrator binding REST client, WS client, and MarketState."""

    def __init__(
        self,
        settings: Settings | None = None,
        state: MarketState | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.state = state or MarketState()

        hosts = self.settings.active_hosts
        self.rest = DeltaRestClient(base_url=hosts.rest)
        self.ws = DeltaWsClient(
            ws_url=hosts.ws,
            state=self.state,
            on_reconnect=self._on_ws_reconnect,
        )

        self._active_symbol: str | None = None
        self._index_symbol: str = ".DEXBTUSD"
        self._perp_symbol: str = "BTCUSD"
        self._running: bool = False
        self._refresh_task: asyncio.Task | None = None

    @property
    def active_symbol(self) -> str | None:
        return self._active_symbol

    @property
    def index_symbol(self) -> str:
        return self._index_symbol

    async def start(self, initial_symbol: str | None = None) -> None:
        """Start feed manager. Discovers live symbol if none provided."""
        if self._running:
            return
        self._running = True

        await self.rest.start()

        if initial_symbol:
            await self.set_active_symbol(initial_symbol)
        else:
            await self.discover_and_set_symbol()

        await self.ws.start()
        self._refresh_task = asyncio.create_task(self._periodic_refresh_loop())

    async def stop(self) -> None:
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()
        await self.ws.stop()
        await self.rest.close()

    async def discover_and_set_symbol(self, prefer_type: str = "P") -> str | None:
        """Auto-discover the nearest ATM BTC option and set as active."""
        try:
            products = await self.rest.list_products(underlying="BTC")
            if not products:
                logger.warning("No live BTC options found during discovery")
                return None

            # Fetch a sample ticker or spot price to evaluate ATM
            sample_sym = products[0]["symbol"]
            sample_ticker = await self.rest.get_ticker(sample_sym)
            spot = sample_ticker.spot_price_in_ticker or 64000.0

            # Rank by nearest settlement_time, then closest strike to spot
            for p in products:
                try:
                    p["_strike"] = float(p.get("strike_price") or 0)
                    settle_epoch = p.get("settlement_time")
                    p["_settle"] = settle_epoch or "9999"
                    p["_dist"] = abs(p["_strike"] - spot)
                    p["_is_pref"] = prefer_type.upper() in p.get("symbol", "")
                except Exception:
                    p["_dist"] = float("inf")
                    p["_is_pref"] = False

            # Sort: preferred type first, nearest expiry, then closest strike
            products.sort(key=lambda p: (not p["_is_pref"], p["_dist"]))
            chosen = products[0]["symbol"]
            await self.set_active_symbol(chosen)
            return chosen
        except Exception as e:
            logger.error("Failed to auto-discover symbol: %s", e)
            return None

    async def set_active_symbol(self, symbol: str) -> ProductSpec:
        """Switch or set the active option symbol and subscribe feeds."""
        old_symbol = self._active_symbol
        self._active_symbol = symbol
        self.state.active_symbol = symbol

        # 1. Fetch product specifications via REST
        spec = await self.rest.get_product(symbol)
        self.state.products.set(spec)

        if spec.spot_index_symbol:
            self._index_symbol = spec.spot_index_symbol

        # 2. Re-warm candles from REST
        await self._rewarm_candles()

        # 3. Snapshot initial ticker via REST
        try:
            init_ticker = await self.rest.get_ticker(symbol)
            self.state.update_option_ticker(init_ticker)
        except Exception as e:
            logger.warning("Could not fetch initial REST ticker for %s: %s", symbol, e)

        # 4. Update WebSocket subscriptions
        if old_symbol and old_symbol != symbol:
            self.ws.unsubscribe("ticker", [old_symbol])
            self.ws.unsubscribe("ob_l1", [old_symbol])

        self.ws.subscribe("ticker", [symbol])
        self.ws.subscribe("ob_l1", [symbol])
        self.ws.subscribe("spot_price", [self._index_symbol])
        self.ws.subscribe("candlestick_5m", [self._index_symbol, self._perp_symbol])

        logger.info("Active symbol set to %s (index: %s)", symbol, self._index_symbol)
        return spec

    async def _rewarm_candles(self) -> None:
        """Fetch 500 closed 5m candles for index and perp to prime indicators."""
        try:
            logger.info("Re-warming candles for %s...", self._index_symbol)
            candles = await self.rest.warm_up_candles(
                symbol=self._index_symbol,
                resolution="5m",
                count=500,
                step_seconds=300,
            )
            self.state.candles_5m.set_closed_candles(candles)
            if candles:
                self.state.clock.record("candles_5m", candles[-1].t)
                logger.info("Warmed %d closed candles for %s", len(candles), self._index_symbol)
        except Exception as e:
            logger.error("Failed to re-warm index candles: %s", e)

        try:
            perp_candles = await self.rest.warm_up_candles(
                symbol=self._perp_symbol,
                resolution="5m",
                count=500,
                step_seconds=300,
            )
            self.state.perp_candles_5m.set_closed_candles(perp_candles)
            if perp_candles:
                self.state.clock.record("perp_5m", perp_candles[-1].t)
        except Exception as e:
            logger.warning("Could not warm perp candles (%s): %s", self._perp_symbol, e)

    async def _on_ws_reconnect(self) -> None:
        """Callback invoked whenever WebSocket reconnects after a drop."""
        logger.info("WebSocket reconnected. Re-warming candles to cover gap...")
        await self._rewarm_candles()

    async def _periodic_refresh_loop(self) -> None:
        """Refresh product specs every 10 minutes and periodic health check."""
        while self._running:
            await asyncio.sleep(600.0)
            if self._active_symbol:
                try:
                    spec = await self.rest.get_product(self._active_symbol)
                    self.state.products.set(spec)
                except Exception as e:
                    logger.warning("Failed periodic product spec refresh: %s", e)
