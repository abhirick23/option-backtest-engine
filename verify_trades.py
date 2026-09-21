"""
verify_trades.py
==================================================
Independent checker for a backtest trades CSV. It does NOT use the engine or
the DuckDB file: it re-reads data/raw_parquet/*.parquet and replays every
trade tick by tick, then reports PASS/FAIL per trade.

    python verify_trades.py results/backtest_straddle_45dte_t50_s100_exit21_....csv

The rule parameters (dte, n, target %, stop %, exit dte, lot size) are parsed
from the file name; override with --dte/--n/--target-pct/--stop-pct/--exit-dte/
--lot-size. Works for both strategies: n is 0 for the straddle, and the
strangle's call/put strikes are re-derived as the listed strikes nearest to
ATM+n / ATM-n.

Per trade it checks:
  1. entry tick  = first tick on/after (expiry - dte days)
  2. strikes     = ATM is the listed strike nearest to spot at that tick; call
                   is nearest to ATM+n, put nearest to ATM-n
  3. entry call/put/credit match the raw prices
  4. exit tick   = the FIRST tick where combined <= target level or >= stop
                   level; if none, the first tick on/after (expiry - exit_dte)
  5. exit call/put match the raw prices at that tick
  6. P&L         = (credit - exit combined) * lot size
It also reports the best/worst the combined premium got as a % of the credit,
which shows how close the trade came to the profit target / stop.
Per-trade tick paths are written to results/verify/<expiry>.csv.
"""

import argparse
import pathlib
import re
import sys

import duckdb
import pandas as pd

from backtest_engine.data import _date_norm_sql

ROOT = pathlib.Path(__file__).resolve().parent
PARQUET = (ROOT / "data" / "raw_parquet" / "*.parquet").as_posix()
TOL = 0.011  # rupee tolerance for price / P&L comparisons (CSV is rounded to 2dp)


def _params_from_name(name):
    m = re.search(r"(\d+)dte(?:_n(\d+))?_t([\d.]+)_s([\d.]+)_exit(\d+)", name)
    if not m:
        return {}
    return dict(dte=int(m[1]), n=int(m[2] or 0), target_pct=float(m[3]), stop_pct=float(m[4]),
                exit_dte=int(m[5]))


def _args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv")
    p.add_argument("--dte", type=int)
    p.add_argument("--n", type=int)
    p.add_argument("--target-pct", type=float)
    p.add_argument("--stop-pct", type=float)
    p.add_argument("--exit-dte", type=int)
    p.add_argument("--lot-size", type=int, default=65)
    a = p.parse_args()
    d = _params_from_name(pathlib.Path(a.csv).name)
    for k in ("dte", "target_pct", "stop_pct", "exit_dte"):
        if getattr(a, k) is None:
            setattr(a, k, d.get(k))
        if getattr(a, k) is None:
            sys.exit(f"Could not read --{k.replace('_', '-')} from the file name; pass it explicitly.")
    if a.n is None:
        a.n = d.get("n", 0)
    return a


def _norm_time(col):
    return (f"CASE WHEN length(trim({col})) = 5 THEN trim({col}) || ':00' "
            f"ELSE substr(trim({col}), 1, 8) END")


