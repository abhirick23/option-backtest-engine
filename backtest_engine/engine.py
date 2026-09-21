"""
backtest_engine/engine.py
==================================================
Mechanical backtest: "sell a call and a put N days before expiry, exit at a
profit target or stop loss, otherwise exit at the time-exit date (exit_dte
days before expiry, default 21)" -- replayed across every monthly expiry.

One engine serves both strategies via `offset` (n, in index points):
  call strike = listed strike nearest to ATM + n
  put  strike = listed strike nearest to ATM - n
n = 0 is the short straddle; n > 0 is the short strangle.

Max profit of the short position is the total credit collected, so:
  profit target = target_pct % of the credit  (buy back at credit * (1 - t))
  stop loss     = stop_pct   % of the credit  (buy back at credit * (1 + s))
Both are checked on the combined premium (call LTP + put LTP).

IMPORTANT: this is N independent per-expiry trials, not a single-portfolio
simulation. Monthly expiries can overlap when dte is large; no shared-capital
or margin constraint is modeled.
"""

import pandas as pd

from .data import get_available_expiries, load_expiry_chain, get_option_price
from .payoff import calculate_leg_pnl


def identify_monthly_expiries(expiries):
    """The latest expiry date within each (year, month) of the expiry date
    itself -- derived from the data rather than a hardcoded day-of-week
    rule, since the monthly-expiry weekday convention changed more than once
    across the archive's date range."""
    by_month = {}
    for e in expiries:
        key = (pd.to_datetime(e).year, pd.to_datetime(e).month)
        if key not in by_month or e > by_month[key]:
            by_month[key] = e
    return set(by_month.values())


def filter_expiries(expiries, start_date, end_date):
    """Monthly expiries only, with the expiry date inside start_date/end_date
    (datetime.date, inclusive)."""
    in_range = [
        e for e in expiries
        if start_date <= pd.to_datetime(e).date() <= end_date
    ]
    monthly = identify_monthly_expiries(in_range)
    return sorted((e for e in in_range if e in monthly), key=lambda e: pd.to_datetime(e))


def find_entry_datetime(unique_times, expiry, dte):
    """First tick on/after (expiry_date - dte calendar days). None if no such
    tick exists in this expiry's own chain (the archive doesn't reach back
    that far for it) -- the caller treats that as 'skip'."""
    target_date = (pd.to_datetime(expiry) - pd.Timedelta(days=dte)).date()
    for t in unique_times:
        if pd.to_datetime(t).date() >= target_date:
            return t
    return None


def _legs_price(df_indexed, t, call_strike, put_strike):
    """(call, put) LTPs at one tick, or None if either leg has no price."""
    ce = get_option_price(df_indexed, t, call_strike, "CE")
    pe = get_option_price(df_indexed, t, put_strike, "PE")
    if ce is None or pe is None:
        return None
    return ce, pe


