"""
backtest_engine/payoff.py
==================================================
The two pieces of option P&L math the engine needs. Pure numpy.
"""

import numpy as np


def calculate_leg_pnl(cmp_price, entry_price, buy_sell, lots, lot_size):
    """Rupee P&L of one leg: (exit - entry) per share, sign-flipped for a
    Sell, times lot_size * lots."""
    diff = cmp_price - entry_price
    if buy_sell == "Sell":
        diff *= -1
    return diff * lot_size * lots


def option_payoff_at_expiry(spot_range, strike, opt_type, buy_sell, entry_price, lots, lot_size):
    """P&L at expiry across an array of spot prices: intrinsic value minus
    the premium paid (Buy) or the premium received minus intrinsic (Sell),
    times lot_size * lots. With entry_price=0 and lots=lot_size=1 it returns
    the pure per-share intrinsic value."""
    if opt_type == "CE":
        intrinsic = np.maximum(spot_range - strike, 0)
    else:
        intrinsic = np.maximum(strike - spot_range, 0)

    if buy_sell == "Buy":
        payoff_points = intrinsic - entry_price
    else:
        payoff_points = entry_price - intrinsic

    return payoff_points * lot_size * lots
