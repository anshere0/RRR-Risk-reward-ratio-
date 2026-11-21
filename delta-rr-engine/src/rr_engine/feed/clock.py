"""Staleness clock and clock skew detection.

Delta sends timestamps:
  - Tickers & Orderbook & WS messages: microseconds (> 1e15) or milliseconds (> 1e12)
  - Candles: seconds

This module normalizes timestamps to seconds (UTC float) and tracks data age
per stream so the DATA_STALE gate (threshold default 3s) can fail closed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def normalize_timestamp_to_seconds(ts: float | int | str | None) -> float:
    """Convert any exchange timestamp (us, ms, s) to float epoch seconds."""
    if ts is None:
        return 0.0
    val = float(ts)
    if val > 1e15:
        return val / 1_000_000.0
    if val > 1e12:
        return val / 1_000.0
    return val


@dataclass
class StreamHealth:
    """Tracks updates for a single data stream."""

    name: str
    last_exchange_time: float = 0.0
    last_local_receive_time: float = 0.0
    update_count: int = 0

    def record_update(self, exchange_ts: float | int | str | None, local_now: float | None = None) -> None:
        self.last_exchange_time = normalize_timestamp_to_seconds(exchange_ts)
        self.last_local_receive_time = local_now if local_now is not None else time.time()
        self.update_count += 1

    def age_seconds(self, now: float | None = None) -> float:
        """Seconds since the last update was received locally."""
        if self.last_local_receive_time == 0.0:
            return float("inf")
        current = now if now is not None else time.time()
        return max(0.0, current - self.last_local_receive_time)


@dataclass
class StalenessClock:
    """Tracks data age across all market streams and monitors clock skew."""

    streams: dict[str, StreamHealth] = field(default_factory=dict)
    max_skew_seen: float = 0.0

    def __post_init__(self) -> None:
        for name in ("index", "option_ticker", "ob_l1", "candles_5m", "perp_5m"):
            self.streams[name] = StreamHealth(name=name)

    def record(self, stream_name: str, exchange_ts: float | int | str | None, now: float | None = None) -> None:
        local_now = now if now is not None else time.time()
        if stream_name not in self.streams:
            self.streams[stream_name] = StreamHealth(name=stream_name)
        self.streams[stream_name].record_update(exchange_ts, local_now)

        # Monitor clock skew if exchange_ts is valid
        if exchange_ts is not None:
            ts_sec = normalize_timestamp_to_seconds(exchange_ts)
            if ts_sec > 0:
                skew = abs(local_now - ts_sec)
                if skew > self.max_skew_seen:
                    self.max_skew_seen = skew

    def stream_age(self, stream_name: str, now: float | None = None) -> float:
        if stream_name not in self.streams:
            return float("inf")
        return self.streams[stream_name].age_seconds(now)

    def max_data_age(
        self, required_streams: tuple[str, ...] = ("index", "option_ticker"), now: float | None = None
    ) -> float:
        """Returns the oldest data age among required streams."""
        return max(self.stream_age(s, now) for s in required_streams)

    def is_stale(self, stream_name: str, limit_seconds: float = 3.0, now: float | None = None) -> bool:
        return self.stream_age(stream_name, now) > limit_seconds

    def check_clock_skew(
        self, stream_name: str, limit_seconds: float = 2.0, now: float | None = None
    ) -> tuple[bool, float]:
        """Check if local time diverges from latest exchange time by > limit_seconds."""
        health = self.streams.get(stream_name)
        if not health or health.last_exchange_time == 0.0:
            return False, 0.0
        local_now = now if now is not None else time.time()
        skew = abs(local_now - health.last_exchange_time)
        return (skew > limit_seconds), skew
