"""Product specifications model and caching.

Parsed from Delta REST `GET /v2/products/{symbol}`.
Cached in memory; refreshed periodically (e.g. every 10 min).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


def parse_iso_to_epoch_seconds(iso_str: str) -> float:
    """Parse Delta ISO settlement_time (e.g. '2026-11-21T12:00:00Z') to UTC epoch seconds."""
    if not iso_str:
        return 0.0
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.timestamp()


@dataclass
class ProductSpec:
    id: int
    symbol: str
    contract_type: str  # 'call_options' | 'put_options'
    strike_price: float
    contract_value: float  # 0.001 BTC per lot
    tick_size: float  # 0.1
    settlement_time_iso: str  # e.g. '2026-11-21T12:00:00Z'
    settlement_time_epoch: float  # seconds
    state: str  # 'live'
    trading_status: str  # 'operational'
    taker_commission_rate: float  # e.g. 0.0001
    maker_commission_rate: float
    premium_commission_rate: float  # e.g. 0.035 (cap on premium)
    spot_index_symbol: str  # e.g. '.DEXBTUSD'
    underlying_asset_symbol: str  # 'BTC'
    fetched_at: float = 0.0

    @property
    def is_call(self) -> bool:
        return "call" in self.contract_type.lower()

    @property
    def is_put(self) -> bool:
        return "put" in self.contract_type.lower()

    @property
    def is_tradable(self) -> bool:
        return self.state.lower() == "live" and self.trading_status.lower() == "operational"

    def seconds_to_expiry(self, now: float | None = None) -> float:
        current = now if now is not None else time.time()
        return max(0.0, self.settlement_time_epoch - current)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> ProductSpec:
        spot_idx = raw.get("spot_index") or {}
        underlying = raw.get("underlying_asset") or {}
        specs = raw.get("product_specs") or {}

        settle_iso = str(raw.get("settlement_time") or "")
        settle_epoch = parse_iso_to_epoch_seconds(settle_iso)

        # Parse numbers safely via Decimal to float
        strike = float(Decimal(str(raw.get("strike_price") or 0)))
        val = float(Decimal(str(raw.get("contract_value") or "0.001")))
        tick = float(Decimal(str(raw.get("tick_size") or "0.1")))
        taker_comm = float(Decimal(str(raw.get("taker_commission_rate") or "0.0003")))
        maker_comm = float(Decimal(str(raw.get("maker_commission_rate") or "0.0001")))
        prem_comm = float(Decimal(str(specs.get("premium_commission_rate") or "0.10")))

        return cls(
            id=int(raw.get("id") or 0),
            symbol=str(raw.get("symbol") or ""),
            contract_type=str(raw.get("contract_type") or ""),
            strike_price=strike,
            contract_value=val,
            tick_size=tick,
            settlement_time_iso=settle_iso,
            settlement_time_epoch=settle_epoch,
            state=str(raw.get("state") or ""),
            trading_status=str(raw.get("trading_status") or ""),
            taker_commission_rate=taker_comm,
            maker_commission_rate=maker_comm,
            premium_commission_rate=prem_comm,
            spot_index_symbol=str(spot_idx.get("symbol") or ".DEXBTUSD"),
            underlying_asset_symbol=str(underlying.get("symbol") or "BTC"),
            fetched_at=time.time(),
        )


class ProductCache:
    """Thread-safe/async cache for product specifications with TTL."""

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, ProductSpec] = {}

    def get(self, symbol: str, now: float | None = None) -> ProductSpec | None:
        spec = self._cache.get(symbol)
        if not spec:
            return None
        current = now if now is not None else time.time()
        if (current - spec.fetched_at) > self.ttl_seconds:
            return None
        return spec

    def set(self, spec: ProductSpec) -> None:
        self._cache[spec.symbol] = spec

    def clear(self) -> None:
        self._cache.clear()
