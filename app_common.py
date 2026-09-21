"""
app_common.py
==================================================
Shared Streamlit bits: the strategy picker used by every page.

Streamlit forgets a widget's value when the page that renders it is left, so
the choice is mirrored into a plain session_state key and copied back into the
widget's own key when the next page opens -- that is what makes the pick carry
from the home page to Compare runs. (The radio keeps a fixed key and index=None:
changing `index` between runs would change the widget's identity and swallow
the user's next click.)
"""

import streamlit as st

from backtest_engine.strategies import STRATEGIES, label

_KEY = "strategy_choice"        # survives page changes
_WIDGET_KEY = "strategy_widget"   # the radio itself


def pick_strategy():
    """Ask which strategy is being tested (nothing pre-selected on first
    open), show its brief in a click-to-open box, and return its key -- or
    None until the user has chosen."""
    keys = list(STRATEGIES)
    saved = st.session_state.get(_KEY)
    if _WIDGET_KEY not in st.session_state and saved in keys:
        st.session_state[_WIDGET_KEY] = saved

    col_pick, col_info = st.columns([6, 1], vertical_alignment="bottom")
    with col_pick:
        choice = st.radio("Which strategy are you testing?", keys, horizontal=True,
                          index=None, key=_WIDGET_KEY, format_func=label)
    if choice:
        st.session_state[_KEY] = choice
        with col_info:
            with st.popover("❗ About"):
                st.markdown(STRATEGIES[choice]["brief"])
    return choice
