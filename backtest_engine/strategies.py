"""
backtest_engine/strategies.py
==================================================
The strategy registry. Add a new entry here (plus its engine support) and the
app, storage and compare pages pick it up.

key      -- used in file names and folder names
folder   -- full runs go to results/<folder>/, short runs to
            results/<folder>_short_runs/
has_n    -- whether the strategy takes the strike offset `n`
"""

STRATEGIES = {
    "straddle": {
        "number": 1,
        "title": "Short Straddle",
        "folder": "straddle",
        "has_n": False,
        "brief": (
            "**Strategy 1 - Short Straddle.** Sell the ATM call and the ATM put together, "
            "N days before each monthly expiry. Max profit is the credit you collect. "
            "Exit at X% of that credit (profit target), when the loss reaches Y% of it (stop), "
            "or at the time-exit DTE, whichever comes first. Best when the market stays put; "
            "risk is open on both sides."
        ),
    },
    "strangle": {
        "number": 2,
        "title": "Short Strangle",
        "folder": "strangle",
        "has_n": True,
        "brief": (
            "**Strategy 2 - Short Strangle.** Like the straddle, but the call is sold n points "
            "above ATM and the put n points below ATM (nearest listed strikes). You collect less "
            "credit but the market can move further before you lose. Same target, stop and "
            "time-exit rules as the straddle. n = 0 is the straddle."
        ),
    },
}


def label(key):
    s = STRATEGIES[key]
    return f"Strategy {s['number']}: {s['title']}"
