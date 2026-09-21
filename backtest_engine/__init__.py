"""
backtest_engine -- mechanical Nifty options backtest over the local DuckDB
archive. See engine.py for the rule, data.py for the data access.
"""

from .engine import run_backtest, simulate_trade

__all__ = ["run_backtest", "simulate_trade"]
