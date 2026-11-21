"""Tests for F4: Closed candles only, forming candle isolation, single writer."""

from rr_engine.state.candles import Candle, CandleSeries
from rr_engine.state.market_state import MarketState
from rr_engine.structure.levels import standard_pivot_points


def test_forming_candle_isolation_and_close():
    series = CandleSeries(step_seconds=300)
    base_t = 1700000000  # 300-second aligned (1700000000 % 300 == 200, so let's align it)
    aligned_t = (base_t // 300) * 300

    # Seed 5 closed candles
    closed_init = [
        Candle(t=aligned_t - (5 - i) * 300, o=60000 + i, h=60100 + i, l=59900 + i, c=60050 + i) for i in range(5)
    ]
    series.set_closed_candles(closed_init)
    initial_closed_count = len(series)
    assert initial_closed_count == 5

    # Bucket T (forming)
    cur_bucket = aligned_t
    now_within_bucket = cur_bucket + 45  # 45 seconds into the bucket

    # 1. Feed candle update for forming bucket
    forming_c1 = Candle(t=cur_bucket, o=60100.0, h=60150.0, l=60080.0, c=60120.0)
    closed = series.on_candle_update(forming_c1, now=now_within_bucket)

    assert not closed, "A forming candle update must not close a candle"
    assert len(series.closed_candles) == initial_closed_count, "len(closed) must remain unchanged"
    assert series.forming_candle is not None
    assert series.forming_candle.t == cur_bucket
    assert series.forming_candle.c == 60120.0

    # 2. Feed an update to the SAME forming candle later in the bucket
    now_later = cur_bucket + 180
    forming_c2 = Candle(t=cur_bucket, o=60100.0, h=60200.0, l=60050.0, c=60190.0)
    closed = series.on_candle_update(forming_c2, now=now_later)

    assert not closed
    assert len(series.closed_candles) == initial_closed_count
    assert series.forming_candle.h == 60200.0
    assert series.forming_candle.c == 60190.0

    # 3. Advance to NEXT bucket (cur_bucket + 300)
    next_bucket = cur_bucket + 300
    now_next_bucket = next_bucket + 10
    next_forming = Candle(t=next_bucket, o=60190.0, h=60220.0, l=60180.0, c=60210.0)

    closed = series.on_candle_update(next_forming, now=now_next_bucket)
    assert closed, "Advancing to next bucket must close the previous forming candle"
    assert len(series.closed_candles) == initial_closed_count + 1

    # Verify closed candle has final OHLC of the previous forming candle
    last_closed = series.closed_candles[-1]
    assert last_closed.t == cur_bucket
    assert last_closed.h == 60200.0
    assert last_closed.c == 60190.0

    # Verify forming is now the next bucket
    assert series.forming_candle.t == next_bucket
    assert series.forming_candle.c == 60210.0


def test_pivots_stability_on_forming_updates():
    series = CandleSeries(step_seconds=300)
    aligned_t = 1700000100 // 300 * 300

    # 288 closed candles (full 24h)
    candles = [
        Candle(t=aligned_t - (288 - i) * 300, o=60000.0 + (i % 20), h=60500.0, l=59500.0, c=60000.0) for i in range(288)
    ]
    series.set_closed_candles(candles)

    # Compute pivots before forming updates
    pivots_before = standard_pivot_points(series.closed_dicts)

    # Ingest multiple forming updates
    for offset, price in [(10, 61000.0), (50, 62000.0), (120, 58000.0), (250, 60500.0)]:
        forming = Candle(t=aligned_t, o=60000.0, h=max(60000.0, price), l=min(60000.0, price), c=price)
        series.on_candle_update(forming, now=aligned_t + offset)

        # Pivots computed from closed candles must NOT change!
        pivots_during = standard_pivot_points(series.closed_dicts)
        assert pivots_during["P"] == pivots_before["P"]
        assert pivots_during["R1"] == pivots_before["R1"]
        assert pivots_during["S1"] == pivots_before["S1"]


def test_single_writer_no_tick_pollution():
    state = MarketState()
    # update_spot_price should NOT mutate candles_5m
    state.update_spot_price(65000.0, exchange_ts=1700000050)
    assert len(state.candles_5m) == 0
    assert state.candles_5m.forming_candle is None
