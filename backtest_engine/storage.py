"""
backtest_engine/storage.py
==================================================
Where a run's trades CSV is saved, plus the per-expiry coverage checklist.

  results/straddle/             FULL runs only (the range covers every expiry in
                                the data) -- these feed the Compare runs page.
  results/straddle_short_runs/  any narrower run -- kept, but not compared.
"""

import pandas as pd

FULL_DIR = "straddle"
SHORT_DIR = "straddle_short_runs"


def is_full_run(start, end, expiries):
    """True if [start, end] covers every expiry available in the data."""
    first = pd.to_datetime(min(expiries)).date()
    last = pd.to_datetime(max(expiries)).date()
    return start <= first and end >= last


def run_stem(dte, target_pct, stop_pct, exit_dte, start, end):
    return f"backtest_straddle_{dte}dte_t{target_pct:g}_s{stop_pct:g}_exit{exit_dte}_{start}_{end}"


def save_run(trades_df, results_root, expiries, dte, target_pct, stop_pct, exit_dte, start, end):
    """Write the trades CSV into the full-run or short-run folder. Returns
    (path, is_full)."""
    full = is_full_run(start, end, expiries)
    out_dir = results_root / (FULL_DIR if full else SHORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_stem(dte, target_pct, stop_pct, exit_dte, start, end)}.csv"
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
