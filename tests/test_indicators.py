from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from moneylab.components.builtin import rsi, sma
from moneylab.components.registry import get_component


def _bars(close: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": close})


def test_sma_hand_values() -> None:
    out = sma(_bars([1, 2, 3, 4, 5]), {"length": 3}).tolist()
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert out[2:] == [2.0, 3.0, 4.0]


def test_rsi_hand_values() -> None:
    # changes: +1, -1, +2, -1 ; length 2
    # first avg (changes 1-2): gain 0.5, loss 0.5 -> RSI 50 at index 2
    # idx 3: gain (0.5*1+2)/2 = 1.25, loss (0.5*1+0)/2 = 0.25 -> RS 5 -> 83.333...
    # idx 4: gain (1.25+0)/2 = 0.625, loss (0.25+1)/2 = 0.625 -> 50
    out = rsi(_bars([10, 11, 10, 12, 11]), {"length": 2}).tolist()
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert out[2] == pytest.approx(50.0)
    assert out[3] == pytest.approx(100 - 100 / 6)
    assert out[4] == pytest.approx(50.0)


def test_rsi_missing_bar_restarts_warmup() -> None:
    out = rsi(_bars([10, 11, 10, np.nan, 12, 11, 12]), {"length": 2}).to_numpy()
    assert not math.isnan(out[2])
    assert np.isnan(out[3:6]).all()  # gap, then two sessions of warm-up
    assert not math.isnan(out[6])


def test_param_validation() -> None:
    decl = get_component("rsi")
    assert decl.resolve_params({}) == {"length": 14}
    with pytest.raises(ValueError):
        decl.resolve_params({"length": 1})
    with pytest.raises(ValueError):
        decl.resolve_params({"lenght": 14})
