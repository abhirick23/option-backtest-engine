"""
pages/1_Compare_Runs.py
==================================================
Stats page: reads every saved straddle CSV in results/, lets you pick which
runs to look at, and compares them side by side.
"""

import pathlib

import pandas as pd
import streamlit as st

from app_common import pick_strategy
from backtest_engine.storage import full_dir, parse_run_name, run_label
from backtest_engine.strategies import STRATEGIES, label

ROOT = pathlib.Path(__file__).resolve().parent.parent

st.set_page_config(page_title="Compare runs", layout="wide")
st.title("Compare runs")

strategy = pick_strategy()
if strategy is None:
    st.info("Pick a strategy above to see its saved runs.")
    st.stop()

RESULTS = ROOT / "results" / full_dir(strategy)  # full runs only
st.caption(f"Full runs (all available data) of {label(strategy)}, saved in results/{full_dir(strategy)}/. "
           f"Shorter runs live in results/{full_dir(strategy)}_short_runs/ and are not compared here.")


@st.cache_data(show_spinner=False)
def _load(path_str, mtime):
    return pd.read_csv(path_str)


def _runs():
    runs = {}
    for f in sorted(RESULTS.glob("backtest_*.csv")):
        info = parse_run_name(f.stem)
        if info and info["strategy"] == strategy:
            runs[run_label(info)] = f
    return runs


def _stats(df):
    pnl = df["pnl"]
    cum = pnl.cumsum()
    reasons = df["exit_reason"].str.startswith
    return {
        "Trades": len(df),
        "Win rate %": round((pnl > 0).mean() * 100, 1),
        "Total P&L": round(pnl.sum()),
        "Avg P&L": round(pnl.mean()),
        "Best": round(pnl.max()),
        "Worst": round(pnl.min()),
        "Max drawdown": round((cum - cum.cummax()).min()),
        "Avg hold (d)": round(df["holding_days"].mean(), 1),
        "Target exits": int(reasons("Profit").sum()),
        "Stop exits": int(reasons("Stop").sum()),
        "Time exits": int(reasons("Time").sum()),
    }


runs = _runs()
if not runs:
    st.info(f"No full runs of {label(strategy)} yet. Run a backtest over all the data from the home page first.")
    st.stop()

chosen = st.multiselect("Runs", list(runs), default=list(runs)[-3:] if len(runs) > 3 else list(runs))
if not chosen:
    st.info("Select at least one run.")
    st.stop()

data = {lab: _load(str(runs[lab]), runs[lab].stat().st_mtime) for lab in chosen}

st.subheader("Summary")
summary = pd.DataFrame({lab: _stats(df) for lab, df in data.items()}).T
st.dataframe(summary, width="stretch")
st.caption("A run whose range reaches past the end of the data may include an incomplete last trade "
           "(files made before that was fixed). Re-run it to refresh.")

st.subheader("Cumulative P&L")
cum = pd.DataFrame({
    lab: pd.Series(df["pnl"].cumsum().values, index=pd.to_datetime(df["expiry"]))
    for lab, df in data.items()
}).sort_index().ffill()
st.line_chart(cum)

left, right = st.columns(2)
with left:
    st.subheader("P&L by year")
    by_year = pd.DataFrame({
        lab: df.assign(year=pd.to_datetime(df["expiry"]).dt.year).groupby("year")["pnl"].sum()
        for lab, df in data.items()
    })
    st.bar_chart(by_year)
with right:
    st.subheader("Exit reasons")
    reasons = pd.DataFrame({
        lab: df["exit_reason"].str.replace(r"\s*\(.*\)", "", regex=True).value_counts()
        for lab, df in data.items()
    }).fillna(0).astype(int)
    st.dataframe(reasons, width="stretch")

st.subheader("Trades")
pick = st.selectbox("Show trades for", chosen)
st.dataframe(data[pick], width="stretch", hide_index=True)
