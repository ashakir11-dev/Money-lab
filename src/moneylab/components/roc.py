"""Rate of change: the close's fractional change over `length` sessions."""

from __future__ import annotations

from typing import Any

import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator


@register_indicator(
    id="roc",
    version="1.0.0",
    label="Rate of change",
    params=(ParamSpec("length", "int", 20, "Lookback (sessions)", min=1, max=400),),
    description=(
        "close[t] / close[t-length] - 1, as a fraction (0.05 = +5%). The lookback is "
        "counted in exchange sessions, so the first value is at 0-based index `length`. "
        "Undefined if either the current bar or the bar `length` sessions earlier is "
        "missing (no gap is filled)."
    ),
)
def roc(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    close = bars["close"].astype(float)
    out: pd.Series = close / close.shift(n) - 1.0
    return out
