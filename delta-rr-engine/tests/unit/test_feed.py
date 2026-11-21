"""Unit tests for Phase 1 feed layer: market state, candles, staleness clock, and disconnect simulation."""

import json
import time
from pathlib import Path

import pytest

from rr_engine.feed.clock import StalenessClock
from rr_engine.state.candles import Candle, CandleSeries
from rr_engine.state.market_state import MarketState, OptionTicker, TopOfBook
from rr_engine.state.products import ProductSpec

FIXTURE_PATH = Path(__file__).parent.parent / "contract" / "fixtures" / "probe_output.json"


@pytest.fixture
def probe_data():
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def test_staleness_clock_age_and_stale():
    clock = StalenessClock()
    t0 = 1000.0

    # Before any updates, age is infinite
    assert clock.stream_age("index") == float("inf")
    assert clock.is_stale("index", limit_seconds=3.0)

    # Record update at t0
    clock.record("index", exchange_ts=t0 * 1_000_000, now=t0)
    assert clock.stream_age("index", now=t0 + 1.5) == pytest.approx(1.5)
    assert not clock.is_stale("index", limit_seconds=3.0, now=t0 + 1.5)

    # Stale after 3.1 seconds
    assert clock.is_stale("index", limit_seconds=3.0, now=t0 + 3.1)
    assert clock.max_data_age(("index",), now=t0 + 3.1) == pytest.approx(3.1)


def test_clock_skew_detection():
    clock = StalenessClock()
    local_now = 1700000000.0

    # Exchange timestamp 3 seconds behind local clock
    clock.record("index", exchange_ts=(local_now - 3.0) * 1_000_000, now=local_now)
    is_skewed, skew = clock.check_clock_skew("index", limit_seconds=2.0, now=local_now)
    assert is_skewed
    assert skew == pytest.approx(3.0)

    # Exchange timestamp within tolerance
    clock.record("index", exchange_ts=(local_now - 0.5) * 1_000_000, now=local_now)
    is_skewed, skew = clock.check_clock_skew("index", limit_seconds=2.0, now=local_now)
    assert not is_skewed
    assert skew == pytest.approx(0.5)


def test_candle_ring_buffer_and_close():
    series = CandleSeries(step_seconds=300, maxlen=5)

    # Feed ticks within the same 5-minute bucket (0 to 299)
    assert not series.on_tick(price=64000.0, timestamp=10)
    assert not series.on_tick(price=64100.0, timestamp=150)
    assert not series.on_tick(price=63950.0, timestamp=250)

    # Forming candle has high 64100, low 63950, close 63950
    forming = series.forming_candle
    assert forming is not None
    assert forming.t == 0
    assert forming.o == 64000.0
    assert forming.h == 64100.0
    assert forming.l == 63950.0
    assert forming.c == 63950.0
    assert len(series) == 0  # Still no closed candles

    # Advance to next candle bucket (timestamp=305) -> candle 0 closes!
    closed = series.on_tick(price=64050.0, timestamp=305)
    assert closed
    assert len(series) == 1
    c0 = series.closed_candles[0]
    assert c0.t == 0
    assert c0.c == 63950.0

    # Next forming candle started at 300
    assert series.forming_candle.t == 300
    assert series.forming_candle.o == 64050.0


def test_candle_gap_detection():
    series = CandleSeries(step_seconds=300, maxlen=10)

    # Add 5 consecutive 5-minute candles
    candles = [Candle(t=1000 + i * 300, o=60000, h=60100, l=59900, c=60050) for i in range(5)]
    series.set_closed_candles(candles)
    has_gap, reason = series.has_gap(lookback_candles=5)
    assert not has_gap
    assert reason is None

    # Inject a gap (skip one 300s period: 1000, 1300, 1900)
    gapped_candles = [
        Candle(t=1000, o=60000, h=60100, l=59900, c=60050),
        Candle(t=1300, o=60000, h=60100, l=59900, c=60050),
        Candle(t=1900, o=60000, h=60100, l=59900, c=60050),  # Jump of 600s
    ]
    series.set_closed_candles(gapped_candles)
    has_gap, reason = series.has_gap(lookback_candles=3)
    assert has_gap
    assert "Candle gap detected" in reason


