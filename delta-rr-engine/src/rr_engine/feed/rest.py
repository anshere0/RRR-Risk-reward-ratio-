"""Delta Exchange REST API client (async).

Handles:
  - Product specification retrieval & discovery
  - Ticker snapshots
  - Candle history warm-up (500 closed candles)
  - Rate limits & error handling
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from rr_engine.state.candles import Candle
from rr_engine.state.market_state import OptionTicker
from rr_engine.state.products import ProductSpec


class DeltaRestError(Exception):
    """Base exception for Delta REST API failures."""


class DeltaRateLimitError(DeltaRestError):
    """Raised on HTTP 429."""

    def __init__(self, reset_after_ms: int = 1000) -> None:
        super().__init__(f"Rate limit exceeded (HTTP 429), reset in {reset_after_ms}ms")
        self.reset_after_ms = reset_after_ms


class DeltaRestClient:
    """Async REST client for Delta Exchange public market data."""

    def __init__(self, base_url: str = "https://api.india.delta.exchange", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> DeltaRestClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None, retries: int = 3) -> dict[str, Any]:
        await self.start()
        assert self._client is not None

        for attempt in range(retries):
            try:
                resp = await self._client.get(path, params=params)
                if resp.status_code == 429:
                    reset_ms = int(resp.headers.get("X-RATE-LIMIT-RESET", "1000"))
                    if attempt < retries - 1:
                        await asyncio.sleep(max(reset_ms / 1000.0, 0.5))
                        continue
                    raise DeltaRateLimitError(reset_ms)

                if resp.status_code == 404:
                    raise DeltaRestError(f"Resource not found: {path} (HTTP 404)")

                resp.raise_for_status()
                data = resp.json()
                if not data.get("success", True) and "result" not in data:
                    raise DeltaRestError(f"API returned error: {data.get('error')}")
                return data

            except httpx.RequestError as e:
                if attempt == retries - 1:
                    raise DeltaRestError(f"Network error querying {path}: {e}") from e
                await asyncio.sleep(0.5 * (2**attempt))

        raise DeltaRestError(f"Failed to query {path} after {retries} retries")

    async def get_product(self, symbol: str) -> ProductSpec:
        """Fetch specification for a given product symbol."""
        data = await self._get(f"/v2/products/{symbol}")
        result = data.get("result")
        if not result:
            raise DeltaRestError(f"Empty product result for symbol {symbol}")
        return ProductSpec.from_api(result)

    async def list_products(
        self,
        contract_types: str = "call_options,put_options",
        underlying: str = "BTC",
        states: str = "live",
    ) -> list[dict[str, Any]]:
        """List active products for live symbol discovery."""
        params = {"contract_types": contract_types, "states": states}
        data = await self._get("/v2/products", params=params)
        all_products = data.get("result") or []
        return [p for p in all_products if (p.get("underlying_asset", {}) or {}).get("symbol") == underlying]

    async def get_ticker(self, symbol: str) -> OptionTicker:
        """Fetch snapshot ticker for an option symbol."""
        data = await self._get(f"/v2/tickers/{symbol}")
        result = data.get("result")
        if not result or not isinstance(result, dict):
            raise DeltaRestError(f"Empty or invalid ticker for symbol {symbol}")
        return OptionTicker.from_rest(result)

    async def get_all_tickers(self) -> list[dict[str, Any]]:
        """Fetch snapshot tickers for all active products."""
        data = await self._get("/v2/tickers")
        return data.get("result") or []

    async def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> list[Candle]:
        """Fetch historical candles between start and end (epoch seconds)."""
        params = {"symbol": symbol, "resolution": resolution, "start": start, "end": end}
        data = await self._get("/v2/history/candles", params=params)
        raw_candles = data.get("result") or []
        candles = [Candle.from_api(c) for c in raw_candles]
        candles.sort(key=lambda c: c.t)
        return candles

    async def warm_up_candles(
        self,
        symbol: str,
        resolution: str = "5m",
        count: int = 500,
        step_seconds: int = 300,
    ) -> list[Candle]:
        """Fetch the most recent closed candles to warm up indicators and structure.

        Drops the still-forming (current) candle so only strictly closed candles are returned.
        """
        now_sec = int(time.time())
        # Candle currently open started at (now_sec // step_seconds) * step_seconds
        current_forming_start = (now_sec // step_seconds) * step_seconds
        end_time = current_forming_start  # End before the currently forming candle
        start_time = end_time - (count + 10) * step_seconds

        candles = await self.get_candles(symbol=symbol, resolution=resolution, start=start_time, end=end_time)

        # Strictly exclude any candle that opened at or after current_forming_start
        closed = [c for c in candles if c.t < current_forming_start]
        return closed[-count:]
