"""Replay determinism tests: bit-for-bit replay reproduction (docs/01 §4, docs/05 §4)."""

import time
from pathlib import Path

import pytest

from rr_engine.config import get_settings
from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.journal.db import JournalDB
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec
from scripts.calibrate import run_calibration_report
from scripts.replay import replay_evaluation


@pytest.mark.anyio
async def test_bit_for_bit_replay(tmp_path: Path):
    db_file = tmp_path / "replay_journal.db"
    journal = JournalDB(db_path=db_file)

    state = MarketState()
    state.is_ws_connected = True
    now = time.time()
    state.spot_index_price = 64850.7
    state.perp_mark_price = 64855.0
    state.clock.record("index", now * 1_000_000, now)

    symbol = "C-BTC-64800-281126"
    spec = ProductSpec(
        id=123,
        symbol=symbol,
        contract_type="call_options",
        strike_price=64800.0,
        contract_value=0.001,
        tick_size=0.1,
        settlement_time_iso="2026-11-28T12:00:00Z",
        settlement_time_epoch=now + 86400 * 3,
        state="live",
        trading_status="operational",
        taker_commission_rate=0.0001,
        maker_commission_rate=0.0001,
        premium_commission_rate=0.035,
        spot_index_symbol=".DEXBTUSD",
        underlying_asset_symbol="BTC",
        fetched_at=now,
    )
    state.products.set(spec)

    tk = OptionTicker(
        symbol=symbol,
        mark_price=700.0,
        best_bid=698.0,
        best_ask=702.0,
        bid_size=100.0,
        ask_size=100.0,
        delta=0.52,
        timestamp=now,
    )
    state.update_option_ticker(tk)
    state.active_symbol = symbol

    candles = [Candle(t=int(now - (100 - i) * 300), o=64800, h=64900, l=64750, c=64850) for i in range(100)]
    state.candles_5m.set_closed_candles(candles)

    # 1. Run live evaluation with journal recording enabled
    settings = get_settings()
    engine = RREngine(
        feed_manager=None,
        settings=settings,
        state=state,
        journal=journal,
        enable_journal=True,
    )

    req = EvaluateRequest(symbol=symbol, lots=10.0, hold_minutes=30)
    resp = await engine.evaluate(req)

    assert resp.provenance.evaluation_id is not None
    eval_id = resp.provenance.evaluation_id
    assert eval_id == 1
    assert journal.count_evaluations() == 1

    # 2. Replay evaluation from journal DB: must be bit-for-bit identical
    match = await replay_evaluation(eval_id=eval_id, db_path=str(db_file), verbose=True)
    assert match is True, "Replay should be bit-for-bit identical to stored evaluation"


@pytest.mark.anyio
async def test_replay_catches_divergence(tmp_path: Path):
    db_file = tmp_path / "diverge_journal.db"
    journal = JournalDB(db_path=db_file)

    # Record fake evaluation
    eval_id = journal.record_evaluation(
        timestamp_utc="2026-11-19T12:00:00Z",
        symbol="C-BTC-64800-281126",
        spot_index_price=64850.7,
        verdict="TRADE",
        validation_status="UNVALIDATED",
        config_hash="test_hash",
        request_data={"symbol": "C-BTC-64800-281126", "lots": 10.0, "hold_minutes": 30},
        market_snapshot={
            "spot_index_price": 64850.7,
            "candles_5m": [],
            "clock_records": {},
        },
        response_data={
            "verdict": "TRADE",
            "validation_status": "UNVALIDATED",
            "reasons": [],
            "setup": None,
        },
    )

    # Replay will evaluate with empty candles -> will fail-closed to NO_TRADE (CANDLE_GAP or FEED_DISCONNECTED)
    # This must differ from stored 'TRADE' verdict and replay_evaluation must return False!
    match = await replay_evaluation(eval_id=eval_id, db_path=str(db_file))
    assert match is False, "Replay must detect divergence and return False"


def test_calibration_report_run(tmp_path: Path):
    db_file = tmp_path / "calib_journal.db"
    journal = JournalDB(db_path=db_file)

    # Add 2 evaluations and 2 paper trades
    e1 = journal.record_evaluation(
        timestamp_utc="2026-11-19T10:00:00Z",
        symbol="C-BTC-65000-281126",
        spot_index_price=64850.0,
        verdict="TRADE",
        validation_status="UNVALIDATED",
        config_hash="h1",
        request_data={"lots": 10.0},
        market_snapshot={},
        response_data={"verdict": "TRADE"},
        net_rr=1.85,
        breakeven_win_probability=0.35,
    )

    t1 = journal.record_paper_trade(
        symbol="C-BTC-65000-281126",
        direction="call",
        lots=10.0,
        entry_price=700.0,
        sl_price=480.0,
        tp_price=1050.0,
        entered_at="2026-11-19T10:00:00Z",
        evaluation_id=e1,
    )
    journal.update_paper_trade_exit(
        trade_id=t1,
        actual_exit_price=1045.0,
        exit_reason="TP_HIT",
        exited_at="2026-11-19T10:25:00Z",
        pnl_usd=345.0,
    )

    # Run calibration report without error
    run_calibration_report(db_path=str(db_file))
