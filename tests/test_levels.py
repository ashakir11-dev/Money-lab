"""Support and resistance from turns that clear the usual bar size."""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fixtures_util import bars, day, decisions, fills, run
from moneylab.components import get_component, list_components
from moneylab.components.levels import Area, choose_level
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
)

NAN = float("nan")
LEVELS = (
    "support",
    "resistance",
    "support_tests",
    "resistance_tests",
    "support_distance",
    "resistance_distance",
)
PARAMS = {"min_move": 2.0, "zone": 1.0, "lookback": 20, "atr_length": 1}


def compute(ind: str, frame: pd.DataFrame, **params: Any) -> np.ndarray:
    decl = get_component(ind)
    assert decl.compute is not None
    return decl.compute(frame, decl.resolve_params(params)).to_numpy(dtype=float)


def ohlc(rows: list[tuple[float, float, float] | None]) -> pd.DataFrame:
    """(high, low, close); None is a missing bar."""
    table = []
    for row in rows:
        if row is None:
            table.append((NAN, NAN, NAN))
        else:
            table.append(row)
    return pd.DataFrame(table, columns=["high", "low", "close"])


def assert_values(got: np.ndarray, expected: list[float]) -> None:
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12, equal_nan=True)


# Hand-traced with atr_length=1, so the usual bar equals the true range, and every
# true range below is 1. min_move=2, zone=1.
#
# idx 3 confirms the low 9 from idx 0 (the high has risen by 2 while this bar's
# usual size is still 1). idx 6 only ties the high 12, so the extreme stays idx 5.
# idx 8 confirms that high. idx 10 confirms the low 9.5. The two lows are 0.5 apart,
# inside one usual bar, so the area is their middle, 9.25, with 2 tests. The high
# at 12 is its own area.
TRACE = ohlc(
    [
        (10.0, 9.0, 9.0),
        (10.0, 9.0, 9.5),
        (10.5, 9.5, 10.0),
        (11.0, 10.0, 10.5),
        (11.5, 10.5, 11.0),
        (12.0, 11.0, 11.5),
        (12.0, 11.0, 11.5),
        (11.5, 10.5, 10.5),
        (10.5, 9.5, 9.5),
        (10.5, 9.5, 10.5),
        (11.5, 10.5, 11.5),
    ]
)


def test_declarations() -> None:
    expected = {
        "support": True,
        "resistance": True,
        "support_tests": False,
        "resistance_tests": False,
        "support_distance": False,
        "resistance_distance": False,
    }
    for ind, price_scale in expected.items():
        decl = get_component(ind)
        assert decl in list_components("indicator")
        assert decl.version == "1.0.0"
        assert decl.inputs == ("high", "low", "close")
        assert decl.price_scale is price_scale
        assert decl.resolve_params({}) == {
            "min_move": 2.0,
            "zone": 1.0,
            "lookback": 120,
            "atr_length": 14,
        }
        assert decl.description


def test_small_moves_leave_the_level_blank() -> None:
    # idx 0 and 1 move by less than 2 usual bars, and idx 0 has no previous close.
    support = compute("support", TRACE, **PARAMS)
    assert_values(support[:3], [NAN, NAN, NAN])


def test_reversal_confirms_on_the_threshold_bar_and_not_before() -> None:
    support = compute("support", TRACE, **PARAMS)
    # idx 2 is still short of a 2-point rise from 9. idx 3 is the first bar that clears it.
    assert math.isnan(support[2])
    assert support[3] == 9.0


def test_flat_extreme_keeps_the_first_bar() -> None:
    # The high 12 prints at idx 5 and only ties at idx 6, so the area confirmed at
    # idx 8 is 12, not a later bar's price. One test: the tied bar is not a second turn.
    resistance = compute("resistance", TRACE, **PARAMS)
    tests = compute("resistance_tests", TRACE, **PARAMS)
    assert resistance[8] == 12.0
    assert tests[8] == 1.0


def test_two_turns_inside_the_zone_share_one_area() -> None:
    # Lows at 9 and 9.5. Middle 9.25, two tests. The high at 12 stands alone.
    # Previous close at idx 10 is 10.5, so 9.25 is support and 12 is resistance.
    assert compute("support", TRACE, **PARAMS)[10] == 9.25
    assert compute("support_tests", TRACE, **PARAMS)[10] == 2.0
    assert compute("resistance", TRACE, **PARAMS)[10] == 12.0
    assert compute("resistance_tests", TRACE, **PARAMS)[10] == 1.0


def test_one_turn_area_is_support_until_a_second_turn_joins_it() -> None:
    support = compute("support", TRACE, **PARAMS)
    tests = compute("support_tests", TRACE, **PARAMS)
    resistance = compute("resistance", TRACE, **PARAMS)
    # idx 3: only the low at 9, and it is under the previous close 10. No area above it.
    assert support[3] == 9.0
    assert tests[3] == 1.0
    assert math.isnan(resistance[3])
    # idx 8: the high has confirmed and the second low has not, so support is still one test.
    assert support[8] == 9.0
    assert tests[8] == 1.0
    assert resistance[8] == 12.0


def test_distance_is_the_gap_in_usual_bars() -> None:
    # idx 10 close 11.5, support 9.25, resistance 12, usual bar 1.
    assert compute("support_distance", TRACE, **PARAMS)[10] == pytest.approx(2.25)
    assert compute("resistance_distance", TRACE, **PARAMS)[10] == pytest.approx(0.5)
    # idx 3 has support 9 and close 10.5, and no resistance.
    assert compute("support_distance", TRACE, **PARAMS)[3] == pytest.approx(1.5)
    assert math.isnan(compute("resistance_distance", TRACE, **PARAMS)[3])