def main():
    a = _args()
    trades = pd.read_csv(a.csv)
    if "call_strike" not in trades:  # older straddle files have one `strike` column
        trades["call_strike"] = trades["put_strike"] = trades["atm_strike"] = trades["strike"]
    for c in ("call_strike", "put_strike", "atm_strike"):
        trades[c] = trades[c].astype(int)
    con = duckdb.connect()
    con.register("trades", trades[["expiry", "call_strike", "put_strike"]])

    exp_n, date_n, time_n = _date_norm_sql("expiry"), _date_norm_sql("pulled_date"), _norm_time("pulled_time")
    base = f"""
        SELECT {exp_n} AS expiry, {date_n} AS d, {time_n} AS t,
               CAST(strike_price AS INTEGER) AS strike,
               spot_price AS spot, call_ltp AS ce, put_ltp AS pe
        FROM read_parquet('{PARQUET}')
    """
    print("Reading raw parquet (one pass)...")
    # ticks for each trade's own strike (the replay path)
    con.execute(f"""
        CREATE TABLE path AS
        SELECT r.* FROM ({base}) r
        JOIN (SELECT CAST(expiry AS DATE) AS expiry, call_strike AS strike FROM trades
              UNION
              SELECT CAST(expiry AS DATE), put_strike FROM trades) x
          ON r.expiry = x.expiry AND r.strike = x.strike
    """)
    # every strike listed at each trade's entry date (to re-derive ATM)
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
        # call leg from the call strike's rows, put leg from the put strike's rows
        legs_c = raw[raw["strike"] == tr.call_strike][["dt", "d", "ce"]].drop_duplicates("dt")
        legs_p = raw[raw["strike"] == tr.put_strike][["dt", "pe"]].drop_duplicates("dt")
        p = legs_c.merge(legs_p, on="dt").dropna(subset=["ce", "pe"]).sort_values("dt")
        p["combined"] = p["ce"] + p["pe"]
        errs = []

        # 1. entry tick: first tick on/after expiry - dte, from the raw data
        entry_date = (pd.Timestamp(exp) - pd.Timedelta(days=a.dte)).date()
        ec = con.execute("SELECT * FROM entry_chain WHERE expiry = ? ORDER BY t", [exp]).df()
        if ec.empty:
            rows.append(dict(expiry=tr.expiry, result="FAIL", problems="no raw ticks at entry date"))
            n_fail += 1
            continue
        ec["d"] = pd.to_datetime(ec["d"]).dt.strftime("%Y-%m-%d")
        first_t = ec["t"].min()
        exp_entry_dt = f"{ec['d'].iloc[0]} {first_t}"
        if tr.entry_datetime != exp_entry_dt:
            errs.append(f"entry tick {tr.entry_datetime} != expected {exp_entry_dt}")

        # 2. ATM, then call/put strikes, from the full chain at that tick
        snap = ec[ec["t"] == first_t]
        spot0 = float(snap["spot"].iloc[0])
        listed = snap["strike"].astype(int)
        nearest = lambda target: int(listed.iloc[(listed - target).abs().argmin()])
        atm = nearest(spot0)
        exp_call, exp_put = nearest(atm + a.n), nearest(atm - a.n)
        if tr.atm_strike != atm:
            errs.append(f"atm strike {tr.atm_strike} != nearest-to-spot {atm} (spot {spot0})")
        if tr.call_strike != exp_call or tr.put_strike != exp_put:
            errs.append(f"strikes csv call {tr.call_strike}/put {tr.put_strike} vs expected "
                        f"call {exp_call}/put {exp_put} (n={a.n})")

        # 3. entry prices
        e = p[p["dt"] == tr.entry_datetime]
        if e.empty:
            errs.append("entry tick missing for this strike in raw data")
            rows.append(dict(expiry=tr.expiry, result="FAIL", problems="; ".join(errs)))
            n_fail += 1
            continue
        e = e.iloc[0]
        if abs(e["ce"] - tr.entry_ce) > TOL or abs(e["pe"] - tr.entry_pe) > TOL:
            errs.append(f"entry prices csv {tr.entry_ce}/{tr.entry_pe} vs raw {e['ce']}/{e['pe']}")
        credit = float(e["ce"] + e["pe"])
        if abs(credit - tr.credit) > TOL:
            errs.append(f"credit csv {tr.credit} vs raw {credit:.2f}")

        # 4. replay from raw: first crossing, else time exit
        target_lvl = credit * (1 - a.target_pct / 100.0)
        stop_lvl = credit * (1 + a.stop_pct / 100.0)
        tx_date = (pd.Timestamp(exp) - pd.Timedelta(days=a.exit_dte)).date()
        later = p[p["dt"] > tr.entry_datetime]
        tx = later[later["d"] >= str(tx_date)]
        if tx.empty:
            errs.append("raw data ends before the time-exit date -- trade is incomplete")
            rows.append(dict(expiry=tr.expiry, result="FAIL", problems="; ".join(errs)))
            n_fail += 1
            continue
        tx_dt = tx["dt"].iloc[0]  # first tick on/after the time-exit date (with a price)
        walk = later[later["dt"] <= tx_dt]
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
            exp_exit_dt, exp_reason = tx_dt, f"Time Exit ({a.exit_dte} DTE)"
        if tr.exit_datetime != exp_exit_dt or tr.exit_reason != exp_reason:
            errs.append(f"exit csv {tr.exit_datetime} [{tr.exit_reason}] vs raw replay {exp_exit_dt} [{exp_reason}]")

        # 5. exit prices + 6. pnl
        x = p[p["dt"] == tr.exit_datetime]
        if x.empty:
            errs.append("exit tick missing in raw data")
        else:
            x = x.iloc[0]
            if abs(x["ce"] - tr.exit_ce) > TOL or abs(x["pe"] - tr.exit_pe) > TOL:
                errs.append(f"exit prices csv {tr.exit_ce}/{tr.exit_pe} vs raw {x['ce']}/{x['pe']}")
            pnl = (credit - float(x["ce"] + x["pe"])) * a.lot_size
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
        print(f"Lowest the combined premium ever got across all trades: {rep['lowest_pct_of_credit'].min():.1f}% of credit")
    rep.to_csv(out_dir / "_report.csv", index=False)
    print(f"Per-trade paths + report written to {out_dir}")


if __name__ == "__main__":
    main()
