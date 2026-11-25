"""Exponential moving average of the close, registered as one new component file."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator


@register_indicator(
    id="ema",
    version="1.0.0",
    label="Exponential moving average",
    params=(ParamSpec("length", "int", 20, "Lookback (sessions)", min=1, max=400),),
    description=(
        "EMA of the close with alpha = 2/(length+1): ema[t] = ema[t-1] + alpha * "
        "(close[t] - ema[t-1]). Seeded with the simple mean of the first `length` closes, "
        "so the first value is at 0-based index length-1 of an unbroken run of bars. A "
        "missing bar is never filled: it restarts the warm-up, and the run after it is "
        "seeded afresh."
    ),
    price_scale=True,
)
def ema(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    close = bars["close"].to_numpy(dtype=float)
    out = np.full(close.shape, np.nan)
    # As with RSI, each unbroken run of bars is computed on its own.
    valid = ~np.isnan(close)
    start = 0
    while start < close.size:
        if not valid[start]:
            start += 1
            continue
        end = start
        while end < close.size and valid[end]:
            end += 1
        out[start:end] = _ema(close[start:end], n)
        start = end
    return pd.Series(out, index=bars.index)


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    """EMA of a gap-free array, NaN until index n-1, seeded with the mean of x[:n]."""
    out = np.full(x.shape, np.nan)
    if x.size < n:
        return out
    alpha = 2.0 / (n + 1)
    prev = float(x[:n].mean())
    out[n - 1] = prev
    for i in range(n, x.size):
        prev = prev + alpha * (float(x[i]) - prev)
        out[i] = prev
    return out
