"""
backtest_engine/engine.py
==================================================
Mechanical backtest: sell (and, for the iron strategies, also buy) a set of
options N days before expiry, exit at a profit target or stop loss, otherwise
exit at the time-exit date (exit_dte days before expiry, default 21) --
replayed across every monthly expiry.

One engine serves every strategy via a `legs` list. Each leg is
  {"name": "short_call" | "short_put" | "long_call" | "long_put",
   "opt_type": "CE" | "PE", "side": "Sell" | "Buy",
   "offset": int   OR   "delta": float}
A leg is placed either by `offset` -- index points from the tick's ATM strike
(the listed strike nearest spot); the strike used is the listed strike nearest
ATM + offset -- or by `delta`: the listed strike whose absolute delta at the
entry tick is closest to that value (0..1).
See backtest_engine/strategies.py for each strategy's leg list:

  straddle     : short call @ 0,  short put @ 0                              (offsets)
  strangle     : short call @ +n, short put @ -n                             (offsets)
  iron_fly     : short call @ 0,  short put @ 0,  long call @ +w,  long put @ -w   (offsets)
  iron_condor  : sold call / put at delta sd, bought call / put at delta wd
                 (wd < sd, so the wings sit further out)                     (deltas)

Net credit = sum(sell leg entry prices) - sum(buy leg entry prices); this is
also each strategy's max profit. Both are checked against the position's
combined value (sum of sell-leg prices minus sum of buy-leg prices) each
tick:
  profit target = combined <= credit * (1 - target_pct / 100)
  stop loss     = combined >= credit * (1 + stop_pct / 100)

IMPORTANT: this is N independent per-expiry trials, not a single-portfolio
simulation. Monthly expiries can overlap when dte is large; no shared-capital
or margin constraint is modeled.
"""

import pandas as pd

from .data import get_available_expiries, load_expiry_chain, get_option_price, strike_by_delta
from .payoff import calculate_leg_pnl

# The four possible leg names, in a fixed column order for the trade record.
LEG_NAMES = ["short_call", "short_put", "long_call", "long_put"]

DEFAULT_LEGS = [
    {"name": "short_call", "opt_type": "CE", "side": "Sell", "offset": 0},
    {"name": "short_put", "opt_type": "PE", "side": "Sell", "offset": 0},
]


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


def _leg_prices(df_indexed, t, resolved_legs):
    """{name: price} for every leg at one tick, or None if any leg has no
    price there."""
    prices = {}
    for leg in resolved_legs:
        price = get_option_price(df_indexed, t, leg["strike"], leg["opt_type"])
        if price is None:
            return None
        prices[leg["name"]] = price
    return prices


def _entry_delta(df_indexed, t, leg):
    """The leg's absolute delta at the entry tick (recorded for delta-placed legs)."""
    col = "call_delta" if leg["opt_type"] == "CE" else "put_delta"
    return round(abs(float(df_indexed.loc[(t, int(leg["strike"])), col])), 4)


def _combined(prices, resolved_legs):
    """Cost to close the position right now: buy back the sold legs, sell
    the bought legs. Equals the net credit at entry."""
    return sum(prices[l["name"]] if l["side"] == "Sell" else -prices[l["name"]] for l in resolved_legs)


