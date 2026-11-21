"""Unit tests for SQLite JournalDB and Section F session discipline gates."""

from pathlib import Path

from rr_engine.engine.gates import gate_cooldown, gate_daily_loss_limit
from rr_engine.journal.db import JournalDB


def test_journal_crud(tmp_path: Path):
    db_file = tmp_path / "test_journal.db"
    journal = JournalDB(db_path=db_file)

    assert journal.count_evaluations() == 0
    assert journal.count_paper_trades() == 0

    # Insert evaluation
    eval_id = journal.record_evaluation(
        timestamp_utc="2026-11-19T12:00:00Z",
        symbol="C-BTC-65000-281126",
        spot_index_price=64850.0,
        verdict="TRADE",
        validation_status="UNVALIDATED",
        config_hash="a1b2c3d4",
        request_data={"lots": 10.0},
        market_snapshot={"spot": 64850.0},
        response_data={"verdict": "TRADE"},
        net_rr=1.85,
        breakeven_win_probability=0.35,
    )

    assert eval_id == 1
    assert journal.count_evaluations() == 1

    rec = journal.get_evaluation(eval_id)
    assert rec is not None
    assert rec["symbol"] == "C-BTC-65000-281126"
    assert rec["verdict"] == "TRADE"
    assert rec["request"]["lots"] == 10.0
    assert rec["market_snapshot"]["spot"] == 64850.0
    assert rec["net_rr"] == 1.85

    latest = journal.get_latest_evaluation()
    assert latest is not None
    assert latest["id"] == eval_id

    summaries = journal.list_evaluations(limit=10)
    assert len(summaries) == 1
    assert summaries[0]["id"] == eval_id


def test_journal_paper_trades(tmp_path: Path):
    db_file = tmp_path / "test_journal.db"
    journal = JournalDB(db_path=db_file)

    # 1. Add paper trade
    trade_id = journal.record_paper_trade(
        symbol="C-BTC-65000-281126",
        direction="call",
        lots=10.0,
        entry_price=700.0,
        sl_price=480.0,
        tp_price=1050.0,
        entered_at="2026-11-19T12:00:00Z",
        evaluation_id=1,
    )
    assert trade_id == 1
    assert journal.count_paper_trades() == 1

    # 2. Update exit (loss)
    journal.update_paper_trade_exit(
        trade_id=trade_id,
        actual_exit_price=475.0,
        exit_reason="SL_HIT",
        exited_at="2026-11-19T12:15:00Z",
        pnl_usd=-225.0,
    )

    all_trades = journal.get_all_paper_trades()
    assert len(all_trades) == 1
    assert all_trades[0]["pnl_usd"] == -225.0
    assert all_trades[0]["exit_reason"] == "SL_HIT"

    # Daily loss query
    daily_loss = journal.get_daily_loss("2026-11-19")
    assert daily_loss == 225.0

    # Consecutive loss query
    cons, last_time = journal.get_consecutive_losses()
    assert cons == 1
    assert last_time == "2026-11-19T12:15:00Z"


def test_session_discipline_gates():
    # Daily loss limit gate
    g_pass = gate_daily_loss_limit(daily_loss_usd=150.0, limit_usd=500.0)
    assert not g_pass.fired

    g_fail = gate_daily_loss_limit(daily_loss_usd=520.0, limit_usd=500.0)
    assert g_fail.fired
    assert g_fail.code == "DAILY_LOSS_LIMIT"
    assert g_fail.severity == "hard"

    # Cooldown gate
    # 2 losses (threshold 3) -> pass
    c_pass1 = gate_cooldown(consecutive_losses=2, last_loss_seconds_ago=100.0, max_losses=3)
    assert not c_pass1.fired

    # 3 losses, 10 min ago (cooldown 30 min = 1800s) -> fail
    c_fail = gate_cooldown(consecutive_losses=3, last_loss_seconds_ago=600.0, max_losses=3, cooldown_seconds=1800.0)
    assert c_fail.fired
    assert c_fail.code == "COOLDOWN"

    # 3 losses, 35 min ago (cooldown expired) -> pass
    c_pass2 = gate_cooldown(consecutive_losses=3, last_loss_seconds_ago=2100.0, max_losses=3, cooldown_seconds=1800.0)
    assert not c_pass2.fired
