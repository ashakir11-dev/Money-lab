"""Trend straightness: how much of the recent path was net progress."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from moneylab.components.registry import ParamSpec, register_indicator


@register_indicator(
    id="straightness",
    version="1.1.0",
    label="Trend straightness",
    params=(
        ParamSpec("length", "int", 20, "Closes in the path", min=2, max=400),
        ParamSpec(
            "output",
            "choice",
            "signed",
            "Output",
            choices=("signed", "unsigned"),
            help=(
                "signed: -1 straight drop .. +1 straight rise. unsigned: 0 chop .. 1 a "
                "straight move either way (Kaufman's efficiency ratio)."
            ),
        ),
    ),
    description=(
        "Signed efficiency of the last `length` close-to-close moves: "
        "(close[t] - close[t-length]) / the sum of the absolute moves. +1 is a "
        "straight rise, -1 a straight drop, and 0 a flat stretch or one that ends "
        "where it started. The first value is at 0-based index `length`. A missing "
        "close anywhere in the span leaves the value blank. The count is in bars, "
        "whatever size the snapshot uses. Output `unsigned` is the absolute value: "
        "Kaufman's efficiency ratio, 0 for chop and 1 for a straight move either way."
    ),
)
def straightness(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    close = bars["close"].to_numpy(dtype=float)
    out = np.full(close.shape, np.nan)
    if close.size > n:
        win = sliding_window_view(close, n + 1)
        net = win[:, -1] - win[:, 0]
        travel = np.abs(np.diff(win, axis=1)).sum(axis=1)
        score = np.zeros(net.shape, dtype=float)
        np.divide(net, travel, out=score, where=travel != 0)
        score[np.isnan(win).any(axis=1)] = np.nan
        out[n:] = np.abs(score) if params.get("output") == "unsigned" else score
    return pd.Series(out, index=bars.index)
