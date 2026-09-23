"""
verify_trades.py
==================================================
Independent checker for a backtest trades CSV. It does NOT use the engine or
the DuckDB file: it re-reads data/raw_parquet/*.parquet and replays every
trade tick by tick, then reports PASS/FAIL per trade.

    python verify_trades.py results/straddle/backtest_straddle_45dte_t50_s100_exit21_....csv
    python verify_trades.py results/iron_condor/backtest_iron_condor_45dte_sd30_wd10_...csv

The strategy and its rule parameters (dte, n/w/sd/wd, target %, stop %, exit dte)
are parsed from the file name (see backtest_engine/storage.py); override any of
them with --strategy/--dte/--param key=value/--target-pct/--stop-pct/--exit-dte/
--lot-size. Works for every strategy in backtest_engine/strategies.py -- it
rebuilds the same leg list (short_call, short_put, and for the iron strategies
long_call, long_put) and re-derives each leg's strike independently.

Per trade it checks:
  1. entry tick   = on the first trading day on/after (expiry - dte days), the
                    first tick at/after the trade time (default 15:15, read from
                    the file name's `_at1515`; older runs = first tick of the day)
  2. strikes      = ATM is the listed strike nearest to spot at that tick; each
                    leg's strike is the listed strike nearest ATM + its offset,
                    or (iron condor) the strike whose |delta| at that tick is
                    closest to the leg's target delta among strikes with a
                    traded price (LTP > 0)
  3. entry prices + net credit match the raw prices
  4. exit tick    = the FIRST tick where the combined position value (sum of
                    sold legs minus sum of bought legs) crosses the target or
                    stop level; if neither, the last priced tick up to the
                    time-exit tick (same trade time, on the time-exit day)
  5. exit prices match the raw prices at that tick
  6. P&L          = sum of each leg's (Sell: entry-exit, Buy: exit-entry) * lot
It also reports the best/worst the combined position value got as a % of the
credit, which shows how close the trade came to the profit target / stop.
Per-trade tick paths are written to results/verify/<csv name>/<expiry>.csv.
"""

import argparse
import pathlib
import sys

import duckdb
import pandas as pd

from backtest_engine.data import _date_norm_sql
from backtest_engine.storage import parse_run_name
from backtest_engine.strategies import STRATEGIES, build_legs

ROOT = pathlib.Path(__file__).resolve().parent
PARQUET = (ROOT / "data" / "raw_parquet" / "*.parquet").as_posix()
TOL = 0.011  # rupee tolerance for price / P&L comparisons (CSV is rounded to 2dp)


def _parse_param(text):
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"--param must be key=value, got {text!r}")
    key, value = text.split("=", 1)
    return key.strip(), float(value)


def _args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv")
    p.add_argument("--strategy", choices=list(STRATEGIES))
    p.add_argument("--param", action="append", default=[], type=_parse_param, metavar="KEY=VALUE")
    p.add_argument("--dte", type=int)
    p.add_argument("--target-pct", type=float)
    p.add_argument("--stop-pct", type=float)
    p.add_argument("--exit-dte", type=int)
    p.add_argument("--lot-size", type=int, default=65)
    p.add_argument("--entry-time", help="HH:MM the run traded at; omit for older runs that used the "
                                        "first tick of the day (09:20). Read from the file name if present.")
    a = p.parse_args()

    d = parse_run_name(pathlib.Path(a.csv).stem) or {}
    at = a.entry_time or d.get("entry_time")
    a.entry_time = f"{at}:00" if at else None  # "HH:MM:SS", or None = first tick of the day
    if a.strategy is None:
        a.strategy = d.get("strategy")
    if a.strategy is None:
        sys.exit("Could not read --strategy from the file name; pass it explicitly.")
    for k in ("dte", "target_pct", "stop_pct", "exit_dte"):
        if getattr(a, k) is None:
            setattr(a, k, d.get(k))
        if getattr(a, k) is None:
            sys.exit(f"Could not read --{k.replace('_', '-')} from the file name; pass it explicitly.")

    params = dict(d.get("params", {}))
    for key, value in a.param:
        params[key] = int(value) if value.is_integer() else value
    missing = [spec["key"] for spec in STRATEGIES[a.strategy]["params"] if spec["key"] not in params]
    if missing:
        sys.exit(f"Missing params for --strategy {a.strategy}: {missing}. Pass --param key=value.")
    a.params = params
    return a


