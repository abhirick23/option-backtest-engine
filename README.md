# Nifty Option Backtest

A mechanical short-option backtest over a local DuckDB archive of Nifty
option-chain snapshots (5-minute ticks), with a Streamlit UI, a CLI and an
independent result checker. Two strategies share one engine:

| # | Strategy | Legs sold |
|---|---|---|
| 1 | **Short Straddle** | ATM call + ATM put |
| 2 | **Short Strangle** | call at ATM **+ n**, put at ATM **- n** (`n` in index points, nearest listed strikes) |

`n = 0` makes the strangle identical to the straddle. New strategies are added
in `backtest_engine/strategies.py`.

## The rule

For every **monthly** expiry in the chosen range:

1. **Entry** -- `dte` calendar days before expiry, at the first available tick,
   sell the call and put (ATM = strike nearest spot at that tick; the strangle
   then shifts them by `n`).
2. **Max profit** is the credit collected (call + put premium). Exit at whichever
   comes first, checked on the combined premium every tick:
   - falls to `credit * (1 - target%)` -> **Profit Target**
   - rises to `credit * (1 + stop%)` -> **Stop Loss**
   - neither by `exit-dte` days before expiry -> **Time Exit**
3. **Skip** an expiry if the data doesn't reach the entry point or the time-exit
   date. Skips are reported in the coverage checklist, never silently dropped.

One straddle is traded per expiry; expiries are independent trials, not a
shared-capital portfolio. No fees, slippage or margin are modeled.

## Setup

```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**The data is not in this repo.** Put the option-chain database at
`data/market_data.duckdb` (table `option_chain_snapshots`, columns incl.
`pulled_date, pulled_time, expiry, spot_price, strike_price, call_ltp, put_ltp`).
To use `verify_trades.py` you also need the raw quarterly parquet files in
`data/raw_parquet/`. The `data/` and `results/` folders are git-ignored;
`results/` and its sub-folders are created automatically on the first run.

## Use it

**App** -- double-click `run_app.bat` (or `.venv\Scripts\streamlit run app.py`).
It first asks which strategy you are testing (the ❗ About box explains it). The
home page runs a backtest; **Compare runs** compares the saved full runs of the
strategy you picked.

**CLI**

```
.venv\Scripts\python run_backtest.py --strategy strangle --n 200 --dte 45 --exit-dte 21 --target-pct 50 --stop-pct 100
```

| option | default | meaning |
|---|---|---|
| `--strategy` | straddle | `straddle` or `strangle` |
| `--n` | 200 | strangle only: call = ATM+n, put = ATM-n (index points) |
| `--dte` | 45 | days before expiry to enter |
| `--exit-dte` | 21 | time exit, days before expiry (must be < `--dte`) |
| `--target-pct` | 50 | profit target, % of the credit (max profit) |
| `--stop-pct` | 100 | stop loss, % of the credit taken as a loss |
| `--lot-size` | 65 | contract multiplier |
| `--start` / `--end` | whole archive | bounds on the **expiry** date, `YYYY-MM-DD` |
| `--db` | `data/market_data.duckdb` | DuckDB file |
| `--out-dir` | `results/` | results root |

**Where results go**

- A run covering **every expiry in the data** is a *full run* ->
  `results/<strategy>/` (what the Compare runs page shows for that strategy).
- Any narrower run -> `results/<strategy>_short_runs/` (saved, not compared).
- File names carry the settings, e.g.
  `backtest_strangle_45dte_n200_t50_s100_exit21_<start>_<end>.csv`.
- Every run prints/shows a **coverage checklist**: each eligible expiry ticked if
  traded, or unticked with the skip reason (e.g. data ends before the time exit).

**Verify a run** (independent recompute from the raw parquet, no engine code):

```
.venv\Scripts\python verify_trades.py results\<strategy>\<file>.csv
```

## Layout

```
app.py                     Streamlit home: pick a strategy, run a backtest
pages/1_Compare_Runs.py    Streamlit: compare saved full runs of a strategy
app_common.py              shared strategy picker
run_app.bat                one-click launcher
run_backtest.py            command-line runner
verify_trades.py           independent result checker
backtest_engine/
    engine.py              the rule: simulate_trade, run_backtest
    data.py                read-only DuckDB access
    payoff.py              leg P&L math
    storage.py             full/short run folders, coverage checklist
    strategies.py          strategy registry (names, briefs, folders)
```

## Notes

- Ticks are 5-minute LTP snapshots (75 per trading day, 09:20-15:30), so a fast
  move can jump past a stop/target level and fill at the first tick beyond it.
- The database file is opened read-only; close any DB viewer that has it open.
