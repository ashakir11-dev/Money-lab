"""MACD histogram slope: this session's histogram minus the previous session's."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.macd import macd
from moneylab.components.registry import ParamSpec, register_indicator


def _check(params: dict[str, Any]) -> None:
    if params["fast"] >= params["slow"]:
        raise ValueError(
            f"macd_slope: fast ({params['fast']}) must be shorter than slow ({params['slow']})"
        )


@register_indicator(
    id="macd_slope",
    version="1.0.0",
    label="MACD histogram slope",
    params=(
        ParamSpec("fast", "int", 12, "Fast EMA (sessions)", min=1, max=200),
        ParamSpec("slow", "int", 26, "Slow EMA (sessions)", min=2, max=400),
        ParamSpec("signal", "int", 9, "Signal EMA (sessions)", min=1, max=200),
    ),
    inputs=("close",),
    description=(
        "MACD histogram at this session minus the histogram at the previous session. "
        "The first session and any session whose histogram is still warming up are missing."
    ),
    check=_check,
)
def macd_slope(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    histogram = macd(bars, {**params, "output": "histogram"}).to_numpy(dtype=float)
    out = np.full(histogram.shape, np.nan)
    if histogram.size > 1:
        out[1:] = histogram[1:] - histogram[:-1]
    return pd.Series(out, index=bars.index)
