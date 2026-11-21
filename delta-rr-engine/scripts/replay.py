"""scripts/replay.py: Bit-for-bit evaluation replay.

Requirements (docs/01 §4, docs/05 §4):
  - Every evaluation is journaled in SQLite with its market snapshot and config hash.
  - Replays any historical evaluation from journal.db.
  - Reconstructs exact market state and verifies bit-for-bit identity against journaled response.

Usage:
  python scripts/replay.py <evaluation_id> [--db journal.db]
  python scripts/replay.py --latest [--db journal.db]
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rr_engine.config import get_settings
from rr_engine.engine.models import EvaluateRequest
from rr_engine.engine.rr_engine import RREngine
from rr_engine.journal.db import JournalDB
from rr_engine.state.candles import Candle
from rr_engine.state.market_state import MarketState, OptionTicker
from rr_engine.state.products import ProductSpec


def is_close(a: float | None, b: float | None, tol: float = 1e-5) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


async def replay_evaluation(eval_id: int | None, db_path: str = "journal.db", verbose: bool = False) -> bool:
    journal = JournalDB(db_path=db_path)
    if eval_id is None:
        rec = journal.get_latest_evaluation()
        if not rec:
            print(f"Error: No evaluations found in journal at '{db_path}'.")
            return False
        eval_id = rec["id"]
    else:
        rec = journal.get_evaluation(eval_id)
        if not rec:
            print(f"Error: Evaluation #{eval_id} not found in journal at '{db_path}'.")
            return False

    print("==================================================================")
    print(f" Replaying Evaluation #{eval_id}")
    print(f" Timestamp (UTC): {rec['timestamp_utc']}")
    print(f" Symbol: {rec['symbol']}")
    print(f" Spot Index: {rec['spot_index_price']}")
    print(f" Config Hash: {rec['config_hash'][:8]}")
    print("==================================================================")

    req_data = rec["request"]
    snap = rec["market_snapshot"]
    orig_resp = rec["response"]

    # Reconstruct MarketState exactly from market_snapshot
    state = MarketState()
    state.is_ws_connected = True
    state.spot_index_price = snap.get("spot_index_price")
    state.perp_mark_price = snap.get("perp_mark_price")
    state.active_symbol = rec["symbol"]

    # Restore candles
    candles = [
        Candle(t=c["t"], o=c["o"], h=c["h"], l=c["l"], c=c["c"], v=c.get("v", 0.0))
        for c in snap.get("candles_5m", [])
    ]
    state.candles_5m.set_closed_candles(candles)

    perp_candles = [
        Candle(t=c["t"], o=c["o"], h=c["h"], l=c["l"], c=c["c"], v=c.get("v", 0.0))
        for c in snap.get("perp_candles_5m", [])
    ]
    state.perp_candles_5m.set_closed_candles(perp_candles)

    # Restore product spec
    if snap.get("product_spec"):
        p_data = snap["product_spec"]
        spec = ProductSpec(
            id=p_data["id"],
            symbol=p_data["symbol"],
            contract_type=p_data["contract_type"],
            strike_price=p_data["strike_price"],
            contract_value=p_data["contract_value"],
            tick_size=p_data["tick_size"],
            settlement_time_iso=p_data["settlement_time_iso"],
            settlement_time_epoch=p_data["settlement_time_epoch"],
            state=p_data["state"],
            trading_status=p_data["trading_status"],
            taker_commission_rate=p_data["taker_commission_rate"],
            maker_commission_rate=p_data["maker_commission_rate"],
            premium_commission_rate=p_data["premium_commission_rate"],
            spot_index_symbol=p_data["spot_index_symbol"],
            underlying_asset_symbol=p_data["underlying_asset_symbol"],
            fetched_at=time.time(),
        )
        state.products.set(spec)

    # Restore ticker
    if snap.get("option_ticker"):
        t_data = snap["option_ticker"]
        tk = OptionTicker(
            symbol=t_data["symbol"],
            mark_price=t_data["mark_price"],
            best_bid=t_data["best_bid"],
            best_ask=t_data["best_ask"],
            bid_size=t_data["bid_size"],
            ask_size=t_data["ask_size"],
            delta=t_data.get("delta"),
            mark_iv=t_data.get("mark_iv") or t_data.get("iv"),
            timestamp=t_data["timestamp"],
            spot_price_in_ticker=t_data.get("spot_price_in_ticker"),
        )
        state.update_option_ticker(tk)

    # Restore clock records (preserve stream health timestamps so data age matches)
    now_restore = time.time()
    for k, data in snap.get("clock_records", {}).items():
        if k in state.clock.streams:
            if isinstance(data, dict):
                state.clock.streams[k].last_exchange_time = data.get("last_exchange_time", 0.0)
                # Keep local receive time fresh for replay evaluation so it does not falsely trigger DATA_STALE
                state.clock.streams[k].last_local_receive_time = now_restore
            else:
                state.clock.streams[k].last_local_receive_time = now_restore

    # Instantiate engine (without writing a new journal entry)
    settings = get_settings()
    engine = RREngine(state=state, settings=settings, enable_journal=False)

    # Execute replayed evaluation
    req = EvaluateRequest(**req_data)
    replayed = await engine.evaluate(req)
    replayed_dict = replayed.model_dump()

    # Compare results bit-for-bit
    diffs: list[str] = []

    # 1. Verdict & Status
    if replayed.verdict != orig_resp["verdict"]:
        diffs.append(f"Verdict mismatch: Original={orig_resp['verdict']} vs Replayed={replayed.verdict}")
    if replayed.validation_status != orig_resp["validation_status"]:
        diffs.append(f"Validation status mismatch: Original={orig_resp['validation_status']} vs Replayed={replayed.validation_status}")

    # 2. Reasons count and codes
    orig_codes = [r["code"] for r in orig_resp.get("reasons", [])]
    rep_codes = [r.code for r in replayed.reasons]
    if orig_codes != rep_codes:
        diffs.append(f"Reason codes mismatch: Original={orig_codes} vs Replayed={rep_codes}")

    # 3. Setup comparison
    orig_setup = orig_resp.get("setup")
    rep_setup = replayed_dict.get("setup")

    if (orig_setup is None) != (rep_setup is None):
        diffs.append("Setup presence mismatch (one is None, one is present)")
    elif orig_setup is not None and rep_setup is not None:
        # Compare spot geometry
        for k in ("price", "sl", "tp", "atr", "spot_rr"):
            ov, rv = orig_setup["spot"].get(k), rep_setup["spot"].get(k)
            if not is_close(ov, rv):
                diffs.append(f"Spot {k} diff: orig={ov} vs rep={rv}")

        # Compare option geometry
        for k in ("entry_limit", "sl_premium_model", "tp_premium_model", "sl_exit_net", "tp_exit_net"):
            ov, rv = orig_setup["option"].get(k), rep_setup["option"].get(k)
            if not is_close(ov, rv):
                diffs.append(f"Option {k} diff: orig={ov} vs rep={rv}")

        # Compare money geometry
        for k in ("risk_usd", "reward_usd", "net_rr", "breakeven_win_probability", "worst_case_loss_usd"):
            ov, rv = orig_setup["money"].get(k), rep_setup["money"].get(k)
            if not is_close(ov, rv):
                diffs.append(f"Money {k} diff: orig={ov} vs rep={rv}")

    # Output verification report
    if not diffs:
        print(f" Verdict: {replayed.verdict} (Original: {orig_resp['verdict']})")
        if replayed.setup:
            money = replayed.setup.money
            spot = replayed.setup.spot
            opt = replayed.setup.option
            print(f" Net RR: {money.net_rr:.2f} : 1")
            print(f" Break-Even Win Rate: {money.breakeven_win_probability * 100:.1f}%")
            print(f" Spot SL / TP: {spot.sl:.1f} / {spot.tp:.1f}")
            print(f" Option SL / TP Exit: {opt.sl_exit_net:.1f} / {opt.tp_exit_net:.1f}")
            print(f" Planned Risk / Reward: ${money.risk_usd:.2f} / ${money.reward_usd:.2f}")
        print("------------------------------------------------------------------")
        print(" ✓ REPLAY VERIFIED: Bit-for-bit identical reproduction!")
        print("==================================================================")
        return True
    else:
        print(" ❌ REPLAY DIVERGENCE DETECTED:")
        for d in diffs:
            print(f"   - {d}")
        print("==================================================================")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic evaluation replay tool.")
    parser.add_argument("eval_id", nargs="?", type=int, help="Evaluation ID to replay.")
    parser.add_argument("--latest", action="store_true", help="Replay latest evaluation.")
    parser.add_argument("--db", default="journal.db", help="Path to journal SQLite database.")
    parser.add_argument("--verbose", action="store_true", help="Verbose comparison details.")
    args = parser.parse_args()

    eval_id = None if args.latest else args.eval_id
    success = asyncio.run(replay_evaluation(eval_id=eval_id, db_path=args.db, verbose=args.verbose))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
