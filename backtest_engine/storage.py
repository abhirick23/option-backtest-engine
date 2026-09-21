"""
backtest_engine/storage.py
==================================================
Where a run's trades CSV is saved, plus the per-expiry coverage checklist.

  results/<strategy>/             FULL runs only (the range covers every expiry
                                  in the data) -- these feed Compare runs.
  results/<strategy>_short_runs/  any narrower run -- kept, but not compared.

<strategy> is the registry folder, e.g. "straddle" or "strangle".
"""

import re

import pandas as pd

from .strategies import STRATEGIES

# n appears in the name only for strategies that take it
_NAME = re.compile(
    r"backtest_(?P<strategy>\w+?)_(?P<dte>\d+)dte(?:_n(?P<n>\d+))?_t(?P<t>[\d.]+)_s(?P<s>[\d.]+)"
    r"_exit(?P<x>\d+)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})"
)


def full_dir(strategy):
    return STRATEGIES[strategy]["folder"]


def short_dir(strategy):
    return f"{STRATEGIES[strategy]['folder']}_short_runs"


def is_full_run(start, end, expiries):
    """True if [start, end] covers every expiry available in the data."""
    first = pd.to_datetime(min(expiries)).date()
    last = pd.to_datetime(max(expiries)).date()
    return start <= first and end >= last


def run_stem(strategy, dte, target_pct, stop_pct, exit_dte, start, end, n=0):
    n_part = f"_n{n}" if STRATEGIES[strategy]["has_n"] else ""
    return f"backtest_{strategy}_{dte}dte{n_part}_t{target_pct:g}_s{stop_pct:g}_exit{exit_dte}_{start}_{end}"


def parse_run_name(stem):
    """The settings encoded in a saved run's file name, or None."""
    m = _NAME.search(stem)
    if not m:
        return None
    d = m.groupdict()
    return {
        "strategy": d["strategy"], "dte": int(d["dte"]), "n": int(d["n"]) if d["n"] else 0,
        "target_pct": float(d["t"]), "stop_pct": float(d["s"]), "exit_dte": int(d["x"]),
        "start": d["start"], "end": d["end"],
    }


def run_label(info):
    n_part = f" | n={info['n']}" if STRATEGIES[info["strategy"]]["has_n"] else ""
    return (f"{info['dte']}→{info['exit_dte']} DTE{n_part} | target {info['target_pct']:g}% | "
            f"stop {info['stop_pct']:g}% | {info['start']} to {info['end']}")


def save_run(strategy, trades_df, results_root, expiries, dte, target_pct, stop_pct, exit_dte,
             start, end, n=0):
    """Write the trades CSV into the strategy's full-run or short-run folder.
    Returns (path, is_full)."""
    full = is_full_run(start, end, expiries)
    out_dir = results_root / (full_dir(strategy) if full else short_dir(strategy))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_stem(strategy, dte, target_pct, stop_pct, exit_dte, start, end, n)}.csv"
    trades_df.to_csv(path, index=False)
    return path, full


def coverage_table(trades_df, skipped_df):
    """One row per eligible expiry: Traded (with its exit) or Skipped (with
    the reason, e.g. data ends before the time-exit date)."""
    rows = []
    if not trades_df.empty:
        for r in trades_df.itertuples(index=False):
            rows.append({"expiry": r.expiry, "status": "Traded", "detail": r.exit_reason})
    if not skipped_df.empty:
        for r in skipped_df.itertuples(index=False):
            rows.append({"expiry": r.expiry, "status": "Skipped", "detail": r.reason})
    if not rows:
        return pd.DataFrame(columns=["expiry", "status", "detail"])
    return pd.DataFrame(rows).sort_values("expiry").reset_index(drop=True)
