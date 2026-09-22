"""
backtest_engine/strategies.py
==================================================
The strategy registry. Add a new entry here (name, legs, params) and the
engine, CLI, app, storage and compare pages all pick it up -- nothing else
needs to change for a new leg combination.

key        -- used in file names and folder names
folder     -- full runs go to results/<folder>/, short runs to
              results/<folder>_short_runs/
params     -- ordered list of the strategy's own parameters (empty for the
              straddle). Each is {key, label, default, min, max, step}.
legs(p)    -- given a dict of the params above (by key), returns the leg
              list for backtest_engine.engine.simulate_trade/run_backtest.
validate(p) -- optional: return an error string if p is invalid, else None.
"""

STRATEGIES = {
    "straddle": {
        "number": 1,
        "title": "Short Straddle",
        "folder": "straddle",
        "params": [],
        "legs": lambda p: [
            {"name": "short_call", "opt_type": "CE", "side": "Sell", "offset": 0},
            {"name": "short_put", "opt_type": "PE", "side": "Sell", "offset": 0},
        ],
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
        "params": [
            {"key": "n", "label": "n: call = ATM + n, put = ATM - n (index points)",
             "default": 200, "min": 0, "max": 2000, "step": 50},
        ],
        "legs": lambda p: [
            {"name": "short_call", "opt_type": "CE", "side": "Sell", "offset": p["n"]},
            {"name": "short_put", "opt_type": "PE", "side": "Sell", "offset": -p["n"]},
        ],
        "brief": (
            "**Strategy 2 - Short Strangle.** Like the straddle, but the call is sold n points "
            "above ATM and the put n points below ATM (nearest listed strikes). You collect less "
            "credit but the market can move further before you lose. Same target, stop and "
            "time-exit rules as the straddle. n = 0 is the straddle."
        ),
    },
    "iron_fly": {
        "number": 3,
        "title": "Iron Fly (Iron Butterfly)",
        "folder": "iron_fly",
        "params": [
            {"key": "w", "label": "w: wing width, bought call = ATM + w, bought put = ATM - w",
             "default": 300, "min": 50, "max": 3000, "step": 50},
        ],
        "legs": lambda p: [
            {"name": "short_call", "opt_type": "CE", "side": "Sell", "offset": 0},
            {"name": "short_put", "opt_type": "PE", "side": "Sell", "offset": 0},
            {"name": "long_call", "opt_type": "CE", "side": "Buy", "offset": p["w"]},
            {"name": "long_put", "opt_type": "PE", "side": "Buy", "offset": -p["w"]},
        ],
        "brief": (
            "**Strategy 3 - Iron Fly.** A short straddle (sell ATM call + ATM put) with protection "
            "bought around it: a call at ATM + w and a put at ATM - w. Max profit is the *net* "
            "credit -- what the straddle brings in minus what the wings cost. The wings cap the "
            "loss on both sides, at the price of a smaller credit than a plain straddle. Target, "
            "stop and time-exit are all a % of that net credit."
        ),
    },
    "iron_condor": {
        "number": 4,
        "title": "Iron Condor",
        "folder": "iron_condor",
        "params": [
            {"key": "n", "label": "n: sold call = ATM + n, sold put = ATM - n (index points)",
             "default": 200, "min": 0, "max": 2000, "step": 50},
            {"key": "var", "label": "var: bought call = ATM + var, bought put = ATM - var (must be > n)",
             "default": 500, "min": 50, "max": 3000, "step": 50},
        ],
        "legs": lambda p: [
            {"name": "short_call", "opt_type": "CE", "side": "Sell", "offset": p["n"]},
            {"name": "short_put", "opt_type": "PE", "side": "Sell", "offset": -p["n"]},
            {"name": "long_call", "opt_type": "CE", "side": "Buy", "offset": p["var"]},
            {"name": "long_put", "opt_type": "PE", "side": "Buy", "offset": -p["var"]},
        ],
        "validate": lambda p: (
            f"var ({p['var']}) must be greater than n ({p['n']})." if p["var"] <= p["n"] else None
        ),
        "brief": (
            "**Strategy 4 - Iron Condor.** A short strangle (sell call at ATM + n, put at ATM - n) "
            "with protection bought further out: a call at ATM + var and a put at ATM - var "
            "(var > n). Max profit is the net credit -- the strangle's credit minus the wings' "
            "cost. Wider wings (bigger var - n) mean more credit but a bigger max loss; narrower "
            "wings cap the loss tighter for less credit. Target, stop and time-exit are all a % "
            "of the net credit."
        ),
    },
}


def label(key):
    s = STRATEGIES[key]
    return f"Strategy {s['number']}: {s['title']}"


def default_params(key):
    return {p["key"]: p["default"] for p in STRATEGIES[key]["params"]}


def build_legs(key, params):
    return STRATEGIES[key]["legs"](params)


def validate_params(key, params):
    """Error string if params are invalid for this strategy, else None."""
    v = STRATEGIES[key].get("validate")
    return v(params) if v else None
