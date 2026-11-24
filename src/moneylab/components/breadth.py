"""Breadth filter: the fraction of eligible universe symbols closing above their SMA.

A cross-sectional indicator. At each session a symbol is eligible when it has a bar
and its SMA(length) is defined there (listed long enough, no gap inside the window).
The value is (eligible symbols with close > SMA) / (eligible symbols), between 0 and
1, and is the same for every symbol in the run. With no eligible symbol it is
undefined (NaN), so a condition on it does not pass and the decision says "warmup".
Use it with a threshold, for example "breadth above 0.5" as an extra entry condition.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import ParamSpec, register_universe_indicator


@register_universe_indicator(
    id="breadth",
    version="1.0.0",
    label="Breadth (fraction above SMA)",
    params=(ParamSpec("length", "int", 50, "SMA lookback (sessions)", min=1, max=400),),
    description=(
        "Fraction of eligible universe symbols whose close is above their own "
        "SMA(length). Eligible: has a bar and a defined SMA that session. 0 to 1; "
        "undefined when no symbol is eligible."
    ),
)
def breadth(frames: dict[str, pd.DataFrame], params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    if not frames:
        return pd.Series(dtype=float)
    index = next(iter(frames.values())).index
    above = np.zeros(len(index))
    eligible = np.zeros(len(index))
    for sym in sorted(frames):
        close = frames[sym]["close"]
        sma = close.rolling(window=n, min_periods=n).mean()
        ok = (close.notna() & sma.notna()).to_numpy()
        eligible += ok
        above += ok & (close > sma).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(eligible > 0, above / np.maximum(eligible, 1), np.nan)
    return pd.Series(out, index=index)
