"""Swing points and trend indicators (ADX, regression trend, market structure).

Expected values are worked out by hand next to each assertion. The causality test
checks the central rule: a swing point is only used once it is confirmed, so no value
ever changes when later bars change.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fixtures_util import bars, day, decisions, fills, run
from moneylab.components import get_component
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    resolve,
)
from moneylab.settings import Settings

NAN = float("nan")
NEW = ("swing_high", "swing_low", "adx", "linreg_trend", "market_structure")


def compute(ind: str, frame: pd.DataFrame, **params: Any) -> np.ndarray:
    decl = get_component(ind)
    assert decl.compute is not None
    return decl.compute(frame, decl.resolve_params(params)).to_numpy(dtype=float)


def hlc(high: list[float], low: list[float], close: list[float] | None = None) -> pd.DataFrame:
    close = close if close is not None else [(h + lo) / 2 for h, lo in zip(high, low, strict=True)]
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close})


def same(got: np.ndarray, expected: list[float]) -> None:
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-9)


# Declarations ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ind", "price_scale"),
    [
        ("swing_high", True),
        ("swing_low", True),
        ("adx", False),
        ("linreg_trend", False),
        ("market_structure", False),
    ],
)
def test_declared_as_indicators(ind: str, price_scale: bool) -> None:
    d = get_component(ind)
    assert d.uses == ("indicator",)
    assert d.price_scale is price_scale
    assert d.description


# Swing points ----------------------------------------------------------------------


def test_swing_high_is_known_only_after_the_right_bars() -> None:
    # left = right = 2. Highs 1,2,5,3,2,4,6,4,3,2. Idx 2 (5): above 1,2 and >= 3,2 -> swing,
    # confirmed at idx 4. Idx 6 (6): above 2,4 and >= 4,3 -> swing, confirmed at idx 8.
    # Idx 5 (4) is not: 6 follows it.
    high = [1.0, 2, 5, 3, 2, 4, 6, 4, 3, 2]
    got = compute("swing_high", hlc(high, [h - 1 for h in high]), left=2, right=2)
    same(got, [NAN, NAN, NAN, NAN, 5, 5, 5, 5, 6, 6])


def test_swing_low_mirrors_with_lows() -> None:
    # Lows 0,1,4,2,1,3,5,3,2,1. Idx 4 (1): below 4,2 and <= 3,5 -> swing, known at idx 6.
    # Idx 8/9 cannot be judged yet (fewer than 2 bars after them).
    high = [1.0, 2, 5, 3, 2, 4, 6, 4, 3, 2]
    got = compute("swing_low", hlc(high, [h - 1 for h in high]), left=2, right=2)
    same(got, [NAN] * 6 + [1, 1, 1, 1])


def test_flat_top_counts_once_and_a_missing_bar_blocks_a_swing() -> None:
    # Highs 1, 3, 3, 1, 1 with left = right = 1: idx 1 is a swing (3 > 1, 3 >= 3); idx 2 is
    # not (3 is not above 3). Known at idx 2.
    got = compute("swing_high", hlc([1.0, 3, 3, 1, 1], [0.0, 2, 2, 0, 0]), left=1, right=1)
    same(got, [NAN, NAN, 3, 3, 3])
    # The same with idx 0 missing: idx 1 has no full window, idx 2 still fails -> none.
    got = compute("swing_high", hlc([NAN, 3, 3, 1, 1], [NAN, 2, 2, 0, 0]), left=1, right=1)
    assert np.isnan(got).all()


# Trend -------------------------------------------------------------------------------


def test_adx_of_a_steady_climb() -> None:
    # Every session: high +1, low +1, so +DM = 1, -DM = 0. Low_i = i + 1, high = low + 1,
    # close = low + 0.5; TR = max(1, |high - prev close| = 1.5, |low - prev close| = 0.5)
    # = 1.5. +DI = 100 x 1 / 1.5, -DI = 0, DX = 100, ADX = 100. length 3: DI from idx 3,
    # ADX from idx 2 x 3 - 1 = 5.
    low = [float(i + 1) for i in range(9)]
    frame = hlc([x + 1 for x in low], low, [x + 0.5 for x in low])
    same(compute("adx", frame, length=3, output="plus_di"), [NAN] * 3 + [100 / 1.5] * 6)
    same(compute("adx", frame, length=3, output="minus_di"), [NAN] * 3 + [0.0] * 6)
    same(compute("adx", frame, length=3), [NAN] * 5 + [100.0] * 4)


def test_adx_is_low_in_a_range() -> None:
    # Alternating up and down bars of the same size: +DM and -DM take turns, so the
    # smoothed +DI and -DI end up close and ADX stays well under the usual 25 cut-off.
    low = [10.0, 11.0] * 30
    frame = hlc([x + 1 for x in low], low)
    assert compute("adx", frame, length=14)[-1] < 25


def test_linreg_trend_of_exponential_growth() -> None:
    # ln(close) = ln(100) + 0.001 t exactly: slope 0.001 per session, R^2 = 1, and the
    # t-statistic is undefined (no residual).
    close = [100 * math.exp(0.001 * t) for t in range(8)]
    frame = hlc(close, close, close)
    slope = compute("linreg_trend", frame, length=5)
    same(slope, [NAN] * 4 + [(math.exp(0.252) - 1) * 100] * 4)
    same(compute("linreg_trend", frame, length=5, output="r2"), [NAN] * 4 + [1.0] * 4)
    assert np.isnan(compute("linreg_trend", frame, length=5, output="tstat")).all()


def test_linreg_tstat_by_hand() -> None:
    # y = ln(close) = 0, 1, 1 (closes 1, e, e), x = 0, 1, 2. x mean 1, y mean 2/3.
    # Sxx = 2, Sxy = (-1)(-2/3) + 0 + (1)(1/3) = 1 -> slope 0.5.
    # Residuals: y - (2/3 + 0.5 (x - 1)) = -1/6, 1/3, -1/6 -> SSE = 1/36 + 4/36 + 1/36 = 1/6.
    # se = sqrt(SSE / (3 - 2) / Sxx) = sqrt(1/12); t = 0.5 / sqrt(1/12) = sqrt(3).
    close = [1.0, math.e, math.e]
    got = compute("linreg_trend", hlc(close, close, close), length=3, output="tstat")
    same(got, [NAN, NAN, math.sqrt(3)])
    # R^2 = 1 - SSE / SST; SST = 4/9 + 1/9 + 1/9 = 2/3 -> 1 - (1/6) / (2/3) = 0.75.
    got = compute("linreg_trend", hlc(close, close, close), length=3, output="r2")
    same(got, [NAN, NAN, 0.75])


UP_H = [1.0, 3, 2, 4, 3, 5, 4, 6, 5]
UP_L = [h - 1 for h in UP_H]


def test_market_structure_up_and_down() -> None:
    # left = right = 1. Swing highs at idx 1, 3, 5, 7 (3 < 4 < 5 < 6), known one bar later;
    # swing lows at idx 2, 4, 6 (1 < 2 < 3). The second low is known at idx 5, so from
    # there: higher high and higher low -> +1.
    got = compute("market_structure", hlc(UP_H, UP_L), left=1, right=1)
    same(got, [NAN] * 5 + [1, 1, 1, 1])
    # Mirror the prices: every swing reverses -> -1 from the same session.
    down = hlc([10 - x for x in UP_L], [10 - x for x in UP_H])
    same(compute("market_structure", down, left=1, right=1), [NAN] * 5 + [-1, -1, -1, -1])


def test_market_structure_mixed_is_zero() -> None:
    # Higher highs but a lower low: highs 3 then 4 (idx 1, 3); lows 1 then 0.5 (idx 2, 4).
    high = [1.0, 3, 2, 4, 2, 3]
    low = [0.0, 2, 1, 3, 0.5, 2]
    got = compute("market_structure", hlc(high, low), left=1, right=1)
    same(got, [NAN] * 5 + [0])


# Causality and validation for all of them -------------------------------------------


def random_frame(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + rng.uniform(0, 2, n),
            "low": close - rng.uniform(0, 2, n),
            "close": close,
            "volume": rng.integers(1_000, 10_000, n).astype(float),
        }
    )


SMALL: dict[str, dict[str, Any]] = {
    "swing_high": {"left": 3, "right": 2},
    "swing_low": {"left": 3, "right": 2},
    "adx": {"length": 7},
    "linreg_trend": {"length": 10, "output": "tstat"},
    "market_structure": {"left": 2, "right": 2},
}


@pytest.mark.parametrize("ind", NEW)
def test_causal_prefix_unchanged_when_later_bars_change(ind: str) -> None:
    frame = random_frame(120, 7)
    full = compute(ind, frame, **SMALL[ind])
    assert not np.isnan(full[-30:]).all(), "produces values on random data"
    for cut in (15, 47, 90, 119):
        np.testing.assert_array_equal(compute(ind, frame.iloc[:cut], **SMALL[ind]), full[:cut])
        changed = frame.copy()
        changed.iloc[cut:] = changed.iloc[cut:] * 3.0
        np.testing.assert_array_equal(compute(ind, changed, **SMALL[ind])[:cut], full[:cut])


@pytest.mark.parametrize(
    ("ind", "params", "message"),
    [
        ("swing_high", {"right": 0}, "right must be >= 1"),
        ("adx", {"output": "dx"}, "output must be one of"),
        ("linreg_trend", {"length": 2}, "length must be >= 3"),
    ],
)
def test_parameter_validation(ind: str, params: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        get_component(ind).resolve_params(params)


# Through the engine -----------------------------------------------------------------


def strategy(entry: list[Condition], hold: int = 1) -> StrategyConfig:
    return StrategyConfig(
        name="fixture",
        universe=["XA"],
        benchmark="XA",
        timeframe="1d",
        entry=entry,
        exit=ExitRule(params={"sessions": hold}),
        portfolio=PortfolioSettings(starting_cash=1000, target_allocation=0.5, cost_bps_per_side=0),
    )


def test_price_cross_needs_a_price_level_and_di_lines_compare() -> None:
    settings = Settings(_env_file=None)
    bad = Condition(
        kind="price_cross", indicator=IndicatorRef(id="adx"), params={"direction": "above"}
    )
    with pytest.raises(ValueError, match="not in price units"):
        resolve(strategy([bad]), settings)
    # +DI above -DI: the same indicator with a different output is allowed.
    di = Condition(
        kind="indicator_compare",
        indicator=IndicatorRef(id="adx", params={"output": "plus_di"}),
        other=IndicatorRef(id="adx", params={"output": "minus_di"}),
        params={"op": "above"},
    )
    resolved = resolve(strategy([di]), settings)
    assert resolved["entry"][0]["other"]["params"]["output"] == "minus_di"


def test_breakout_above_the_last_swing_high_enters() -> None:
    # (open, high, low, close). Swing high at idx 1 (high 12 > 10, >= 11), known at idx 2
    # with left = right = 1. Idx 4: previous close 11 <= 12, close 12.5 > 12 -> breakout.
    # Sized at the idx 4 close: floor(500 / 12.5) = 40 shares, filled at the idx 5 open of 13.
    spec = [
        (10.0, 10.0, 9.0, 10.0),
        (11.0, 12.0, 10.5, 11.0),
        (11.0, 11.0, 10.0, 10.5),
        (10.5, 11.5, 10.0, 11.0),
        (11.0, 12.6, 11.0, 12.5),
        (13.0, 13.0, 12.5, 13.0),
        (13.0, 13.0, 12.5, 13.0),
    ]
    cond = Condition(
        kind="price_cross",
        indicator=IndicatorRef(id="swing_high", params={"left": 1, "right": 1}),
        params={"direction": "above"},
    )
    r = run(bars({"XA": spec}), strategy([cond]))
    assert decisions(r, "enter") == [(day(4), "XA", "enter", "all entry conditions passed")]
    assert fills(r)[0][:5] == (day(5), "XA", "buy", 40, 13.0)


def test_linreg_slope_is_annualised_by_the_bar_size() -> None:
    # Hourly bars (seven a day): ln(close) rises 0.001 per bar -> a year is 1764 bars.
    stamps = pd.DatetimeIndex(
        [f"2024-01-02 {h:02d}:00" for h in range(9, 16)] + ["2024-01-03 09:00"]
    )
    close = [100 * math.exp(0.001 * t) for t in range(len(stamps))]
    frame = hlc(close, close, close)
    frame.index = stamps
    got = compute("linreg_trend", frame, length=5)
    np.testing.assert_allclose(got[4:], (math.exp(0.001 * 1764) - 1) * 100, rtol=1e-12)
