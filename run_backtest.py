"""
run_backtest.py
==================================================
Command-line runner for the backtest engine.

    python run_backtest.py --strategy straddle --dte 45 --target-pct 50 --stop-pct 100 \
        --exit-dte 21 --start 2023-01-01 --end 2023-12-31

Prints the summary and every trade, and writes the trades to results/*.csv.
"""

import argparse
import datetime
import pathlib
import sys

import pandas as pd

from backtest_engine import run_backtest
from backtest_engine.data import get_available_expiries
from backtest_engine.storage import coverage_table, save_run
from backtest_engine.strategies import STRATEGIES

ROOT = pathlib.Path(__file__).resolve().parent


def _parse_args():
    p = argparse.ArgumentParser(
        description="Sell the ATM straddle N days before expiry; exit at X%% of the credit "
                    "(profit target) or Y%% of the credit as a loss (stop), else at --exit-dte, "
                    "replayed across monthly expiries."
    )
    p.add_argument("--strategy", choices=list(STRATEGIES), default="straddle",
                   help="straddle = sell ATM call+put; strangle = sell call at ATM+n and put at ATM-n")
    p.add_argument("--n", type=int, default=200,
                   help="strangle only: strike offset in index points, call=ATM+n / put=ATM-n (default 200)")
    p.add_argument("--dte", type=int, default=45, help="calendar days before expiry to enter (default 45)")
    p.add_argument("--target-pct", type=float, default=50.0,
                   help="profit target: %% of the credit collected, i.e. of max profit (default 50)")
    p.add_argument("--stop-pct", type=float, default=100.0,
                   help="stop loss: %% of the credit collected, taken as a loss (default 100)")
    p.add_argument("--lot-size", type=int, default=65, help="contract multiplier (default 65)")
    p.add_argument("--exit-dte", type=int, default=21,
                   help="time exit: calendar days before expiry to close if no target/stop hit (default 21)")
    p.add_argument("--start", type=datetime.date.fromisoformat, default=None,
                   help="first expiry date to include, YYYY-MM-DD (default: earliest in the data)")
    p.add_argument("--end", type=datetime.date.fromisoformat, default=None,
                   help="last expiry date to include, YYYY-MM-DD (default: latest in the data)")
    p.add_argument("--db", default=str(ROOT / "data" / "market_data.duckdb"), help="path to the DuckDB file")
    p.add_argument("--out-dir", default=str(ROOT / "results"), help="results root; full runs go to <root>/<strategy>, shorter runs to <root>/<strategy>_short_runs")
    return p.parse_args()


def main():
    args = _parse_args()

    db_file = pathlib.Path(args.db)
    if not db_file.is_file():
        sys.exit(f"Database not found: {db_file}")
    db_path = str(db_file.resolve())

    expiries = get_available_expiries(db_path)
    start = args.start or pd.to_datetime(min(expiries)).date()
    end = args.end or pd.to_datetime(max(expiries)).date()
    if start > end:
        sys.exit(f"--start ({start}) is after --end ({end}).")

    n = args.n if STRATEGIES[args.strategy]["has_n"] else 0
    what = f"strangle (call ATM+{n} / put ATM-{n})" if n else "ATM straddle"
    print(f"Rule: sell {what}, enter {args.dte} DTE, target {args.target_pct:g}% / "
          f"stop {args.stop_pct:g}% of credit, time exit {args.exit_dte} DTE, lot size {args.lot_size}")
    print(f"Sweep: monthly expiries from {start} to {end}")
    print("Running (each expiry takes a couple of seconds to load)...\n")

    trades_df, summary, skipped_df = run_backtest(
        db_path, args.dte, args.target_pct, args.stop_pct, args.lot_size, start, end, args.exit_dte, n
    )

    if summary["total_trades"] == 0:
        print(f"No trades produced -- all {summary['skipped_count']} eligible expiries were skipped.")
    else:
        trades_df = trades_df.drop(columns=["skipped"])
        print(trades_df.to_string(index=False))
        print()
        print(f"Trades          : {summary['total_trades']}")
        print(f"Win rate        : {summary['win_rate']:.1f}%")
        print(f"Total P&L       : {summary['total_pnl']:,.2f}")
        print(f"Avg P&L / trade : {summary['avg_pnl']:,.2f}")
        print(f"Avg holding     : {summary['avg_holding_days']:.1f} days")
        print(f"Best trade      : {summary['best_trade']:,.2f}")
        print(f"Worst trade     : {summary['worst_trade']:,.2f}")

        trades_path, full = save_run(args.strategy, trades_df, pathlib.Path(args.out_dir), expiries, args.dte,
                                     args.target_pct, args.stop_pct, args.exit_dte, start, end, n)
        kind = "FULL run (counts in Compare runs)" if full else "short run (not in Compare runs)"
        print(f"\nTrades written to {trades_path}  -- {kind}")

    cov = coverage_table(trades_df, skipped_df)
    print("\nCoverage checklist (every eligible expiry):")
    for r in cov.itertuples(index=False):
        mark = "[x]" if r.status == "Traded" else "[ ]"
        print(f"  {mark} {r.expiry}  {r.detail}")


if __name__ == "__main__":
    main()
