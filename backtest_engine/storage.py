"""
backtest_engine/storage.py
==================================================
Where a run's trades CSV is saved, plus the per-expiry coverage checklist.

  results/<strategy>/             FULL runs only (the range covers every expiry
                                  in the data) -- these feed Compare runs.
  results/<strategy>_short_runs/  any narrower run -- kept, but not compared.

<strategy> is the registry folder, e.g. "straddle", "strangle", "iron_fly",
"iron_condor". A strategy's own parameters (n, w, var, ...) are encoded in
the file name in the order STRATEGIES[key]["params"] lists them, e.g.
`backtest_iron_condor_45dte_n200_var500_t50_s100_exit21_<start>_<end>.csv`.
"""

import re

import pandas as pd

from .strategies import STRATEGIES

_NAME = re.compile(
    r"backtest_(?P<strategy>[a-z]+(?:_[a-z]+)*?)_(?P<dte>\d+)dte(?P<params>(?:_[a-z]+[\d.]+)*)"
    r"_t(?P<t>[\d.]+)_s(?P<s>[\d.]+)_exit(?P<x>\d+)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})"
)
_PARAM_PART = re.compile(r"_([a-z]+)([\d.]+)")


def full_dir(strategy):
    return STRATEGIES[strategy]["folder"]


def short_dir(strategy):
    return f"{STRATEGIES[strategy]['folder']}_short_runs"


def is_full_run(start, end, expiries):
    """True if [start, end] covers every expiry available in the data."""
    first = pd.to_datetime(min(expiries)).date()
    last = pd.to_datetime(max(expiries)).date()
    return start <= first and end >= last


def run_stem(strategy, dte, params, target_pct, stop_pct, exit_dte, start, end):
    """params -- dict of the strategy's own parameters, e.g. {"n": 200}."""
    param_part = "".join(f"_{spec['key']}{params[spec['key']]:g}" for spec in STRATEGIES[strategy]["params"])
    return (f"backtest_{strategy}_{dte}dte{param_part}_t{target_pct:g}_s{stop_pct:g}"
            f"_exit{exit_dte}_{start}_{end}")


def parse_run_name(stem):
    """The settings encoded in a saved run's file name, or None."""
    m = _NAME.search(stem)
    if not m or m["strategy"] not in STRATEGIES:
        return None
    params = {k: (int(v) if float(v).is_integer() else float(v)) for k, v in _PARAM_PART.findall(m["params"])}
    return {
        "strategy": m["strategy"], "dte": int(m["dte"]), "params": params,
        "target_pct": float(m["t"]), "stop_pct": float(m["s"]), "exit_dte": int(m["x"]),
        "start": m["start"], "end": m["end"],
    }


def run_label(info):
    param_part = "".join(f" | {k}={v:g}" for k, v in info["params"].items())
    return (f"{info['dte']}→{info['exit_dte']} DTE{param_part} | target {info['target_pct']:g}% | "
            f"stop {info['stop_pct']:g}% | {info['start']} to {info['end']}")


def save_run(strategy, trades_df, results_root, expiries, dte, params, target_pct, stop_pct, exit_dte,
             start, end):
    """Write the trades CSV into the strategy's full-run or short-run folder.
    Returns (path, is_full)."""
    full = is_full_run(start, end, expiries)
    out_dir = results_root / (full_dir(strategy) if full else short_dir(strategy))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_stem(strategy, dte, params, target_pct, stop_pct, exit_dte, start, end)}.csv"
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
