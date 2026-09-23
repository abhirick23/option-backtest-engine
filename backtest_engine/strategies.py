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
defaults   -- optional {"target_pct", "stop_pct", "exit_dte"} that replace the
              global defaults (50 / 100 / 21) for this strategy.
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
        "defaults": {"target_pct": 33.0, "stop_pct": 50.0, "exit_dte": 15},
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
            "stop and time-exit are all a % of that net credit (defaults: target 33%, stop 50%, time exit 15 DTE)."
        ),
    },
    "iron_condor": {
        "number": 4,
        "title": "Iron Condor",
        "folder": "iron_condor",
        "defaults": {"exit_dte": 10},
        "params": [
            {"key": "sd", "label": "sd: sold call & put at this delta (e.g. 30 = 30-delta)",
             "default": 30, "min": 5, "max": 50, "step": 5},
            {"key": "wd", "label": "wd: bought call & put (wings) at this delta (must be < sd)",
             "default": 10, "min": 1, "max": 45, "step": 1},
        ],
        "legs": lambda p: [
            {"name": "short_call", "opt_type": "CE", "side": "Sell", "delta": p["sd"] / 100.0},
            {"name": "short_put", "opt_type": "PE", "side": "Sell", "delta": p["sd"] / 100.0},
            {"name": "long_call", "opt_type": "CE", "side": "Buy", "delta": p["wd"] / 100.0},
            {"name": "long_put", "opt_type": "PE", "side": "Buy", "delta": p["wd"] / 100.0},
        ],
        "validate": lambda p: (
            f"wd ({p['wd']}) must be smaller than sd ({p['sd']}): the bought wings have to sit "
            f"further out (lower delta) than the sold legs." if p["wd"] >= p["sd"] else None
        ),
        "brief": (
            "**Strategy 4 - Iron Condor.** Sell a call and a put at delta *sd* (say 30-delta) and "
            "buy protection further out at delta *wd* (say 10-delta, wd < sd). Strikes are picked "
            "by delta at entry (among strikes that have a traded price) instead of a fixed "
            "distance from ATM, so the trade adapts to volatility: strikes sit wider in "
            "high-vol markets and tighter in quiet ones. Max "
            "profit is the net credit -- what the sold legs bring in minus what the wings cost. "
            "Default time exit: 10 DTE."
        ),
    },
}


def label(key):
    s = STRATEGIES[key]
    return f"Strategy {s['number']}: {s['title']}"


def default_rule(key):
    """This strategy's default (target_pct, stop_pct, exit_dte): 50 / 100 / 21
    unless the registry entry overrides them."""
    d = STRATEGIES[key].get("defaults", {})
    return d.get("target_pct", 50.0), d.get("stop_pct", 100.0), d.get("exit_dte", 21)


def default_params(key):
    return {p["key"]: p["default"] for p in STRATEGIES[key]["params"]}


def build_legs(key, params):
    return STRATEGIES[key]["legs"](params)


def validate_params(key, params):
    """Error string if params are invalid for this strategy, else None."""
    v = STRATEGIES[key].get("validate")
    return v(params) if v else None