def _pick_tick(times, at_time):
    """From one day's sorted tick times: the first at/after at_time ("HH:MM:SS"),
    else the day's last tick; at_time None -> the day's first tick."""
    if at_time is None:
        return times[0]
    return next((t for t in times if t >= at_time), times[-1])


def _norm_time(col):
    return (f"CASE WHEN length(trim({col})) = 5 THEN trim({col}) || ':00' "
            f"ELSE substr(trim({col}), 1, 8) END")


def _load_trades(csv_path, legs):
    """The trades CSV, with every active leg's strike/entry/exit column
    present -- mapped from whichever older column scheme the file used, if
    any (pre-multi-leg straddle/strangle files)."""
    trades = pd.read_csv(csv_path)
    if "short_call_strike" not in trades and "call_strike" in trades:
        # straddle/strangle files saved before the iron strategies existed
        trades = trades.rename(columns={
            "call_strike": "short_call_strike", "put_strike": "short_put_strike",
            "entry_ce": "short_call_entry", "entry_pe": "short_put_entry",
            "exit_ce": "short_call_exit", "exit_pe": "short_put_exit",
        })
    if "short_call_strike" not in trades and "strike" in trades:
        # the original single-strike straddle files
        if "atm_strike" not in trades:
            trades["atm_strike"] = trades["strike"]
        for name in ("short_call", "short_put"):
            trades[f"{name}_strike"] = trades["strike"]
        trades["short_call_entry"] = trades["entry_ce"]
        trades["short_put_entry"] = trades["entry_pe"]
        trades["short_call_exit"] = trades["exit_ce"]
        trades["short_put_exit"] = trades["exit_pe"]
    for leg in legs:
        for suffix in ("strike", "entry", "exit"):
            col = f"{leg['name']}_{suffix}"
            if col not in trades:
                sys.exit(f"Trades CSV is missing column '{col}' for this strategy's legs.")
    return trades


