"""Trend straightness: signed progress divided by the path taken."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from moneylab.components import get_component, list_components
from moneylab.engine.config import Condition, IndicatorRef, resolve
from moneylab.settings import Settings

NAN = float("nan")


def compute(frame: pd.DataFrame, **params: object) -> np.ndarray:
    decl = get_component("straightness")
    assert decl.compute is not None
    return decl.compute(frame, decl.resolve_params(params)).to_numpy(dtype=float)


def closes(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": values})


def test_declaration() -> None:
    decl = get_component("straightness")
    assert decl in list_components("indicator")
    assert decl.version == "1.1.0"
    assert decl.inputs == ("close",)
    assert decl.price_scale is False
    assert decl.resolve_params({}) == {"length": 20, "output": "signed"}
    assert decl.description


def test_net_progress_over_travel() -> None:
    # length 4. At idx 4, net = 110 - 100 = 10.
    # Travel = |105-100| + |100-105| + |108-100| + |110-108| = 5+5+8+2 = 20.
    # Score = 10/20 = 0.5. Earlier bars have no full window.
    got = compute(closes([100, 105, 100, 108, 110]), length=4)
    np.testing.assert_allclose(got, [NAN, NAN, NAN, NAN, 0.5], rtol=0, atol=1e-12)


def test_straight_rise_drop_and_flat() -> None:
    rise = compute(closes([10, 11, 12, 13]), length=3)
    drop = compute(closes([13, 12, 11, 10]), length=3)
    flat = compute(closes([5, 5, 5]), length=2)
    np.testing.assert_allclose(rise, [NAN, NAN, NAN, 1.0], rtol=0, atol=1e-12)
    np.testing.assert_allclose(drop, [NAN, NAN, NAN, -1.0], rtol=0, atol=1e-12)
    np.testing.assert_allclose(flat, [NAN, NAN, 0.0], rtol=0, atol=1e-12)


def test_missing_close_blanks_the_window() -> None:
    # length 2. idx 3 spans closes 1..3 and idx 4 spans 2..4; both include the gap.
    frame = closes([10, 11, 12, 13, 14])
    frame.loc[2, "close"] = NAN
    got = compute(frame, length=2)
    np.testing.assert_allclose(got, [NAN, NAN, NAN, NAN, NAN], rtol=0, atol=1e-12)


def test_later_bars_do_not_change_earlier_scores() -> None:
    full = compute(closes([10, 11, 10, 12, 13, 11, 14]), length=3)
    changed = closes([10, 11, 10, 12, 13, 11, 14])
    changed.loc[4:, "close"] = changed.loc[4:, "close"] * 3
    got = compute(changed, length=3)
    np.testing.assert_array_equal(got[:4], full[:4])


def test_length_bounds() -> None:
    with pytest.raises(ValueError, match=re.escape("length must be >= 2")):
        get_component("straightness").resolve_params({"length": 1})
    with pytest.raises(ValueError, match=re.escape("length must be <= 400")):
        get_component("straightness").resolve_params({"length": 401})
    cond = Condition(
        kind="threshold",
        indicator=IndicatorRef(id="straightness", params={"length": 1}),
        params={"op": "above", "value": 0},
    )
    from moneylab.engine.config import ExitRule, PortfolioSettings, StrategyConfig

    cfg = StrategyConfig(
        name="fixture",
        universe=["XA"],
        benchmark="XA",
        timeframe="1d",
        entry=[cond],
        exit=ExitRule(params={"sessions": 2}),
        portfolio=PortfolioSettings(),
    )
    with pytest.raises(ValueError, match=re.escape("length must be >= 2")):
        resolve(cfg, Settings(_env_file=None))


def test_unsigned_is_the_efficiency_ratio() -> None:
    # Closes 10, 11, 10, 11, 12 with length 4: net 2, path 4 -> 0.5 either way.
    # A fall 12, 11, 12, 11, 10: signed -0.5, unsigned 0.5.
    up = closes([10.0, 11, 10, 11, 12])
    down = closes([12.0, 11, 12, 11, 10])
    assert compute(up, length=4, output="unsigned")[4] == 0.5
    assert compute(down, length=4)[4] == -0.5
    assert compute(down, length=4, output="unsigned")[4] == 0.5