def test_zero_usual_bar_blanks_the_levels() -> None:
    flat = pd.concat(
        [TRACE, ohlc([(11.5, 11.5, 11.5)])],
        ignore_index=True,
    )
    for ind in LEVELS:
        assert math.isnan(compute(ind, flat, **PARAMS)[11])


def test_missing_bar_starts_a_new_run() -> None:
    # The low confirmed at idx 3 must not survive the gap.
    gapped = pd.concat([TRACE.iloc[:4], ohlc([None, (11.0, 10.0, 10.5)])], ignore_index=True)
    support = compute("support", gapped, **PARAMS)
    assert support[3] == 9.0
    assert math.isnan(support[4])
    assert math.isnan(support[5])


def test_later_bars_do_not_change_earlier_levels() -> None:
    full = compute("support", TRACE, **PARAMS)
    changed = TRACE.copy()
    changed.loc[8:, ["high", "low", "close"]] = changed.loc[8:, ["high", "low", "close"]] + 20
    got = compute("support", changed, **PARAMS)
    np.testing.assert_array_equal(got[:8], full[:8])


def test_zone_must_be_narrower_than_the_minimum_move() -> None:
    with pytest.raises(ValueError, match=re.escape("zone (1.0) must be less than min_move (1.0)")):
        get_component("support").resolve_params({"min_move": 1.0, "zone": 1.0})
    with pytest.raises(ValueError, match=re.escape("lookback must be >= 20")):
        get_component("support").resolve_params({"lookback": 19})
    with pytest.raises(ValueError, match=re.escape("lookback must be <= 2000")):
        get_component("support").resolve_params({"lookback": 2001})
    with pytest.raises(ValueError, match=re.escape("min_move must be >= 0.5")):
        get_component("resistance").resolve_params({"min_move": 0.4})


def test_area_equal_to_the_previous_close_is_both() -> None:
    # atr_length 3 keeps the usual bar at 1. The low 10.5 (idx 2) confirms at idx 4,
    # once the high has risen by 2. The high 14.5 (idx 8) confirms at idx 10, once
    # the low has fallen by 2. At idx 16 the previous close is 10.5, so that area is
    # both support and resistance; the high 14.5 is the farther area above it.
    rows = [(c + 0.5, c - 0.5, c) for c in (10, 10.5, 11)]
    price = 11.0
    for _ in range(6):
        price += 0.5
        rows.append((price + 0.5, price - 0.5, price))
    for _ in range(8):
        price -= 0.5
        rows.append((price + 0.5, price - 0.5, price))
    frame = ohlc(rows)
    params = {"min_move": 2.0, "zone": 1.0, "lookback": 50, "atr_length": 3}
    assert compute("support", frame, **params)[16] == 10.5
    assert compute("resistance", frame, **params)[16] == 10.5
    assert compute("support_tests", frame, **params)[16] == 1.0
    assert compute("resistance_tests", frame, **params)[16] == 1.0


def test_same_price_prefers_more_tests_then_the_later_turn() -> None:
    thin = Area(price=10.0, tests=1, latest=4)
    thick = Area(price=10.0, tests=3, latest=2)
    later = Area(price=10.0, tests=3, latest=6)
    higher = Area(price=11.0, tests=1, latest=1)
    assert choose_level([thin, thick, later, higher], side="support") == higher
    assert choose_level([thin, thick, later], side="support") == later
    assert choose_level([thin, thick, later, higher], side="resistance") == later


def _strategy(entry: list[Condition]) -> StrategyConfig:
    return StrategyConfig(
        name="fixture",
        universe=["XA"],
        benchmark="XA",
        timeframe="1d",
        entry=entry,
        exit=ExitRule(params={"sessions": 2}),
        portfolio=PortfolioSettings(starting_cash=1000, target_allocation=0.5, cost_bps_per_side=0),
    )


def _level_ref(ind: str) -> IndicatorRef:
    return IndicatorRef(id=ind, params=PARAMS)


def test_close_crossing_above_resistance_enters() -> None:
    # idx 11 close 12.5 crosses above resistance 12. The previous close 11.5 was at or
    # below the previous resistance 12. Filled at the idx 12 open of 12.5:
    # floor(500 / 12.5) = 40 shares, cash 1000 - 500 = 500.
    spec: list[tuple[float, float, float, float]] = []
    for high, low, close in TRACE.itertuples(index=False):
        spec.append((close, high, low, close))
    spec.append((11.5, 12.5, 11.5, 12.5))
    spec.append((12.5, 12.5, 11.5, 12.5))
    cond = Condition(
        kind="price_cross",
        indicator=_level_ref("resistance"),
        params={"direction": "above"},
    )
    result = run(bars({"XA": spec}), _strategy([cond]))
    assert decisions(result, "enter") == [(day(11), "XA", "enter", "all entry conditions passed")]
    assert fills(result) == [(day(12), "XA", "buy", 40, 12.5, 0.0, 500.0)]
    key = "resistance(atr_length=1,lookback=20,min_move=2.0,zone=1.0)"
    row = result.decisions[result.decisions["action"] == "enter"].iloc[0]
    assert row["values"][key] == 12.0
    assert row["values"][f"prev {key}"] == 12.0


def test_support_tests_threshold_keeps_the_repeated_area() -> None:
    # support tests are 1 at idx 3 and idx 8, and 2 at idx 10. "above 1" waits for idx 10.
    spec = [(close, high, low, close) for high, low, close in TRACE.itertuples(index=False)]
    spec.append((11.5, 12.5, 11.5, 12.5))
    cond = Condition(
        kind="threshold",
        indicator=_level_ref("support_tests"),
        params={"op": "above", "value": 1},
    )
    result = run(bars({"XA": spec}), _strategy([cond]))
    assert decisions(result, "enter") == [(day(10), "XA", "enter", "all entry conditions passed")]
