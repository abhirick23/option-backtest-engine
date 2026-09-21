"""
pages/1_Compare_Runs.py
==================================================
Stats page: reads every saved straddle CSV in results/, lets you pick which
runs to look at, and compares them side by side.
"""

import pathlib
import re

import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "straddle"  # full runs only

st.set_page_config(page_title="Compare runs", layout="wide")
st.title("Compare runs")
st.caption("Full runs (all available data) saved in results/straddle/. Shorter runs live in "
           "results/straddle_short_runs/ and are not compared here.")

_NAME = re.compile(r"backtest_straddle_(\d+)dte_t([\d.]+)_s([\d.]+)_exit(\d+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})")


@st.cache_data(show_spinner=False)
def _load(path_str, mtime):
    return pd.read_csv(path_str)


def _runs():
    runs = {}
    for f in sorted(RESULTS.glob("backtest_straddle_*.csv")):
        m = _NAME.search(f.stem)
        if not m:
            continue
        dte, t, s, x, start, end = m.groups()
        label = f"{dte}→{x} DTE | target {float(t):g}% | stop {float(s):g}% | {start} to {end}"
        runs[label] = f
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
    st.info("No full runs in results/straddle/ yet. Run a backtest over all the data first.")
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
