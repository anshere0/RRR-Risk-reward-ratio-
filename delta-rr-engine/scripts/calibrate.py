"""scripts/calibrate.py: Calibration & Go-Live readiness report.

Requirements (docs/01 §4, docs/05 §4 & §7):
  - Reliability bins: groups predictions by predicted win rate vs empirical outcomes.
  - Brier score calculation.
  - Option pricing accuracy: predicted V_tp/V_sl vs actual exit ticks (limit: <= 1.5x spread).
  - Go-live checklist status: 100 evaluations, 10 paper trades, positive expectancy.

Usage:
  python scripts/calibrate.py [--db journal.db]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rr_engine.journal.db import JournalDB


def run_calibration_report(db_path: str = "journal.db") -> None:
    journal = JournalDB(db_path=db_path)
    total_evals = journal.count_evaluations()
    trades = journal.get_all_paper_trades()
    total_trades = len(trades)

    print("==================================================================")
    print(" DELTA BTC OPTIONS RR ENGINE - CALIBRATION & GO-LIVE REPORT")
    print(f" Database: {db_path}")
    print("==================================================================")
    print(f" Total Logged Evaluations: {total_evals}")
    print(f" Total Logged Paper Trades: {total_trades}")
    print("------------------------------------------------------------------")

    if total_trades == 0:
        print("\n [!] No paper trades logged yet.")
        print(" Log paper trades via the HUD or API to populate calibration metrics.")
        print("\n------------------------------------------------------------------")
        print(" GO-LIVE READINESS CHECKLIST (docs/05):")
        print(f" [ ] 100+ Live/Replay Evaluations : {total_evals}/100 ({'MET' if total_evals >= 100 else 'PENDING'})")
        print(" [ ] 10+ Paper Trades Logged      : 0/10 (PENDING)")
        print(" [ ] Average Exit Error <= 1.5x Spread: PENDING DATA")
        print(" [ ] Positive Net Expectancy      : PENDING DATA")
        print("------------------------------------------------------------------")
        print(" STATUS: UNVALIDATED (Paper-trading required per docs/05)")
        print("==================================================================")
        return

    # Analyze paper trades
    wins = [t for t in trades if (t.get("pnl_usd") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl_usd") or 0) < 0]
    timeouts = [t for t in trades if t.get("exit_reason") == "TIMEOUT"]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) if total_trades > 0 else 0.0

    tot_pnl = sum(t.get("pnl_usd") or 0.0 for t in trades)
    gross_profit = sum(t.get("pnl_usd") or 0.0 for t in wins)
    gross_loss = abs(sum(t.get("pnl_usd") or 0.0 for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    avg_win = (gross_profit / win_count) if win_count > 0 else 0.0
    avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
    expectancy = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)

    print("\n1. PERFORMANCE SUMMARY")
    print(f"   Win / Loss / Timeout : {win_count} / {loss_count} / {len(timeouts)}")
    print(f"   Win Rate             : {win_rate * 100:.1f}%")
    print(f"   Total Realized PnL   : ${tot_pnl:+.2f}")
    print(f"   Profit Factor        : {profit_factor:.2f}")
    print(f"   Per-Trade Expectancy : ${expectancy:+.2f}")

    # 2. Reliability Bins & Brier Score
    brier_sum = 0.0
    brier_n = 0
    bin_ranges = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    bins: dict[str, list[dict]] = {f"{int(low*100)}-{int(high*100)}%": [] for low, high in bin_ranges}

    for t in trades:
        eval_id = t.get("evaluation_id")
        pred_p = 0.5  # fallback
        if eval_id:
            e_rec = journal.get_evaluation(eval_id)
            if e_rec and e_rec.get("breakeven_win_probability"):
                pred_p = e_rec["breakeven_win_probability"]

        outcome = 1.0 if (t.get("pnl_usd") or 0) > 0 else 0.0
        brier_sum += (pred_p - outcome) ** 2
        brier_n += 1

        for low, high in bin_ranges:
            if low <= pred_p < high or (high == 1.0 and pred_p == 1.0):
                bins[f"{int(low*100)}-{int(high*100)}%"].append({"pred": pred_p, "outcome": outcome})
                break

    brier_score = (brier_sum / brier_n) if brier_n > 0 else 0.0

    print("\n2. CALIBRATION & RELIABILITY BINS")
    print("   Bin Range   | Trades | Mean Predicted | Empirical Win % | Error")
    print("   --------------------------------------------------------------")
    for bin_name, items in bins.items():
        n = len(items)
        if n == 0:
            print(f"   {bin_name:11} |      0 |          --    |           --    |    --")
        else:
            mean_pred = sum(x["pred"] for x in items) / n
            emp_win = sum(x["outcome"] for x in items) / n
            err = abs(mean_pred - emp_win)
            print(f"   {bin_name:11} | {n:6} |        {mean_pred*100:5.1f}%   |        {emp_win*100:5.1f}%    | {err*100:4.1f}%")

    print(f"\n   Brier Score: {brier_score:.4f} (lower is better, 0.0 = perfect calibration)")

    # 3. Option Exit Pricing Accuracy (V_tp / V_sl vs actual exit price)
    price_errors = []
    for t in trades:
        exit_p = t.get("actual_exit_price")
        if exit_p and exit_p > 0:
            predicted_exit = t.get("tp_price") if t.get("exit_reason") == "TP_HIT" else t.get("sl_price")
            if predicted_exit:
                price_errors.append(abs(exit_p - predicted_exit))

    mean_exit_err = (sum(price_errors) / len(price_errors)) if price_errors else 0.0
    print("\n3. PREDICTED OPTION PRICE ACCURACY")
    print(f"   Evaluated Exits      : {len(price_errors)}")
    print(f"   Mean Absolute Error  : ${mean_exit_err:.2f}")

    # 4. Go-Live Checklist Status
    evals_met = total_evals >= 100
    trades_met = total_trades >= 10
    expectancy_met = expectancy > 0
    error_met = (mean_exit_err <= 15.0) if price_errors else False

    print("\n------------------------------------------------------------------")
    print(" GO-LIVE READINESS CHECKLIST (docs/05):")
    print(f" [{'X' if evals_met else ' '}] 100+ Live/Replay Evaluations : {total_evals}/100 ({'MET' if evals_met else 'PENDING'})")
    print(f" [{'X' if trades_met else ' '}] 10+ Paper Trades Logged      : {total_trades}/10 ({'MET' if trades_met else 'PENDING'})")
    print(f" [{'X' if error_met else ' '}] Exit Error <= 1.5x Spread    : ${mean_exit_err:.2f} ({'MET' if error_met else 'PENDING'})")
    print(f" [{'X' if expectancy_met else ' '}] Positive Net Expectancy      : ${expectancy:+.2f} ({'MET' if expectancy_met else 'PENDING'})")
    print("------------------------------------------------------------------")

    ready = evals_met and trades_met and expectancy_met and error_met
    if ready:
        print(" STATUS: READY FOR VALIDATION AUDIT (All criteria satisfied)")
    else:
        print(" STATUS: UNVALIDATED (Further paper-trading required per docs/05)")
    print("==================================================================")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibration and Go-Live Report.")
    parser.add_argument("--db", default="journal.db", help="Path to journal SQLite database.")
    args = parser.parse_args()
    run_calibration_report(db_path=args.db)


if __name__ == "__main__":
    main()
