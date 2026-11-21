"""Candle representation, closed-candle detection, and ring buffer.

CONVENTIONS:
  - Time is UTC epoch seconds (open time of the candle).
  - 5-minute candles have a 300-second step.
  - Closed candles only feed indicators and structure. The forming candle
    is kept separate and never passed to indicators.
  - Missing candles in lookback window trigger the CANDLE_GAP gate.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Candle:
    t: int  # Epoch seconds of candle open
    o: float
    h: float
    l: float
    c: float
    v: float | None = None  # None for spot index (no volume), float for perp

    def to_dict(self) -> dict:
        """Matches reference math dictionary format."""
        d = {"t": self.t, "o": self.o, "h": self.h, "l": self.l, "c": self.c}
        if self.v is not None:
            d["v"] = self.v
        return d

    @classmethod
    def from_api(cls, raw: dict) -> Candle:
        """Parse raw candle from Delta REST or WebSocket."""
        # Timestamps can be under 'time', 't', or 'timestamp'
        t_val = raw.get("time") or raw.get("t") or raw.get("timestamp") or 0
        t_sec = int(float(t_val))
        if t_sec > 1e15:
            t_sec //= 1_000_000
        elif t_sec > 1e12:
            t_sec //= 1_000

        vol_val = raw.get("volume") or raw.get("v")
        v = float(vol_val) if vol_val is not None and vol_val != "" else None

        return cls(
            t=t_sec,
            o=float(Decimal(str(raw.get("open") or raw.get("o") or 0))),
            h=float(Decimal(str(raw.get("high") or raw.get("h") or 0))),
            l=float(Decimal(str(raw.get("low") or raw.get("l") or 0))),
            c=float(Decimal(str(raw.get("close") or raw.get("c") or 0))),
            v=v,
        )


class CandleSeries:
    """Maintains a ring buffer of closed candles plus the currently forming candle."""

    def __init__(self, step_seconds: int = 300, maxlen: int = 2000) -> None:
        self.step_seconds = step_seconds
        self.maxlen = maxlen
        self._closed: deque[Candle] = deque(maxlen=maxlen)
        self._forming: Candle | None = None

    def __len__(self) -> int:
        return len(self._closed)

    @property
    def closed_candles(self) -> list[Candle]:
        """Returns ordered list of closed candles (oldest to newest)."""
        return list(self._closed)

    @property
    def closed_dicts(self) -> list[dict]:
        """Returns list of dicts suitable for reference/rr_reference/ functions."""
        return [c.to_dict() for c in self._closed]

    @property
    def forming_candle(self) -> Candle | None:
        return self._forming

    def set_closed_candles(self, candles: Sequence[Candle]) -> None:
        """Seed or re-warm closed candles (e.g. from REST history).

        Sorts by timestamp, deduplicates, and populates the buffer.
        """
        sorted_candles = sorted(candles, key=lambda c: c.t)
        self._closed.clear()
        seen_times = set()
        for c in sorted_candles:
            if c.t not in seen_times:
                self._closed.append(c)
                seen_times.add(c.t)

    def on_tick(self, price: float, timestamp: float | int, volume: float | None = None) -> bool:
        """Update forming candle from a price tick.

        Returns True if a candle just closed.
        """
        t_sec = int(timestamp)
        candle_open_t = (t_sec // self.step_seconds) * self.step_seconds

        closed_new_candle = False

        if self._forming is None:
            self._forming = Candle(t=candle_open_t, o=price, h=price, l=price, c=price, v=volume)
        elif candle_open_t > self._forming.t:
            # Forming candle is now completed and closed!
            self.add_closed(self._forming)
            closed_new_candle = True
            self._forming = Candle(t=candle_open_t, o=price, h=price, l=price, c=price, v=volume)
        else:
            # Update current forming candle
            new_v = (self._forming.v or 0.0) + (volume or 0.0) if volume is not None else self._forming.v
            self._forming = Candle(
                t=self._forming.t,
                o=self._forming.o,
                h=max(self._forming.h, price),
                l=min(self._forming.l, price),
                c=price,
                v=new_v,
            )

        return closed_new_candle

    def on_candle_update(self, candle: Candle, now: float | None = None) -> bool:
        """Ingest a candle update from the candlestick feed.

        Only a candle whose start time is strictly older than the current 5-minute bucket
        may be closed. The candle for the current 5-minute bucket is kept in _forming and
        never added to _closed. When a new bucket starts, the previous forming candle closes
        with its final OHLC.
        Returns True if a candle just closed.
        """
        import time

        cur_time = time.time() if now is None else now
        current_bucket_t = (int(cur_time) // self.step_seconds) * self.step_seconds

        closed_new_candle = False

        if candle.t < current_bucket_t:
            # Candle's 5-minute interval has completely passed; it is closed
            if self._forming and self._forming.t == candle.t:
                self.add_closed(candle)
                self._forming = None
                closed_new_candle = True
            else:
                self.add_closed(candle)
                closed_new_candle = True
        else:
            # Candle is in the current (or future) forming bucket
            # If an earlier bucket's forming candle exists, close it first!
            if self._forming and self._forming.t < candle.t:
                self.add_closed(self._forming)
                closed_new_candle = True
            self._forming = candle

        return closed_new_candle

    def add_closed(self, candle: Candle) -> None:
        """Append a closed candle ensuring monotonically increasing time."""
        if self._closed and candle.t <= self._closed[-1].t:
            if candle.t == self._closed[-1].t:
                self._closed[-1] = candle
            return
        self._closed.append(candle)

    def has_gap(self, lookback_candles: int = 60) -> tuple[bool, str | None]:
        """Checks for missing candles in the most recent closed lookback.

        Returns (has_gap, reason).
        """
        if len(self._closed) < 2:
            return False, None

        candles = list(self._closed)
        check_count = min(len(candles), lookback_candles)
        subset = candles[-check_count:]

        for i in range(1, len(subset)):
            diff = subset[i].t - subset[i - 1].t
            if diff != self.step_seconds:
                return True, (
                    f"Candle gap detected: {diff}s between candle at "
                    f"{subset[i - 1].t} and {subset[i].t} (expected {self.step_seconds}s)"
                )
        return False, None
