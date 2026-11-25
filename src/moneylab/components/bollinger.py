"""Bollinger %B: where the close sits between its Bollinger bands (0 = lower, 1 = upper)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from moneylab.components.registry import ParamSpec, register_indicator


@register_indicator(
    id="bollinger_pctb",
    version="1.0.0",
    label="Bollinger %B",
    params=(
        ParamSpec("length", "int", 20, "Lookback (sessions)", min=2, max=400),
        ParamSpec("stdevs", "float", 2.0, "Band width (standard deviations)", min=0.1, max=10),
    ),
    description=(
        "%B = (close - lower) / (upper - lower), where middle = SMA(close, length) and "
        "upper/lower = middle +/- stdevs x the population standard deviation (ddof=0) of "
        "the last `length` closes. 0 is the lower band, 1 the upper band; it can be below "
        "0 or above 1. Undefined when the bands coincide (all closes in the window equal). "
        "First value at 0-based index length-1; like SMA it is undefined while a missing "
        "bar is inside the window."
    ),
)
def bollinger_pctb(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    k = float(params["stdevs"])
    close = bars["close"].to_numpy(dtype=float)
    out = np.full(close.shape, np.nan)
    if close.size >= n:
        # Each value uses only its own window, so it cannot change when later bars do.
        # A NaN (missing bar) anywhere in the window makes mean and std NaN.
        win = sliding_window_view(close, n)
        mid = win.mean(axis=1)
        band = k * win.std(axis=1, ddof=0)
        # An all-equal window has zero width; rounding in the mean can leave a tiny
        # non-zero std, so test the window itself.
        flat = win.max(axis=1) == win.min(axis=1)
        width = 2.0 * band
        with np.errstate(invalid="ignore", divide="ignore"):
            pctb = (close[n - 1 :] - (mid - band)) / width
        pctb[flat | (width == 0)] = np.nan
        out[n - 1 :] = pctb
    return pd.Series(out, index=bars.index)
