"""
run_backtest.py
==================================================
Command-line runner for the backtest engine.

    python run_backtest.py --strategy straddle --dte 45 --target-pct 50 --stop-pct 100 \
        --exit-dte 21 --start 2023-01-01 --end 2023-12-31

    python run_backtest.py --strategy strangle --param n=200 ...
    python run_backtest.py --strategy iron_fly --param w=300 ...
    python run_backtest.py --strategy iron_condor --param sd=30 --param wd=10 ...

--param key=value sets one of the strategy's own parameters (n, w, sd, wd -- see
--list-strategies); a strategy's params default sensibly if you leave them out.

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
from backtest_engine.strategies import (STRATEGIES, build_legs, default_params, default_rule, label,
                                        validate_params)

ROOT = pathlib.Path(__file__).resolve().parent


def _print_strategies():
    for key, s in STRATEGIES.items():
        print(f"{key:12s} {label(key)}")
        if s["params"]:
            for spec in s["params"]:
                print(f"             --param {spec['key']}=VALUE   {spec['label']} (default {spec['default']})")
        print(f"             {s['brief']}")
        print()


def _parse_param(text):
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"--param must be key=value, got {text!r}")
    key, value = text.split("=", 1)
    return key.strip(), float(value)


def _parse_args():
    p = argparse.ArgumentParser(
        description="Sell (and, for the iron strategies, buy) a set of options N days before "
                    "expiry; exit at X%% of the net credit (profit target) or Y%% of it as a loss "
                    "(stop), else at --exit-dte, replayed across monthly expiries."
    )
    p.add_argument("--list-strategies", action="store_true", help="print every strategy and its params, then exit")
    p.add_argument("--strategy", choices=list(STRATEGIES), default="straddle")
    p.add_argument("--param", action="append", default=[], type=_parse_param, metavar="KEY=VALUE",
                   help="set a strategy param (repeatable), e.g. --param sd=30 --param wd=10")
    p.add_argument("--dte", type=int, default=45, help="calendar days before expiry to enter (default 45)")
    p.add_argument("--target-pct", type=float, default=None,
                   help="profit target: %% of the net credit collected, i.e. of max profit "
                        "(default 50; 33 for iron_fly)")
    p.add_argument("--stop-pct", type=float, default=None,
                   help="stop loss: %% of the net credit collected, taken as a loss "
                        "(default 100; 50 for iron_fly)")
    p.add_argument("--lot-size", type=int, default=65, help="contract multiplier (default 65)")
    p.add_argument("--exit-dte", type=int, default=None,
                   help="time exit: calendar days before expiry to close if no target/stop hit "
                        "(default 21; 15 for iron_fly, 10 for iron_condor)")
    p.add_argument("--entry-time", default="15:15",
                   help="HH:MM time of day to open the trade and to time-exit at: the first tick at/after it "
                        "(default 15:15; use 09:20 for the market open)")
    p.add_argument("--start", type=datetime.date.fromisoformat, default=None,
                   help="first expiry date to include, YYYY-MM-DD (default: earliest in the data)")
    p.add_argument("--end", type=datetime.date.fromisoformat, default=None,
                   help="last expiry date to include, YYYY-MM-DD (default: latest in the data)")
    p.add_argument("--db", default=str(ROOT / "data" / "market_data.duckdb"), help="path to the DuckDB file")
    p.add_argument("--out-dir", default=str(ROOT / "results"),
                   help="results root; full runs go to <root>/<strategy>, shorter runs to <root>/<strategy>_short_runs")
    return p.parse_args()


def main():
    args = _parse_args()

    if args.list_strategies:
        _print_strategies()
        return

    try:
        entry_time = datetime.time.fromisoformat(args.entry_time).strftime("%H:%M:%S")
    except ValueError:
        sys.exit(f"--entry-time must look like HH:MM, got {args.entry_time!r}.")
    default_target, default_stop, default_exit = default_rule(args.strategy)
    if args.target_pct is None:
        args.target_pct = default_target
    if args.stop_pct is None:
        args.stop_pct = default_stop
    if args.exit_dte is None:
        args.exit_dte = default_exit

    params = default_params(args.strategy)
    valid_keys = set(params)
    for key, value in args.param:
        if key not in valid_keys:
            sys.exit(f"'{key}' is not a param of --strategy {args.strategy}. "
                     f"Valid params: {sorted(valid_keys) or '(none)'}. Try --list-strategies.")
        params[key] = int(value) if value.is_integer() else value
    err = validate_params(args.strategy, params)
    if err:
        sys.exit(f"Invalid params for --strategy {args.strategy}: {err}")

    db_file = pathlib.Path(args.db)
    if not db_file.is_file():
        sys.exit(f"Database not found: {db_file}")
    db_path = str(db_file.resolve())

    expiries = get_available_expiries(db_path)
    start = args.start or pd.to_datetime(min(expiries)).date()
    end = args.end or pd.to_datetime(max(expiries)).date()
    if start > end:
        sys.exit(f"--start ({start}) is after --end ({end}).")

    param_desc = ", ".join(f"{k}={v}" for k, v in params.items())
    print(f"Rule: {label(args.strategy)}" + (f" ({param_desc})" if param_desc else "") +
          f", enter {args.dte} DTE, target {args.target_pct:g}% / stop {args.stop_pct:g}% of net credit, "
          f"time exit {args.exit_dte} DTE, trade time {entry_time[:5]}, lot size {args.lot_size}")
    print(f"Sweep: monthly expiries from {start} to {end}")
    print("Running (each expiry takes a couple of seconds to load)...\n")

    legs = build_legs(args.strategy, params)
    trades_df, summary, skipped_df = run_backtest(
        db_path, args.dte, args.target_pct, args.stop_pct, args.lot_size, start, end, args.exit_dte, legs, entry_time
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
                                     params, args.target_pct, args.stop_pct, args.exit_dte, start, end,
                                     entry_time)
        kind = "FULL run (counts in Compare runs)" if full else "short run (not in Compare runs)"
        print(f"\nTrades written to {trades_path}  -- {kind}")

    cov = coverage_table(trades_df, skipped_df)
    print("\nCoverage checklist (every eligible expiry):")
    for r in cov.itertuples(index=False):
        mark = "[x]" if r.status == "Traded" else "[ ]"
        print(f"  {mark} {r.expiry}  {r.detail}")


if __name__ == "__main__":
    main()
