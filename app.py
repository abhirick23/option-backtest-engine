"""
app.py
==================================================
Streamlit UI for the backtest engine.

    streamlit run app.py
"""

import pathlib

import pandas as pd
import streamlit as st

from backtest_engine import run_backtest
from backtest_engine.data import get_available_expiries
from app_common import pick_strategy
from backtest_engine.storage import coverage_table, save_run
from backtest_engine.strategies import STRATEGIES, build_legs, label, validate_params

ROOT = pathlib.Path(__file__).resolve().parent
DB_PATH = str(ROOT / "data" / "market_data.duckdb")

st.set_page_config(page_title="Nifty Backtest Engine", layout="wide")
st.title("Nifty Option Backtest")
st.caption("Sell (and, for the iron strategies, buy) a set of options N days before each monthly "
           "expiry. Max profit = net credit collected; exit at X% of it (target) or Y% of it as a "
           "loss (stop), otherwise at the time exit.")

strategy = pick_strategy()
if strategy is None:
    st.info("Pick a strategy above to begin.")
    st.stop()


@st.cache_data(show_spinner=False)
def _expiries(db_path):
    return get_available_expiries(db_path)


@st.cache_data(show_spinner=False)
def _run(*args):
    return run_backtest(*args)


if not pathlib.Path(DB_PATH).is_file():
    st.error(f"Database not found: {DB_PATH}")
    st.stop()

expiries = _expiries(DB_PATH)
data_min = pd.to_datetime(min(expiries)).date()
data_max = pd.to_datetime(max(expiries)).date()

with st.sidebar:
    st.header(label(strategy))
    dte = st.number_input("Entry: days before expiry", 2, 120, 45)
    exit_dte = st.number_input("Time exit: days before expiry", 0, 119, 21)
    target_pct = st.number_input("Profit target (% of max profit / credit)", 1.0, 100.0, 50.0, step=5.0)
    stop_pct = st.number_input("Stop loss (% of credit, as a loss)", 1.0, 1000.0, 100.0, step=10.0)
    params = {}
    for spec in STRATEGIES[strategy]["params"]:
        params[spec["key"]] = st.number_input(spec["label"], spec["min"], spec["max"], spec["default"],
                                              step=spec["step"])
    lot_size = st.number_input("Lot size", 1, 1000, 65)
    st.header("Expiry range")
    start = st.date_input("From", data_min, min_value=data_min, max_value=data_max)
    end = st.date_input("To", data_max, min_value=data_min, max_value=data_max)
    go = st.button("Run backtest", type="primary", width="stretch")

if exit_dte >= dte:
    st.error("Time exit days must be smaller than entry days.")
    st.stop()
if start > end:
    st.error("'From' is after 'To'.")
    st.stop()
param_error = validate_params(strategy, params)
if param_error:
    st.error(param_error)
    st.stop()

if go:
    legs = build_legs(strategy, params)
    with st.spinner("Running (each expiry takes a couple of seconds on the first run)..."):
        st.session_state["result"] = _run(DB_PATH, int(dte), float(target_pct), float(stop_pct),
                                          int(lot_size), start, end, int(exit_dte), legs)
    st.session_state["result_strategy"] = strategy
    _t, _s, _k = st.session_state["result"]
    st.session_state["saved_path"] = None
    if _s["total_trades"]:
        saved, full = save_run(strategy, _t.drop(columns=["skipped"]), ROOT / "results", expiries, int(dte),
                               params, target_pct, stop_pct, int(exit_dte), start, end)
        st.session_state["saved_path"] = (str(saved), full)

if st.session_state.get("result_strategy") != strategy:
    st.info("Set the parameters in the sidebar and press **Run backtest**.")
    st.stop()

trades_df, summary, skipped_df = st.session_state["result"]

if summary["total_trades"] == 0:
    st.warning(f"No trades produced -- all {summary['skipped_count']} eligible expiries were skipped.")
else:
    c = st.columns(6)
    c[0].metric("Trades", summary["total_trades"])
    c[1].metric("Win rate", f"{summary['win_rate']:.1f}%")
    c[2].metric("Total P&L", f"{summary['total_pnl']:,.0f}")
    c[3].metric("Avg P&L / trade", f"{summary['avg_pnl']:,.0f}")
    c[4].metric("Best / worst", f"{summary['best_trade']:,.0f} / {summary['worst_trade']:,.0f}")
    c[5].metric("Avg holding", f"{summary['avg_holding_days']:.1f} d")

    trades = trades_df.drop(columns=["skipped"])
    if st.session_state.get("saved_path"):
        path, full = st.session_state["saved_path"]
        st.caption(f"Saved to {path} -- " + ("full run, shows in Compare runs for this strategy" if full
                   else "short run (range doesn't cover all the data), not in Compare runs"))
    st.subheader("Cumulative P&L")
    st.line_chart(pd.Series(trades["pnl"].cumsum().values, index=pd.to_datetime(trades["expiry"]), name="Cumulative P&L"))

    left, right = st.columns([2, 1])
    with left:
        st.subheader("P&L per trade")
        st.bar_chart(pd.Series(trades["pnl"].values, index=pd.to_datetime(trades["expiry"]), name="P&L"))
    with right:
        st.subheader("Exit reasons")
        st.bar_chart(trades["exit_reason"].value_counts())

    st.subheader("Trades")
    st.dataframe(trades, width="stretch", hide_index=True)
    st.download_button("Download trades CSV", trades.to_csv(index=False),
                       file_name="backtest_trades.csv", mime="text/csv")

cov = coverage_table(trades_df, skipped_df)
with st.expander(f"Coverage checklist -- {int((cov['status'] == 'Traded').sum())} of {len(cov)} expiries traded",
                 expanded=bool((cov["status"] == "Skipped").any())):
    cov = cov.assign(done=cov["status"].eq("Traded"))[["done", "expiry", "status", "detail"]]
    st.dataframe(cov, width="stretch", hide_index=True,
                 column_config={"done": st.column_config.CheckboxColumn("Traded", disabled=True)})