def main():
    a = _args()
    legs = build_legs(a.strategy, a.params)
    names = [leg["name"] for leg in legs]
    price_col = {leg["name"]: ("ce" if leg["opt_type"] == "CE" else "pe") for leg in legs}
    trades = _load_trades(a.csv, legs)
    strike_cols = [f"{name}_strike" for name in names]
    for c in strike_cols + ["atm_strike"]:
        trades[c] = trades[c].astype(int)

    con = duckdb.connect()
    # every (expiry, strike) this run actually traded, across all its legs
    strikes_long = pd.concat(
        [trades[["expiry", c]].rename(columns={c: "strike"}) for c in strike_cols], ignore_index=True
    ).drop_duplicates()
    con.register("strikes", strikes_long)
    con.register("trades", trades[["expiry"] + strike_cols])

    exp_n, date_n, time_n = _date_norm_sql("expiry"), _date_norm_sql("pulled_date"), _norm_time("pulled_time")
    base = f"""
        SELECT {exp_n} AS expiry, {date_n} AS d, {time_n} AS t,
               CAST(strike_price AS INTEGER) AS strike,
               spot_price AS spot, call_ltp AS ce, put_ltp AS pe,
               call_delta AS cd, put_delta AS pdl
        FROM read_parquet('{PARQUET}')
    """
    print("Reading raw parquet (one pass)...")
    # ticks for each trade's own strikes (the replay path)
    con.execute(f"""
        CREATE TABLE path AS
        SELECT r.* FROM ({base}) r
        JOIN (SELECT CAST(expiry AS DATE) AS expiry, strike FROM strikes) x
          ON r.expiry = x.expiry AND r.strike = x.strike
    """)
    # every strike listed at each trade's entry date (to re-derive ATM + leg strikes)
    con.execute("""
        CREATE TABLE entry_dates AS
        SELECT CAST(t.expiry AS DATE) AS expiry,
               CAST(min(p.d) FILTER (WHERE p.d >= CAST(t.expiry AS DATE) - INTERVAL (?) DAY) AS DATE) AS d
        FROM trades t JOIN path p ON p.expiry = CAST(t.expiry AS DATE)
        GROUP BY t.expiry
    """, [a.dte])
    con.execute(f"""
        CREATE TABLE entry_chain AS
        SELECT r.* FROM ({base}) r JOIN entry_dates e ON r.expiry = e.expiry AND r.d = e.d
    """)

    out_dir = ROOT / "results" / "verify" / pathlib.Path(a.csv).stem
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, n_fail = [], 0
    for tr in trades.itertuples(index=False):
        exp = pd.to_datetime(tr.expiry).date()
        raw = con.execute("SELECT * FROM path WHERE expiry = ? ORDER BY d, t", [exp]).df()
        raw["d"] = pd.to_datetime(raw["d"]).dt.strftime("%Y-%m-%d")
        raw["dt"] = raw["d"] + " " + raw["t"]
        errs = []

        # merge each active leg's price series (from its own strike's rows) on tick
        p = None
        for leg in legs:
            strike = getattr(tr, f"{leg['name']}_strike")
            leg_df = raw[raw["strike"] == strike][["dt", "d", price_col[leg["name"]]]].drop_duplicates("dt")
            leg_df = leg_df.rename(columns={price_col[leg["name"]]: leg["name"]})
            p = leg_df if p is None else p.merge(leg_df.drop(columns="d"), on="dt")
        p = p.dropna(subset=names).sort_values("dt")
        signed = lambda row: sum(row[l["name"]] if l["side"] == "Sell" else -row[l["name"]] for l in legs)
        p["combined"] = p.apply(signed, axis=1) if not p.empty else pd.Series(dtype=float)

        # 1. entry tick: first tick on/after expiry - dte, from the raw data
        ec = con.execute("SELECT * FROM entry_chain WHERE expiry = ? ORDER BY t", [exp]).df()
        if ec.empty:
            rows.append(dict(expiry=tr.expiry, result="FAIL", problems="no raw ticks at entry date"))
            n_fail += 1
            continue
        ec["d"] = pd.to_datetime(ec["d"]).dt.strftime("%Y-%m-%d")
        first_t = _pick_tick(sorted(ec["t"].unique()), a.entry_time)
        exp_entry_dt = f"{ec['d'].iloc[0]} {first_t}"
        if tr.entry_datetime != exp_entry_dt:
            errs.append(f"entry tick {tr.entry_datetime} != expected {exp_entry_dt}")

        # 2. ATM, then each leg's strike, from the full chain at that tick
        snap = ec[ec["t"] == first_t].sort_values("strike")
        spot0 = float(snap["spot"].iloc[0])
        listed = snap["strike"].astype(int)
        nearest = lambda target: int(listed.iloc[(listed - target).abs().argmin()])
        atm = nearest(spot0)
        if tr.atm_strike != atm:
            errs.append(f"atm strike {tr.atm_strike} != nearest-to-spot {atm} (spot {spot0})")
        for leg in legs:
            csv_strike = getattr(tr, f"{leg['name']}_strike")
            if "delta" in leg:
                dcol, pcol = ("cd", "ce") if leg["opt_type"] == "CE" else ("pdl", "pe")
                tradable = snap[snap[pcol] > 0]  # only strikes that have a traded price
                deltas = tradable.set_index("strike")[dcol].dropna().abs()
                exp_strike = int((deltas - leg["delta"]).abs().idxmin())
                how = f"target |delta| {leg['delta']:g}"
                if csv_strike == exp_strike and hasattr(tr, f"{leg['name']}_delta"):
                    raw_delta, csv_delta = float(deltas[exp_strike]), getattr(tr, f"{leg['name']}_delta")
                    if abs(raw_delta - csv_delta) > 0.0002:
                        errs.append(f"{leg['name']} delta csv {csv_delta} vs raw {raw_delta:.4f}")
            else:
                exp_strike = nearest(atm + leg["offset"])
                how = f"offset {leg['offset']:+d}"
            if csv_strike != exp_strike:
                errs.append(f"{leg['name']} strike {csv_strike} != expected {exp_strike} ({how})")

        # 3. entry prices + net credit
        e = p[p["dt"] == tr.entry_datetime]
        if e.empty:
            errs.append("entry tick missing for one or more legs in raw data")
            rows.append(dict(expiry=tr.expiry, result="FAIL", problems="; ".join(errs)))
            n_fail += 1
            continue
        e = e.iloc[0]
        for leg in legs:
            csv_price = getattr(tr, f"{leg['name']}_entry")
            if abs(e[leg["name"]] - csv_price) > TOL:
                errs.append(f"{leg['name']} entry csv {csv_price} vs raw {e[leg['name']]}")
        credit = float(e["combined"])
        if abs(credit - tr.credit) > TOL:
            errs.append(f"credit csv {tr.credit} vs raw {credit:.2f}")

        # 4. replay from raw: first crossing, else time exit
        target_lvl = credit * (1 - a.target_pct / 100.0)
        stop_lvl = credit * (1 + a.stop_pct / 100.0)
        tx_date = (pd.Timestamp(exp) - pd.Timedelta(days=a.exit_dte)).date()
        later = p[p["dt"] > tr.entry_datetime]
        # time-exit tick: on the first trading day on/after tx_date, the first tick at/after the
        # trade time (else that day's last tick), taken from every raw tick of the traded strikes
        tx_days = sorted(d for d in raw["d"].unique() if d >= str(tx_date))
        if not tx_days:
            errs.append("raw data ends before the time-exit date -- trade is incomplete")
            rows.append(dict(expiry=tr.expiry, result="FAIL", problems="; ".join(errs)))
            n_fail += 1
            continue
        tx_dt = f"{tx_days[0]} {_pick_tick(sorted(raw.loc[raw['d'] == tx_days[0], 't'].unique()), a.entry_time)}"
        walk = later[later["dt"] <= tx_dt]
        if walk.empty:
            errs.append("no priced tick between entry and the time-exit tick")
            rows.append(dict(expiry=tr.expiry, result="FAIL", problems="; ".join(errs)))
            n_fail += 1
            continue
        hit_t = walk[walk["combined"] <= target_lvl]
        hit_s = walk[walk["combined"] >= stop_lvl]
        cand = []
        if not hit_t.empty:
            cand.append((hit_t["dt"].iloc[0], "Profit Target"))
        if not hit_s.empty:
            cand.append((hit_s["dt"].iloc[0], "Stop Loss"))
        if cand:
            exp_exit_dt, exp_reason = min(cand)
        else:
            # no trigger: closed at the last tick up to the time-exit tick that had every leg priced
            exp_exit_dt, exp_reason = walk["dt"].iloc[-1], f"Time Exit ({a.exit_dte} DTE)"
        if tr.exit_datetime != exp_exit_dt or tr.exit_reason != exp_reason:
            errs.append(f"exit csv {tr.exit_datetime} [{tr.exit_reason}] vs raw replay {exp_exit_dt} [{exp_reason}]")

        # 5. exit prices + 6. pnl
        x = p[p["dt"] == tr.exit_datetime]
        if x.empty:
            errs.append("exit tick missing in raw data")
        else:
            x = x.iloc[0]
            for leg in legs:
                csv_price = getattr(tr, f"{leg['name']}_exit")
                if abs(x[leg["name"]] - csv_price) > TOL:
                    errs.append(f"{leg['name']} exit csv {csv_price} vs raw {x[leg['name']]}")
            def _leg_pnl(leg):
                entry_price, exit_price = getattr(tr, f"{leg['name']}_entry"), x[leg["name"]]
                diff = entry_price - exit_price if leg["side"] == "Sell" else exit_price - entry_price
                return diff * a.lot_size
            pnl = sum(_leg_pnl(leg) for leg in legs)
            if abs(pnl - tr.pnl) > 0.5:
                errs.append(f"pnl csv {tr.pnl} vs recomputed {pnl:.2f}")

        # path stats over the whole holding window (as % of credit)
        held = p[(p["dt"] >= tr.entry_datetime) & (p["dt"] <= tr.exit_datetime)].copy()
        held["pct_of_credit"] = (held["combined"] / credit * 100).round(1)
        held.to_csv(out_dir / f"{tr.expiry}.csv", index=False)
        low, high = held["pct_of_credit"].min(), held["pct_of_credit"].max()
        rows.append(dict(
            expiry=tr.expiry, result="PASS" if not errs else "FAIL",
            reason=tr.exit_reason, lowest_pct_of_credit=low, highest_pct_of_credit=high,
            problems="; ".join(errs),
        ))
        n_fail += bool(errs)

    rep = pd.DataFrame(rows)
    print(rep.to_string(index=False))
    print(f"\nChecked {len(rep)} trades: {len(rep) - n_fail} PASS, {n_fail} FAIL")
    print(f"Target needs combined <= {100 - a.target_pct:g}% of credit; stop needs >= {100 + a.stop_pct:g}%.")
    if "lowest_pct_of_credit" in rep:
        print(f"Lowest the combined position value ever got across all trades: "
              f"{rep['lowest_pct_of_credit'].min():.1f}% of credit")
    rep.to_csv(out_dir / "_report.csv", index=False)
    print(f"Per-trade paths + report written to {out_dir}")


if __name__ == "__main__":
    main()