def simulate_trade(db_path, expiry, dte, target_pct, stop_pct, lot_size, exit_dte=21, offset=0):
    """One full short call + put lifecycle for one expiry. Returns a dict --
    either a completed trade record, or {"skipped": True, "expiry":...,
    "reason":...} if it couldn't be simulated."""
    df_indexed, unique_times, unique_strikes, spot_indexed = load_expiry_chain(db_path, expiry)

    if df_indexed.empty:
        return {"skipped": True, "expiry": expiry, "reason": "No data for this expiry."}

    if exit_dte >= dte:
        return {"skipped": True, "expiry": expiry,
                "reason": f"exit_dte ({exit_dte}) must be smaller than dte ({dte})."}

    entry_dt = find_entry_datetime(unique_times, expiry, dte)
    if entry_dt is None:
        return {"skipped": True, "expiry": expiry,
                "reason": f"No tick on/after the {dte}-DTE target date (insufficient history before entry point)."}

    if entry_dt not in spot_indexed.index:
        return {"skipped": True, "expiry": expiry, "reason": "No spot price recorded at the entry tick."}
    spot_at_entry = float(spot_indexed.loc[entry_dt, "spot_price"])
    nearest = lambda target: min(unique_strikes, key=lambda x: abs(x - target))
    atm_strike = nearest(spot_at_entry)
    call_strike = nearest(atm_strike + offset)
    put_strike = nearest(atm_strike - offset)

    entry_legs = _legs_price(df_indexed, entry_dt, call_strike, put_strike)
    if entry_legs is None or min(entry_legs) <= 0:
        return {"skipped": True, "expiry": expiry,
                "reason": f"No usable entry price for call {call_strike} / put {put_strike} on {entry_dt}."}
    entry_ce, entry_pe = entry_legs
    credit = entry_ce + entry_pe  # = max profit per share

    target_level = credit * (1 - target_pct / 100.0)  # combined at/below -> profit
    stop_level = credit * (1 + stop_pct / 100.0)      # combined at/above -> loss

    # Time exit: first tick on/after (expiry - exit_dte days). If the chain
    # ends before that date the trade never completed in the data -- skip it
    # rather than closing it early under a "time exit" label.
    time_exit_dt = find_entry_datetime(unique_times, expiry, exit_dte)
    if time_exit_dt is None:
        return {"skipped": True, "expiry": expiry,
                "reason": f"Data ends before the {exit_dte}-DTE time-exit date (trade would be incomplete)."}

    entry_idx = unique_times.index(entry_dt)
    exit_dt = exit_legs = exit_reason = None
    last_seen = None  # (tick, (ce, pe)) of the most recent tick with both prices

    for t in unique_times[entry_idx + 1:]:
        if t > time_exit_dt:
            break
        legs = _legs_price(df_indexed, t, call_strike, put_strike)
        if legs is None:
            continue  # a missing tick for a leg -- keep walking, not a trigger
        last_seen = (t, legs)
        combined = legs[0] + legs[1]
        if combined <= target_level:
            exit_dt, exit_legs, exit_reason = t, legs, "Profit Target"
            break
        if combined >= stop_level:
            exit_dt, exit_legs, exit_reason = t, legs, "Stop Loss"
            break

    if exit_dt is None:
        if last_seen is None:
            return {"skipped": True, "expiry": expiry,
                    "reason": "No call+put price for the chosen strikes after entry."}
        exit_dt, exit_legs = last_seen
        exit_reason = f"Time Exit ({exit_dte} DTE)"

    exit_ce, exit_pe = exit_legs
    pnl = (calculate_leg_pnl(exit_ce, entry_ce, "Sell", 1, lot_size)
           + calculate_leg_pnl(exit_pe, entry_pe, "Sell", 1, lot_size))
    holding_days = (pd.to_datetime(exit_dt) - pd.to_datetime(entry_dt)).total_seconds() / 86400.0
    max_profit = credit * lot_size

    return {
        "skipped": False,
        "expiry": expiry,
        "atm_strike": atm_strike,
        "call_strike": call_strike,
        "put_strike": put_strike,
        "entry_datetime": entry_dt,
        "entry_ce": round(entry_ce, 2),
        "entry_pe": round(entry_pe, 2),
        "credit": round(credit, 2),
        "max_profit": round(max_profit, 2),
        "exit_datetime": exit_dt,
        "exit_ce": round(exit_ce, 2),
        "exit_pe": round(exit_pe, 2),
        "exit_reason": exit_reason,
        "pnl": round(pnl, 2),
        "pnl_pct_of_max": round(pnl / max_profit * 100.0, 1),
        "holding_days": round(holding_days, 2),
    }


def run_backtest(db_path, dte, target_pct, stop_pct, lot_size, start_date, end_date, exit_dte=21, offset=0):
    """Sweeps every eligible monthly expiry. Returns (trades_df, summary, skipped_df).

    dte           -- calendar days before expiry to enter
    target_pct    -- profit target: this % of the credit collected (max profit)
    stop_pct      -- stop loss: this % of the credit collected, as a loss
    lot_size      -- contract multiplier (one call + put pair is traded per expiry)
    start_date / end_date -- datetime.date bounds on the expiry date
    exit_dte      -- time exit: calendar days before expiry (must be < dte)
    offset        -- n in index points: sell call at ATM+n, put at ATM-n (0 = straddle)
    """
    all_expiries = get_available_expiries(db_path)
    eligible = filter_expiries(all_expiries, start_date, end_date)

    trades, skipped = [], []
    for expiry in eligible:
        result = simulate_trade(db_path, expiry, dte, target_pct, stop_pct, lot_size, exit_dte, offset)
        (skipped if result.get("skipped") else trades).append(result)

    trades_df = pd.DataFrame(trades)
    skipped_df = pd.DataFrame(skipped)

    if trades_df.empty:
        summary = {
            "total_trades": 0, "win_rate": None, "total_pnl": 0.0, "avg_pnl": None,
            "avg_holding_days": None, "best_trade": None, "worst_trade": None,
            "skipped_count": len(skipped),
        }
    else:
        wins = int((trades_df["pnl"] > 0).sum())
        summary = {
            "total_trades": len(trades_df),
            "win_rate": wins / len(trades_df) * 100.0,
            "total_pnl": float(trades_df["pnl"].sum()),
            "avg_pnl": float(trades_df["pnl"].mean()),
            "avg_holding_days": float(trades_df["holding_days"].mean()),
            "best_trade": float(trades_df["pnl"].max()),
            "worst_trade": float(trades_df["pnl"].min()),
            "skipped_count": len(skipped),
        }

    return trades_df, summary, skipped_df
