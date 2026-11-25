"""Built-in components: SMA and RSI indicators, conditions, the holding-period exit and
the default position sizing."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import (
    ComponentDeclaration,
    ExitContext,
    ParamSpec,
    SizingContext,
    register,
    register_exit,
    register_indicator,
    register_sizing,
)


@register_indicator(
    id="sma",
    version="1.0.0",
    label="Simple moving average",
    params=(ParamSpec("length", "int", 20, "Lookback (sessions)", min=1, max=400),),
    description="Mean of the last `length` closes. Undefined until `length` closes exist.",
    price_scale=True,
)
def sma(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    return bars["close"].rolling(window=n, min_periods=n).mean()


@register_indicator(
    id="rsi",
    version="1.0.0",
    label="Relative strength index (Wilder)",
    params=(ParamSpec("length", "int", 14, "Lookback (sessions)", min=2, max=200),),
    description=(
        "Wilder RSI. The first average gain/loss is the simple mean of the first `length` "
        "close-to-close changes; later values use Wilder smoothing. First value is at the "
        "session with `length` prior changes (index `length`)."
    ),
)
def rsi(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    close = bars["close"].to_numpy(dtype=float)
    out = np.full(close.shape, np.nan)
    # A missing bar (NaN) restarts the warm-up: lookbacks are counted in exchange
    # sessions and a gap is never filled with an invented price.
    valid = ~np.isnan(close)
    start = 0
    while start < close.size:
        if not valid[start]:
            start += 1
            continue
        end = start
        while end < close.size and valid[end]:
            end += 1
        out[start:end] = _wilder_rsi(close[start:end], n)
        start = end
    return pd.Series(out, index=bars.index)


def _wilder_rsi(close: np.ndarray, n: int) -> np.ndarray:
    out = np.full(close.shape, np.nan)
    if close.size <= n:
        return out
    delta = np.diff(close)
    gains = np.clip(delta, 0, None)
    losses = np.clip(-delta, 0, None)
    avg_gain = float(gains[:n].mean())
    avg_loss = float(losses[:n].mean())

    def value(g: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / loss)

    out[n] = value(avg_gain, avg_loss)
    for i in range(n + 1, close.size):
        avg_gain = (avg_gain * (n - 1) + gains[i - 1]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i - 1]) / n
        out[i] = value(avg_gain, avg_loss)
    return out


# Conditions and exit rules carry no calculation of their own; the engine evaluates them
# against indicator values. They are declared so the UI and validation can use them.
register(
    ComponentDeclaration(
        id="price_cross",
        version="1.0.0",
        label="Close crosses an indicator",
        uses=("entry", "exit"),
        params=(
            ParamSpec("direction", "choice", "above", "Direction", choices=("above", "below")),
        ),
        inputs=("close", "indicator"),
        outputs=("signal",),
        description=(
            "Above: previous close <= previous indicator value and close > indicator value. "
            "Below is the mirror image."
        ),
    )
)
register(
    ComponentDeclaration(
        id="threshold",
        version="1.0.0",
        label="Indicator compared with a number",
        uses=("entry", "exit", "filter"),
        params=(
            ParamSpec("op", "choice", "below", "Comparison", choices=("below", "above")),
            ParamSpec("value", "float", 30.0, "Threshold", min=-1e9, max=1e9),
        ),
        inputs=("indicator",),
        outputs=("signal",),
        description="Below: indicator < value. Above: indicator > value.",
    )
)
register(
    ComponentDeclaration(
        id="indicator_compare",
        version="1.0.0",
        label="Indicator compared with another indicator",
        uses=("entry", "exit", "filter"),
        params=(ParamSpec("op", "choice", "above", "Comparison", choices=("above", "below")),),
        inputs=("indicator", "other"),
        outputs=("signal",),
        description=(
            "Above: indicator > other. Below: indicator < other. Both must be in the same "
            "units: both in price units (SMA, EMA, ...), or the same indicator with "
            "different settings."
        ),
    )
)
register(
    ComponentDeclaration(
        id="indicator_cross",
        version="1.0.0",
        label="Indicator crosses another indicator",
        uses=("entry", "exit"),
        params=(
            ParamSpec("direction", "choice", "above", "Direction", choices=("above", "below")),
        ),
        inputs=("indicator", "other"),
        outputs=("signal",),
        description=(
            "Above: previous indicator <= previous other and indicator > other (for example "
            "SMA50 crossing above SMA200). Below is the mirror image. Same unit rule as "
            "indicator_compare."
        ),
    )
)


@register_exit(
    id="holding_period",
    version="1.0.0",
    label="Exit after N sessions",
    params=(ParamSpec("sessions", "int", 10, "Sessions held", min=1, max=500),),
    description=(
        "Signals an exit at the close of the Nth exchange session held; the fill session "
        "counts as session one."
    ),
)
def holding_period(ctx: ExitContext, params: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    n = int(params["sessions"])
    if ctx.held >= n:
        return f"held {ctx.held} sessions >= {n}", {}
    return None


@register_sizing(
    id="percent_of_equity",
    version="1.0.0",
    label="Percent of equity",
    params=(),
    description=(
        "floor(target_allocation x equity at the decision close / decision close): the "
        "portfolio's target allocation of current equity, in whole shares."
    ),
)
def percent_of_equity(ctx: SizingContext, params: dict[str, Any]) -> int:
    return int((ctx.target_allocation * ctx.equity) // ctx.close)
