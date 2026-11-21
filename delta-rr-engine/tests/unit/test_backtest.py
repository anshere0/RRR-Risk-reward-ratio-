"""Unit tests for Phase 9: Backtest A and Paper-Trading Go-Live Auditor (docs/05)."""

import copy
from pathlib import Path

from rr_engine.journal.db import JournalDB
from scripts.backtest_spot import (
    classify_session,
    generate_synthetic_btc_candles,
    run_spot_backtest,
)
from scripts.paper_report import evaluate_go_live


def test_no_lookahead_bias():
    """Verify strictly zero look-ahead bias in Backtest A.

    Altering future candles must have zero effect on the indicators,
    stop, target, or direction selected at decision time t.
    """
    candles = generate_synthetic_btc_candles(count=200, seed=123)
    trades_original = run_spot_backtest(candles=candles, hold_bars=6, warmup_bars=60)
    assert len(trades_original) > 0

    first_trade = trades_original[0]
    decision_bar = first_trade.bar_index

    # Mutate candles that occur strictly AFTER the decision bar
    mutated_candles = copy.deepcopy(candles)
    for i in range(decision_bar + 10, len(mutated_candles)):
        mutated_candles[i]["c"] *= 1.5
        mutated_candles[i]["h"] *= 1.5
        mutated_candles[i]["l"] *= 1.5

    trades_mutated = run_spot_backtest(candles=mutated_candles, hold_bars=6, warmup_bars=60)
    mutated_first_trade = trades_mutated[0]

    # Decision variables at bar t MUST be bit-for-bit identical
    assert first_trade.spot == mutated_first_trade.spot
    assert first_trade.sl == mutated_first_trade.sl
    assert first_trade.tp == mutated_first_trade.tp
    assert first_trade.atr == mutated_first_trade.atr
    assert first_trade.adx == mutated_first_trade.adx
    assert first_trade.direction == mutated_first_trade.direction
    assert first_trade.ema_aligned == mutated_first_trade.ema_aligned


def test_conservative_intrabar_touch():
    """Verify conservative rule: when both TP and SL are touched in the same bar, SL wins."""
    # Warmup candles
    candles = generate_synthetic_btc_candles(count=70, seed=99)
    # The hold window starts at bar index 60
    # Create extreme wick on bar 60 that spans both way above TP and way below SL
    candles[60]["h"] = 99999.0
    candles[60]["l"] = 1.0

    trades = run_spot_backtest(candles=candles, hold_bars=6, warmup_bars=60)
    # Both barriers touched in bar 60 -> outcome must be SL_HIT
    trade_60 = [t for t in trades if t.bar_index == 60][0]
    assert trade_60.outcome == "SL_HIT"
    assert trade_60.duration_bars == 1


def test_session_classification():
    assert "Asian" in classify_session(3)
    assert "European" in classify_session(10)
    assert "US" in classify_session(16)
    assert "Off-Hours" in classify_session(23)


def test_paper_report_audit_criteria(tmp_path: Path):
    db_file = tmp_path / "audit_journal.db"
    journal = JournalDB(db_path=db_file)

    # 1. Empty journal -> unvalidated
    empty_res = evaluate_go_live(db_path=str(db_file), min_trades_required=10)
    assert empty_res["is_validated"] is False

    # 2. Populate 10 winning trades across 5 weeks
    import datetime

    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

    for i in range(15):
        eval_id = journal.record_evaluation(
            timestamp_utc=(t0 + datetime.timedelta(days=i * 3)).isoformat(),
            symbol="C-BTC-65000-281126",
            spot_index_price=64850.0,
            verdict="TRADE",
            validation_status="UNVALIDATED",
            config_hash="h1",
            request_data={"lots": 10.0},
            market_snapshot={},
            response_data={"verdict": "TRADE"},
            net_rr=2.0,
            breakeven_win_probability=0.33,
        )

        # Mostly winning trades with positive expectancy (planned R = (700-480)*10*0.001 = $2.20)
        pnl = 4.40 if (i % 4 != 0) else -2.20
        trade_id = journal.record_paper_trade(
            symbol="C-BTC-65000-281126",
            direction="call",
            lots=10.0,
            entry_price=700.0,
            sl_price=480.0,
            tp_price=1050.0,
            entered_at=(t0 + datetime.timedelta(days=i * 3)).isoformat(),
            evaluation_id=eval_id,
            actual_fill_price=702.0,
        )
        journal.update_paper_trade_exit(
            trade_id=trade_id,
            actual_exit_price=1045.0 if pnl > 0 else 475.0,
            exit_reason="TP_HIT" if pnl > 0 else "SL_HIT",
            exited_at=(t0 + datetime.timedelta(days=i * 3, hours=1)).isoformat(),
            pnl_usd=pnl,
        )

    # Audit with min_trades_required=10
    res = evaluate_go_live(db_path=str(db_file), min_trades_required=10)
    assert res["total_paper_trades"] == 15
    assert res["criteria"]["sample_size_and_duration"]["met"] is True
    assert res["criteria"]["net_expectancy"]["met"] is True
    assert res["criteria"]["net_expectancy"]["mean_pnl"] > 0
    assert res["criteria"]["net_expectancy"]["lower_bound_80"] > 0
    assert res["criteria"]["max_drawdown_r"]["met"] is True
    assert res["is_validated"] is True
