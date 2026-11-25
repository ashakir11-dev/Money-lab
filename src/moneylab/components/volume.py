"""Average daily volume over the last `length` sessions."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from moneylab.components.registry import ParamSpec, register_indicator


@register_indicator(
    id="avg_volume",
    version="1.0.0",
    label="Average volume",
    params=(ParamSpec("length", "int", 20, "Lookback (sessions)", min=1, max=400),),
    inputs=("volume",),
    description=(
        "Mean volume (shares) over the last `length` sessions including the current one. "
        "First value at 0-based index length-1. Undefined while any session in the window "
        "has no bar (a gap is not counted as zero volume)."
    ),
)
def avg_volume(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    vol = bars["volume"].to_numpy(dtype=float)
    out = np.full(vol.shape, np.nan)
    if vol.size >= n:
        # Mean of each window on its own: exact, and unaffected by later bars.
        out[n - 1 :] = sliding_window_view(vol, n).mean(axis=1)
    return pd.Series(out, index=bars.index)
