"""Relative volume: this bar's volume divided by the average volume."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator
from moneylab.components.volume import avg_volume


@register_indicator(
    id="relative_volume",
    version="1.0.0",
    label="Relative volume",
    params=(ParamSpec("length", "int", 20, "Average length (sessions)", min=1, max=400),),
    inputs=("volume",),
    description=(
        "Volume divided by the average volume of the last `length` sessions, including "
        "this one. Undefined until that average exists, and undefined when the average "
        "is zero. A missing bar is not treated as zero volume."
    ),
)
def relative_volume(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    base = avg_volume(bars, {"length": int(params["length"])}).to_numpy(dtype=float)
    volume = bars["volume"].to_numpy(dtype=float)
    out = np.full(base.shape, np.nan)
    ok = np.isfinite(base) & np.isfinite(volume) & (base != 0)
    out[ok] = volume[ok] / base[ok]
    return pd.Series(out, index=bars.index)
