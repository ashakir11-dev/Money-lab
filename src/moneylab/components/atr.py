"""Average true range (Wilder), a volatility measure in price units per session."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator


@register_indicator(
    id="atr",
    version="1.0.0",
    label="Average true range (Wilder)",
    params=(ParamSpec("length", "int", 14, "Lookback (sessions)", min=1, max=200),),
    inputs=("high", "low", "close"),
    description=(
        "Wilder ATR. True range TR = max(high - low, |high - previous close|, "
        "|low - previous close|); on the first session of a run of bars (no previous "
        "close) TR = high - low. The first ATR is the simple mean of the first `length` "
        "true ranges, at the run's session index `length` - 1; later values are "
        "(previous ATR x (length - 1) + TR) / length. A missing bar is never filled: it "
        "has no value and the next bar starts a new run, so the warm-up restarts. Uses "
        "only bars up to and including each session."
    ),
)
def atr(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    out = np.full(close.shape, np.nan)
    # Lookbacks are counted in exchange sessions: a session with no bar (NaN) ends the
    # run and the next bar starts a fresh warm-up, as for SMA and RSI.
    valid = ~(np.isnan(high) | np.isnan(low) | np.isnan(close))
    start = 0
    while start < close.size:
        if not valid[start]:
            start += 1
            continue
        end = start
        while end < close.size and valid[end]:
            end += 1
        out[start:end] = _wilder_atr(high[start:end], low[start:end], close[start:end], n)
        start = end
    return pd.Series(out, index=bars.index)


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    out = np.full(close.shape, np.nan)
    if close.size < n:
        return out
    tr = high - low
    prev = close[:-1]
    tr[1:] = np.maximum.reduce([tr[1:], np.abs(high[1:] - prev), np.abs(low[1:] - prev)])
    value = float(tr[:n].mean())
    out[n - 1] = value
    for i in range(n, close.size):
        value = (value * (n - 1) + float(tr[i])) / n
        out[i] = value
    return out
