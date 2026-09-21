"""
backtest_engine/data.py
==================================================
Read-only DuckDB access for the backtest engine. No Streamlit, no writes.

The database is data/market_data.duckdb, table `option_chain_snapshots`: one
row per (pulled_date, pulled_time, expiry, strike_price) snapshot of the
Nifty option chain, on a 5-minute cadence.
"""

import time

import duckdb
import numpy as np
import pandas as pd

TABLE_NAME = "option_chain_snapshots"

DATE_COL = "pulled_date"
TIME_COL = "pulled_time"
EXPIRY_COL = "expiry"
SPOT_COL = "spot_price"
STRIKE_COL = "strike_price"
CALL_LTP_COL = "call_ltp"
PUT_LTP_COL = "put_ltp"

# The only columns the engine reads. The table also has OI / IV / Greeks /
# volume (call_oi, call_iv, call_delta, put_theta, ...) -- add them here if a
# future strategy needs them; nothing else in this file has to change.
CHAIN_COLUMNS = [DATE_COL, TIME_COL, EXPIRY_COL, SPOT_COL, STRIKE_COL, CALL_LTP_COL, PUT_LTP_COL]

# The archive mixes several date-string formats across quarters, so dates are
# normalised in SQL rather than trusted as-is.
_DATE_NORMALIZE_CASES = [
    (r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$', '%Y-%m-%d'),
    (r'^[0-9]{2}-[0-9]{2}-[0-9]{4}$', '%d-%m-%Y'),
    (r'^[0-9]{2}-[0-9]{2}-[0-9]{2}$', '%d-%m-%y'),
    (r'^[0-9]{4}/[0-9]{2}/[0-9]{2}$', '%Y/%m/%d'),
    (r'^[0-9]{2}/[0-9]{2}/[0-9]{4}$', '%d/%m/%Y'),
]


def _date_norm_sql(col):
    whens = "\n        ".join(
        f"WHEN regexp_matches({col}, '{pattern}') THEN strptime({col}, '{fmt}')"
        for pattern, fmt in _DATE_NORMALIZE_CASES
    )
    return f"CAST(CASE\n        {whens}\n        ELSE NULL\n    END AS DATE)"


def connect_with_retry(db_path, read_only=True, attempts=5, delay_seconds=1.0):
    """Open a DuckDB connection, retrying if the file is briefly locked by
    another process (a DB-viewer extension in an editor, antivirus, ...).
    DuckDB's Windows file lock is exclusive per process on a plain open, so
    this only rides out brief contention -- a tool left connected all day
    will still make it raise, with the original error."""
    last_error = None
    for attempt in range(attempts):
        try:
            return duckdb.connect(database=str(db_path), read_only=read_only)
        except duckdb.IOException as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    raise last_error


def get_available_expiries(db_path):
    """Every expiry date in the data, as sorted 'YYYY-MM-DD' strings."""
    norm = _date_norm_sql(EXPIRY_COL)
    q = f"""
        SELECT DISTINCT {norm} AS expiry_date
        FROM {TABLE_NAME}
        WHERE {EXPIRY_COL} IS NOT NULL
        ORDER BY expiry_date
    """
    with connect_with_retry(db_path) as con:
        df = con.execute(q).df()
    df = df.dropna(subset=["expiry_date"])
    return df["expiry_date"].astype(str).tolist()


def load_expiry_chain(db_path, expiry_date):
    """The FULL option-chain time series for one expiry's whole life.

    Returns (df_indexed, unique_times, unique_strikes, spot_indexed):
      df_indexed     -- rows indexed by (datetime, strike)
      unique_times   -- sorted 'YYYY-MM-DD HH:MM:SS' strings, one per tick
      unique_strikes -- sorted int strikes
      spot_indexed   -- spot price indexed by datetime
    An expiry with no rows returns (empty DataFrame, [], [], empty DataFrame).
    """
    cols_sql = ", ".join(CHAIN_COLUMNS)
    norm_expiry = _date_norm_sql(EXPIRY_COL)
    norm_date = _date_norm_sql(DATE_COL)

    q = f"""
        SELECT {cols_sql}, {norm_date} AS clean_date
        FROM {TABLE_NAME}
        WHERE {norm_expiry} = CAST(? AS DATE)
    """
    with connect_with_retry(db_path) as con:
        df = con.execute(q, [expiry_date]).df()

    if df.empty:
        return pd.DataFrame(), [], [], pd.DataFrame()

    # ---- clean / coerce types ----
    df[TIME_COL] = df[TIME_COL].astype("string").str.strip()
    df[TIME_COL] = pd.Series(
        np.where(df[TIME_COL].str.len() == 5, df[TIME_COL] + ":00", df[TIME_COL].str.slice(0, 8)),
        index=df.index, dtype="string"
    )
    df[STRIKE_COL] = pd.to_numeric(df[STRIKE_COL], errors="coerce")
    df[SPOT_COL] = pd.to_numeric(df[SPOT_COL], errors="coerce")
    df[CALL_LTP_COL] = pd.to_numeric(df[CALL_LTP_COL], errors="coerce")
    df[PUT_LTP_COL] = pd.to_numeric(df[PUT_LTP_COL], errors="coerce")

    df = df.dropna(subset=[TIME_COL, STRIKE_COL, SPOT_COL, "clean_date"])

    df["datetime"] = df["clean_date"].astype(str) + " " + df[TIME_COL]
    df[STRIKE_COL] = df[STRIKE_COL].astype(int)

    unique_times = sorted(df["datetime"].unique().tolist())
    unique_strikes = sorted(df[STRIKE_COL].dropna().unique().tolist())

    spot_indexed = (
        df[["datetime", SPOT_COL]]
        .dropna()
        .drop_duplicates(subset=["datetime"], keep="first")
        .set_index("datetime")
        .sort_index()
    )

    df_indexed = (
        df.drop_duplicates(subset=["datetime", STRIKE_COL], keep="first")
        .set_index(["datetime", STRIKE_COL])
        .sort_index()
    )

    return df_indexed, unique_times, unique_strikes, spot_indexed


def get_option_price(df_indexed, selected_datetime, strike, opt_type):
    """LTP of one strike at one tick ('CE' -> call, else put); None if that
    tick/strike has no row or no price."""
    key = (selected_datetime, int(strike))
    if key not in df_indexed.index:
        return None
    row = df_indexed.loc[key]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    col = CALL_LTP_COL if opt_type == "CE" else PUT_LTP_COL
    val = row.get(col, None)
    return None if pd.isna(val) else float(val)
