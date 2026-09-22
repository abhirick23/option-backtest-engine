# Nifty Option Backtest

A mechanical short-option backtest over a local DuckDB archive of Nifty
option-chain snapshots (5-minute ticks), with a Streamlit UI, a CLI and an
independent result checker. Four strategies share one engine:

| # | Strategy | Legs |
|---|---|---|
| 1 | **Short Straddle** | sell ATM call + sell ATM put |
| 2 | **Short Strangle** | sell call at ATM **+ n**, sell put at ATM **- n** |
| 3 | **Iron Fly** | Strategy 1 + buy call at ATM **+ w**, buy put at ATM **- w** |
| 4 | **Iron Condor** | Strategy 2 + buy call at ATM **+ var**, buy put at ATM **- var** (`var > n`) |

`n`, `w`, `var` are index points and snap to the nearest listed strike. `n = 0`
makes the strangle identical to the straddle. New strategies are added in
`backtest_engine/strategies.py` -- one entry with a leg list and its own
params, nothing else needs to change.

## The rule

For every **monthly** expiry in the chosen range:

1. **Entry** -- `dte` calendar days before expiry, at the first available tick,
   put on the strategy's legs (ATM = strike nearest spot at that tick; each
   leg's own strike is the listed strike nearest ATM + its offset).
2. **Max profit** is the **net credit**: what the sold legs bring in minus what
   any bought legs (the iron strategies' wings) cost. Exit at whichever comes
   first, checked on the position's combined value every tick:
   - falls to `net_credit * (1 - target%)` -> **Profit Target**
   - rises to `net_credit * (1 + stop%)` -> **Stop Loss**
   - neither by `exit-dte` days before expiry -> **Time Exit**
3. **Skip** an expiry if the data doesn't reach the entry point, the time-exit
   date, or a usable price for every leg. Skips are reported in the coverage
   checklist, never silently dropped.

One set of legs is traded per expiry; expiries are independent trials, not a
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
It first asks which strategy you are testing (the ❗ About box explains it),
then shows that strategy's own parameters (n / w / var) in the sidebar. The
home page runs a backtest; **Compare runs** compares the saved full runs of
the strategy you picked.

**CLI**

```
.venv\Scripts\python run_backtest.py --strategy straddle --dte 45 --target-pct 50 --stop-pct 100
.venv\Scripts\python run_backtest.py --strategy strangle --param n=200 ...
.venv\Scripts\python run_backtest.py --strategy iron_fly --param w=300 ...
.venv\Scripts\python run_backtest.py --strategy iron_condor --param n=200 --param var=500 ...
.venv\Scripts\python run_backtest.py --list-strategies   # every strategy, its params and a brief
```

| option | default | meaning |
|---|---|---|
| `--strategy` | straddle | `straddle`, `strangle`, `iron_fly` or `iron_condor` |
| `--param key=value` | (per strategy) | set one of the strategy's own params; repeatable |
| `--dte` | 45 | days before expiry to enter |
| `--exit-dte` | 21 | time exit, days before expiry (must be < `--dte`) |
| `--target-pct` | 50 | profit target, % of the net credit (max profit) |
| `--stop-pct` | 100 | stop loss, % of the net credit taken as a loss |
| `--lot-size` | 65 | contract multiplier |
| `--start` / `--end` | whole archive | bounds on the **expiry** date, `YYYY-MM-DD` |
| `--db` | `data/market_data.duckdb` | DuckDB file |
| `--out-dir` | `results/` | results root |

**Where results go**

- A run covering **every expiry in the data** is a *full run* ->
  `results/<strategy>/` (what the Compare runs page shows for that strategy).
- Any narrower run -> `results/<strategy>_short_runs/` (saved, not compared).
- File names carry the settings, e.g.
  `backtest_iron_condor_45dte_n200_var500_t50_s100_exit21_<start>_<end>.csv`.
- Every run prints/shows a **coverage checklist**: each eligible expiry ticked if
  traded, or unticked with the skip reason (e.g. data ends before the time exit).
- Each trade row has `atm_strike` plus, for every leg the strategy uses,
  `<leg>_strike` / `<leg>_entry` / `<leg>_exit` (leg names: `short_call`,
  `short_put`, and for the iron strategies `long_call`, `long_put`).

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
    engine.py              the rule: simulate_trade, run_backtest (leg-list based)
    data.py                read-only DuckDB access
    payoff.py              leg P&L math
    storage.py             full/short run folders, coverage checklist
    strategies.py          strategy registry (names, briefs, legs, params, folders)
```

## Notes

- Ticks are 5-minute LTP snapshots (75 per trading day, 09:20-15:30), so a fast
  move can jump past a stop/target level and fill at the first tick beyond it.
- The database file is opened read-only; close any DB viewer that has it open.