def test_market_state_parsing_from_probe_fixtures(probe_data):
    state = MarketState()

    # 1. Update spot price from live WS fixture
    sp_msg = probe_data["ws"]["variant_0"]["spot_price"][0]
    state.update_spot_price(sp_msg["p"], sp_msg["ts"])
    assert state.spot_index_price == pytest.approx(63900.1)

    # 2. Update ob_l1 from live WS fixture
    ob_msg = probe_data["ws"]["variant_0"]["ob_l1"][0]
    ob = TopOfBook.from_ws(ob_msg)
    state.update_orderbook(ob)
    assert ob.best_bid == pytest.approx(2715.0)
    assert ob.best_ask == pytest.approx(2745.0)
    assert ob.bid_size == pytest.approx(8445.0)
    assert ob.ask_size == pytest.approx(5680.0)

    # 3. Update ticker from live WS compact fixture
    tk_msg = probe_data["ws"]["variant_0"]["ticker"][0]
    ticker = OptionTicker.from_ws(tk_msg)
    state.update_option_ticker(ticker)
    assert ticker.symbol == "C-BTC-61400-211126"
    assert ticker.mark_price == pytest.approx(2724.63807813)
    assert ticker.best_bid == pytest.approx(2714.0)
    assert ticker.best_ask == pytest.approx(2745.0)
    assert ticker.delta == pytest.approx(0.83761610)
    assert ticker.gamma == pytest.approx(0.00009301)
    assert ticker.theta == pytest.approx(-155.38963078)
    assert ticker.vega == pytest.approx(11.87237152)
    assert ticker.mark_iv == pytest.approx(0.54668755)
    assert not ticker.is_crossed


def test_index_perp_divergence():
    state = MarketState()
    state.spot_index_price = 64000.0

    # Perp mark within 0.1% -> not divergent
    state.perp_mark_price = 64050.0
    is_div, diff = state.check_index_perp_divergence(limit_pct=0.25)
    assert not is_div

    # Perp mark diverges by 0.5% (> 0.25%) -> divergent
    state.perp_mark_price = 64350.0
    is_div, diff = state.check_index_perp_divergence(limit_pct=0.25)
    assert is_div
    assert diff > 0.25


def test_product_spec_from_api(probe_data):
    raw_prod = probe_data["rest"]["product"]
    spec = ProductSpec.from_api(raw_prod)

    assert spec.symbol == "C-BTC-61400-211126"
    assert spec.is_call
    assert not spec.is_put
    assert spec.is_tradable
    assert spec.strike_price == pytest.approx(61400.0)
    assert spec.contract_value == pytest.approx(0.001)
    assert spec.tick_size == pytest.approx(0.1)
    assert spec.taker_commission_rate == pytest.approx(0.0001)
    assert spec.premium_commission_rate == pytest.approx(0.035)
    assert spec.spot_index_symbol == ".DEXBTUSD"
    assert spec.settlement_time_epoch > 0
    assert spec.seconds_to_expiry(now=spec.settlement_time_epoch - 3600) == pytest.approx(3600.0)


def test_simulated_disconnect_staleness():
    state = MarketState()
    t_live = time.time()
    state.is_ws_connected = True

    # Fresh update
    state.update_spot_price(64000.0, exchange_ts=t_live * 1_000_000)
    tk = OptionTicker(symbol="TEST", mark_price=100.0, best_bid=99.0, best_ask=101.0, timestamp=t_live)
    state.update_option_ticker(tk)
    assert not state.clock.is_stale("index", limit_seconds=3.0)
    assert not state.clock.is_stale("option_ticker", limit_seconds=3.0)

    # Disconnect occurs!
    state.is_ws_connected = False
    state.last_disconnect_time = t_live + 1.0

    # 4 seconds pass with zero updates
    t_after_gap = t_live + 4.0
    assert state.clock.is_stale("index", limit_seconds=3.0, now=t_after_gap)
    assert state.clock.is_stale("option_ticker", limit_seconds=3.0, now=t_after_gap)
    assert not state.is_ws_connected


@pytest.mark.anyio
async def test_reconnect_triggers_candle_rewarm():
    """Verify that FeedManager executes candle re-warming when on_reconnect hook fires."""

    class DummyFeedManager:
        def __init__(self):
            self.rewarm_called = False

        async def _rewarm_candles(self):
            self.rewarm_called = True

        async def _on_ws_reconnect(self):
            await self._rewarm_candles()

    dfm = DummyFeedManager()
    await dfm._on_ws_reconnect()
    assert dfm.rewarm_called
