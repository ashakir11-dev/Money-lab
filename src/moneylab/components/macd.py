"""MACD (moving average convergence/divergence), registered as one new component file.

Nothing outside this file names MACD: the dashboard builds its controls from the
declaration, config resolution validates and records it, and the simulator calls
``compute`` like any other indicator.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator


def _check(params: dict[str, Any]) -> None:
    if params["fast"] >= params["slow"]:
        raise ValueError(
            f"macd: fast ({params['fast']}) must be shorter than slow ({params['slow']})"
        )


@register_indicator(
    id="macd",
    version="1.0.0",
    label="MACD",
    params=(
        ParamSpec("fast", "int", 12, "Fast EMA (sessions)", min=1, max=200),
        ParamSpec("slow", "int", 26, "Slow EMA (sessions)", min=2, max=400),
        ParamSpec("signal", "int", 9, "Signal EMA (sessions)", min=1, max=200),
        ParamSpec(
            "output",
            "choice",
            "histogram",
            "Output",
            choices=("macd", "signal", "histogram"),
        ),
    ),
    check=_check,
    description=(
        "MACD line = EMA(close, fast) - EMA(close, slow); signal = EMA(MACD line, signal); "
        "histogram = MACD line - signal. Each EMA uses alpha = 2/(n+1) and is seeded with "
        "the simple mean of its first n inputs. Warm-up (0-based session index within an "
        "unbroken run of bars): the MACD line is first defined at index slow-1; signal and "
        "histogram at index slow+signal-2 (defaults: 25 and 33). A missing bar restarts "
        "the warm-up. fast must be shorter than slow."
    ),
)
def macd(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    fast, slow, sig = int(params["fast"]), int(params["slow"]), int(params["signal"])
    output = params["output"]
    close = bars["close"].to_numpy(dtype=float)
    out = np.full(close.shape, np.nan)
    # As with RSI, a missing bar (NaN) restarts the warm-up: no gap is filled with an
    # invented price, and each unbroken run of bars is computed on its own.
    valid = ~np.isnan(close)
    start = 0
    while start < close.size:
        if not valid[start]:
            start += 1
            continue
        end = start
        while end < close.size and valid[end]:
            end += 1
        out[start:end] = _macd(close[start:end], fast, slow, sig, output)
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


def _macd(close: np.ndarray, fast: int, slow: int, sig: int, output: str) -> np.ndarray:
    line: np.ndarray = _ema(close, fast) - _ema(close, slow)  # NaN before index slow-1
    if output == "macd":
        return line
    signal = np.full(close.shape, np.nan)
    first = slow - 1
    if close.size > first:
        signal[first:] = _ema(line[first:], sig)
    if output == "signal":
        return signal
    hist: np.ndarray = line - signal
    return hist
