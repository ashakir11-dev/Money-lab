"""Confirmed swing points (pivots): the turning points support, resistance and market
structure are built from.

A swing high is a bar whose high is the highest of the ``left`` bars before it and the
``right`` bars after it (a swing low mirrors this with lows). It is only *known* once
the ``right`` bars after it have printed, so every value here uses a pivot from the
session it was confirmed (pivot bar + ``right``) onwards, never earlier. This is the
look-ahead trap in most published support/resistance code, which marks the pivot on
its own bar.

Swing levels are the trading-range-break levels of Brock, Lakonishok and LeBaron
(1992): resistance is a local maximum and support a local minimum. Zones built from
ATR-sized turns are in ``levels.py`` (support / resistance).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator

LEFT = ParamSpec("left", "int", 5, "Bars before the swing", min=1, max=50)
RIGHT = ParamSpec(
    "right",
    "int",
    5,
    "Bars after the swing (confirmation delay)",
    min=1,
    max=50,
    help="A swing point is only used once this many later bars have printed.",
)


def pivots(values: np.ndarray, left: int, right: int, how: str) -> np.ndarray:
    """Boolean mask of pivot bars (marked on the pivot bar itself, not when confirmed).

    A pivot high at k needs bars k-left..k+right all present, high[k] strictly above
    the ``left`` bars before it and at least as high as the ``right`` bars after it, so
    a flat top counts once (its first bar). Pivot lows mirror this.
    """
    v = values if how == "high" else -values
    n = v.size
    out = np.zeros(n, dtype=bool)
    for k in range(left, n - right):
        win = v[k - left : k + right + 1]
        if np.isnan(win).any():
            continue
        before = v[k - left : k]
        after = v[k + 1 : k + right + 1]
        if v[k] > before.max() and v[k] >= after.max():
            out[k] = True
    return out


def last_confirmed(values: np.ndarray, mask: np.ndarray, right: int) -> np.ndarray:
    """Value of the most recent pivot confirmed by each session (pivot bar + right <= t)."""
    out = np.full(values.size, np.nan)
    current = np.nan
    for t in range(values.size):
        k = t - right
        if k >= 0 and mask[k]:
            current = values[k]
        out[t] = current
    return out


@register_indicator(
    id="swing_high",
    version="1.0.0",
    label="Last swing high (confirmed)",
    params=(LEFT, RIGHT),
    inputs=("high",),
    description=(
        "High of the most recent confirmed swing high. Bar k is a swing high when its "
        "high is above the `left` highs before it and at least the `right` highs after "
        "it, with no missing bar in that window. It becomes known at session k + `right` "
        "and is held until the next swing high is confirmed. A simple resistance level."
    ),
    price_scale=True,
)
def swing_high(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    high = bars["high"].to_numpy(dtype=float)
    left, right = int(params["left"]), int(params["right"])
    vals = last_confirmed(high, pivots(high, left, right, "high"), right)
    return pd.Series(vals, index=bars.index)


@register_indicator(
    id="swing_low",
    version="1.0.0",
    label="Last swing low (confirmed)",
    params=(LEFT, RIGHT),
    inputs=("low",),
    description=(
        "Low of the most recent confirmed swing low: the mirror of swing_high using lows. "
        "Known at session k + `right`. A simple support level."
    ),
    price_scale=True,
)
def swing_low(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    low = bars["low"].to_numpy(dtype=float)
    left, right = int(params["left"]), int(params["right"])
    vals = last_confirmed(low, pivots(low, left, right, "low"), right)
    return pd.Series(vals, index=bars.index)
