"""Highest high and lowest low of the sessions before the current one (breakout levels).

Today's bar is excluded, so "close crosses above highest_high(50)" means the close
broke above the highest high of the previous 50 sessions. Including today would make
that impossible, since a close can never exceed its own session's high.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator

_LENGTH = ParamSpec("length", "int", 20, "Lookback (sessions before today)", min=1, max=400)


def _prior_window(col: pd.Series, n: int, how: str) -> pd.Series:
    # shift(1) drops today; min_periods=n makes any missing bar in the window NaN.
    roll = col.astype(float).shift(1).rolling(window=n, min_periods=n)
    out: pd.Series = roll.max() if how == "max" else roll.min()
    return out


@register_indicator(
    id="highest_high",
    version="1.0.0",
    label="Highest high (prior sessions)",
    params=(_LENGTH,),
    inputs=("high",),
    description=(
        "Highest high over the `length` sessions before the current one (today excluded): "
        "max(high[t-length], ..., high[t-1]). First value at 0-based index `length`. "
        "Undefined unless all `length` prior sessions have a bar."
    ),
    price_scale=True,
)
def highest_high(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _prior_window(bars["high"], int(params["length"]), "max")


@register_indicator(
    id="lowest_low",
    version="1.0.0",
    label="Lowest low (prior sessions)",
    params=(_LENGTH,),
    inputs=("low",),
    description=(
        "Lowest low over the `length` sessions before the current one (today excluded): "
        "min(low[t-length], ..., low[t-1]). First value at 0-based index `length`. "
        "Undefined unless all `length` prior sessions have a bar."
    ),
    price_scale=True,
)
def lowest_low(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _prior_window(bars["low"], int(params["length"]), "min")
