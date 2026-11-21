"""Delta Exchange public WebSocket connection manager.

Handles:
  - Connection lifecycle with exponential backoff (1s -> 30s)
  - Subscription management
  - Heartbeat & connection drop detection (40s silence = dead)
  - Message parsing and dispatch to MarketState
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import websockets

from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker, TopOfBook

logger = logging.getLogger("delta_feed.ws")


def _is_ws_open(ws: Any) -> bool:
    if ws is None:
        return False
    if hasattr(ws, "state"):
        return ws.state.name == "OPEN"
    if hasattr(ws, "closed"):
        return not ws.closed
    return True


class DeltaWsClient:
    """Manages resilient WebSocket connection to Delta Exchange public market data."""

    def __init__(
        self,
        ws_url: str,
        state: MarketState,
        on_reconnect: Callable[[], Any] | None = None,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        self.ws_url = ws_url
        self.state = state
        self.on_reconnect = on_reconnect
        self.max_reconnect_delay = max_reconnect_delay

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running: bool = False
        self._loop_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._last_msg_received_time: float = 0.0

        # Subscribed channels: channel_name -> set(symbols)
        self._subscriptions: dict[str, set[str]] = {}

    @property
    def is_connected(self) -> bool:
        return _is_ws_open(self._ws) and self.state.is_ws_connected

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        self.state.is_ws_connected = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    def subscribe(self, channel: str, symbols: list[str]) -> None:
        """Register desired subscription. If already connected, immediately sends."""
        if channel not in self._subscriptions:
            self._subscriptions[channel] = set()
        self._subscriptions[channel].update(symbols)
        if self.is_connected:
            asyncio.create_task(self._send_subscribe(channel, symbols))

    def unsubscribe(self, channel: str, symbols: list[str]) -> None:
        if channel in self._subscriptions:
            self._subscriptions[channel].difference_update(symbols)

    async def _send_subscribe(self, channel: str, symbols: list[str]) -> None:
        if not _is_ws_open(self._ws):
            return
        payload = {"type": "subscribe", "payload": {"channels": [{"name": channel, "symbols": symbols}]}}
        try:
            assert self._ws is not None
            await self._ws.send(json.dumps(payload))
            logger.info("Subscribed to %s %s", channel, symbols)
        except Exception as e:
            logger.warning("Failed to send subscription: %s", e)

    async def _resubscribe_all(self) -> None:
        if not _is_ws_open(self._ws):
            return
        channels = [{"name": ch, "symbols": sorted(syms)} for ch, syms in self._subscriptions.items() if syms]
        if channels:
            payload = {"type": "subscribe", "payload": {"channels": channels}}
            assert self._ws is not None
            await self._ws.send(json.dumps(payload))
            logger.info("Resubscribed all: %s", channels)

    async def _run_loop(self) -> None:
        delay = 1.0
        while self._running:
            try:
                logger.info("Connecting to Delta WebSocket: %s", self.ws_url)
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,  # We manage application-level ping/heartbeat
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self.state.is_ws_connected = True
                    self._last_msg_received_time = time.time()
                    delay = 1.0  # Reset backoff on successful connect

                    # Enable heartbeat
                    await ws.send(json.dumps({"type": "enable_heartbeat"}))
                    # Resubscribe all registered channels
                    await self._resubscribe_all()

                    # Trigger re-warm callback if registered
                    if self.on_reconnect:
                        try:
                            res = self.on_reconnect()
                            if asyncio.iscoroutine(res):
                                asyncio.create_task(res)
                        except Exception as e:
                            logger.error("Error in on_reconnect handler: %s", e)

                    # Start watchdog / ping loop
                    self._heartbeat_task = asyncio.create_task(self._watchdog_loop())

                    # Process incoming messages
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except TimeoutError:
                            # Check silence threshold
                            if time.time() - self._last_msg_received_time > 40.0:
                                logger.warning("WebSocket silence > 40s, reconnecting...")
                                break
                            continue

                        self._last_msg_received_time = time.time()
                        self._handle_message(raw)

            except (websockets.ConnectionClosed, OSError, asyncio.CancelledError) as e:
                if not self._running:
                    break
                logger.warning("WebSocket connection dropped (%s). Reconnecting in %.1fs...", e, delay)

            finally:
                self.state.is_ws_connected = False
                self.state.last_disconnect_time = time.time()
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                self._ws = None

            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self.max_reconnect_delay)

    async def _watchdog_loop(self) -> None:
        """Sends ping every 30s to keep connection alive."""
        while self._running and _is_ws_open(self._ws):
            await asyncio.sleep(30.0)
            try:
                if _is_ws_open(self._ws):
                    assert self._ws is not None
                    pong_waiter = self._ws.ping()
                    if asyncio.iscoroutine(pong_waiter) or hasattr(pong_waiter, "__await__"):
                        await asyncio.wait_for(pong_waiter, timeout=5.0)
            except Exception as e:
                logger.warning("WebSocket ping timed out or failed: %s", e)
                if self._ws:
                    with contextlib.suppress(Exception):
                        await self._ws.close()
                break

    def _handle_message(self, raw_str: str | bytes) -> None:
        """Parse incoming JSON message and dispatch to market state."""
        try:
            msg = json.loads(raw_str)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type") or msg.get("channel")

        if msg_type == "heartbeat":
            self.state.clock.record("heartbeat", msg.get("ts_origin") or msg.get("ts_publish"))
            return

        if msg_type == "spot_price":
            # {"type": "spot_price", "sy": ".DEXBTUSD", "p": 63900.1, "ts": ...}
            price = float(msg.get("p") or 0.0)
            ts = msg.get("ts")
            self.state.update_spot_price(price, ts)
            return

        if msg_type == "ob_l1":
            # Top of book
            ob = TopOfBook.from_ws(msg)
            self.state.update_orderbook(ob)
            return

        if msg_type == "ticker":
            ticker = OptionTicker.from_ws(msg)
            self.state.update_option_ticker(ticker)
            return

        if msg_type == "candlestick_5m":
            # Candle update
            c_data = msg.get("candle") or msg
            if isinstance(c_data, dict) and "close" in c_data or "c" in c_data:
                candle = Candle.from_api(c_data)
                symbol = msg.get("symbol") or msg.get("sy") or ""
                if "BTCUSD" in symbol and "MARK" not in symbol:
                    self.state.perp_candles_5m.on_candle_update(candle)
                else:
                    self.state.candles_5m.on_candle_update(candle)
            return

        if msg_type == "subscriptions":
            logger.debug("Active subscriptions acknowledged: %s", msg)
            return