def simulate_trade(db_path, expiry, dte, target_pct, stop_pct, lot_size, exit_dte=21, legs=None):
    """One full lifecycle (all legs) for one expiry. Returns a dict -- either
    a completed trade record, or {"skipped": True, "expiry":..., "reason":...}
    if it couldn't be simulated. `legs` defaults to the short straddle."""
    legs = legs if legs is not None else DEFAULT_LEGS
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
    resolved_legs = []
    for leg in legs:
        if "delta" in leg:
            strike = strike_by_delta(df_indexed, entry_dt, leg["opt_type"], leg["delta"])
            if strike is None:
                return {"skipped": True, "expiry": expiry,
                        "reason": f"No delta data at the entry tick {entry_dt} to place {leg['name']}."}
        else:
            strike = nearest(atm_strike + leg["offset"])
        resolved_legs.append({**leg, "strike": strike})

    # A bought wing must sit strictly further out than the sold leg of the same
    # type (calls: higher strike, puts: lower strike), or the pair degenerates.
    sold = {l["opt_type"]: l for l in resolved_legs if l["side"] == "Sell"}
    for leg in resolved_legs:
        short = sold.get(leg["opt_type"])
        if leg["side"] == "Buy" and short is not None:
            further = leg["strike"] > short["strike"] if leg["opt_type"] == "CE" else leg["strike"] < short["strike"]
            if not further:
                return {"skipped": True, "expiry": expiry,
                        "reason": f"{leg['name']} {leg['strike']} is not further out than "
                                  f"{short['name']} {short['strike']} at {entry_dt}."}

    entry_prices = _leg_prices(df_indexed, entry_dt, resolved_legs)
    if entry_prices is None or min(entry_prices.values()) <= 0:
        legs_desc = ", ".join(f"{l['name']} {l['strike']}" for l in resolved_legs)
        return {"skipped": True, "expiry": expiry,
                "reason": f"No usable entry price for one or more legs ({legs_desc}) on {entry_dt}."}
    credit = _combined(entry_prices, resolved_legs)  # = max profit per share
    if credit <= 0:
        return {"skipped": True, "expiry": expiry,
                "reason": f"Net credit is not positive ({credit:.2f}) at entry -- not a credit trade here."}

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
    exit_dt = exit_prices = exit_reason = None
    last_seen = None  # (tick, {name: price}) of the most recent tick with every leg priced

    for t in unique_times[entry_idx + 1:]:
        if t > time_exit_dt:
            break
        prices = _leg_prices(df_indexed, t, resolved_legs)
        if prices is None:
            continue  # a missing tick for a leg -- keep walking, not a trigger
        last_seen = (t, prices)
        combined = _combined(prices, resolved_legs)
        if combined <= target_level:
            exit_dt, exit_prices, exit_reason = t, prices, "Profit Target"
            break
        if combined >= stop_level:
            exit_dt, exit_prices, exit_reason = t, prices, "Stop Loss"
            break

    if exit_dt is None:
        if last_seen is None:
            return {"skipped": True, "expiry": expiry,
                    "reason": "No price for one or more legs at the chosen strikes after entry."}
        exit_dt, exit_prices = last_seen
        exit_reason = f"Time Exit ({exit_dte} DTE)"

    pnl = sum(calculate_leg_pnl(exit_prices[l["name"]], entry_prices[l["name"]], l["side"], 1, lot_size)
              for l in resolved_legs)
    holding_days = (pd.to_datetime(exit_dt) - pd.to_datetime(entry_dt)).total_seconds() / 86400.0
    max_profit = credit * lot_size

    record = {
        "skipped": False,
        "expiry": expiry,
        "atm_strike": atm_strike,
    }
    by_name = {l["name"]: l for l in resolved_legs}
    for name in LEG_NAMES:
        leg = by_name.get(name)
        record[f"{name}_strike"] = leg["strike"] if leg else None
        record[f"{name}_delta"] = _entry_delta(df_indexed, entry_dt, leg) if leg and "delta" in leg else None
        record[f"{name}_entry"] = round(entry_prices[name], 2) if leg else None
        record[f"{name}_exit"] = round(exit_prices[name], 2) if leg else None
    record.update({
        "entry_datetime": entry_dt,
        "exit_datetime": exit_dt,
        "exit_reason": exit_reason,
        "credit": round(credit, 2),
        "max_profit": round(max_profit, 2),
        "pnl": round(pnl, 2),
        "pnl_pct_of_max": round(pnl / max_profit * 100.0, 1),
        "holding_days": round(holding_days, 2),
    })
    return record


def run_backtest(db_path, dte, target_pct, stop_pct, lot_size, start_date, end_date, exit_dte=21, legs=None):
    """Sweeps every eligible monthly expiry. Returns (trades_df, summary, skipped_df).

    dte           -- calendar days before expiry to enter
    target_pct    -- profit target: this % of the net credit collected (max profit)
    stop_pct      -- stop loss: this % of the net credit collected, as a loss
    lot_size      -- contract multiplier (one set of legs is traded per expiry)
    start_date / end_date -- datetime.date bounds on the expiry date
    exit_dte      -- time exit: calendar days before expiry (must be < dte)
    legs          -- the strategy's leg list (see module docstring); default is the straddle
    """
    all_expiries = get_available_expiries(db_path)
    eligible = filter_expiries(all_expiries, start_date, end_date)

    trades, skipped = [], []
    for expiry in eligible:
        result = simulate_trade(db_path, expiry, dte, target_pct, stop_pct, lot_size, exit_dte, legs)
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
