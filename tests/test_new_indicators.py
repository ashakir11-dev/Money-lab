"""EMA, Bollinger %B, ROC, highest high / lowest low and average volume.

Each indicator is one new file in src/moneylab/components/. Values are worked out by
hand in the comments next to each assertion; strategies run through the real config
resolution and simulator (tests/fixtures_util.run).
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fixtures_util import SESSIONS, bars, day, decisions, fills, run
from moneylab.components import get_component, list_components
from moneylab.data.snapshot import normalise
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
NEW = ("ema", "bollinger_pctb", "roc", "highest_high", "lowest_low", "avg_volume")


def compute(ind: str, frame: pd.DataFrame, **params: Any) -> np.ndarray:
    decl = get_component(ind)
    assert decl.compute is not None
    return decl.compute(frame, decl.resolve_params(params)).to_numpy(dtype=float)


def closes(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": values})


def assert_values(got: np.ndarray, expected: list[float]) -> None:
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)


# Declarations ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ind", "inputs", "price_scale", "defaults"),
    [
        ("ema", ("close",), True, {"length": 20}),
        ("bollinger_pctb", ("close",), False, {"length": 20, "stdevs": 2.0}),
        ("roc", ("close",), False, {"length": 20}),
        ("highest_high", ("high",), True, {"length": 20}),
        ("lowest_low", ("low",), True, {"length": 20}),
        ("avg_volume", ("volume",), False, {"length": 20}),
    ],
)
def test_declarations(
    ind: str, inputs: tuple[str, ...], price_scale: bool, defaults: dict[str, Any]
) -> None:
    decl = get_component(ind)
    assert decl in list_components("indicator")
    assert decl.version == "1.0.0"
    assert decl.inputs == inputs
    assert decl.price_scale is price_scale
    assert decl.resolve_params({}) == defaults
    assert decl.description


# EMA -------------------------------------------------------------------------------


def test_ema_hand_values() -> None:
    # length 3, alpha = 2/4 = 0.5. Seed at idx 2 = (10+12+11)/3 = 11.
    # idx3 = 11 + .5(13-11) = 12;  idx4 = 12 + .5(15-12) = 13.5;  idx5 = 13.5 + .5(14-13.5)
    # = 13.75
    got = compute("ema", closes([10, 12, 11, 13, 15, 14]), length=3)
    assert_values(got, [NAN, NAN, 11.0, 12.0, 13.5, 13.75])


def test_ema_length_4_alpha_is_two_fifths() -> None:
    # alpha = 2/5 = 0.4. Seed at idx 3 = (10+10+10+10)/4 = 10; idx4 = 10 + .4(14-10) = 11.6;
    # idx5 = 11.6 + .4(14-11.6) = 12.56
    got = compute("ema", closes([10, 10, 10, 10, 14, 14]), length=4)
    assert_values(got, [NAN, NAN, NAN, 10.0, 11.6, 12.56])


def test_ema_length_1_is_the_close() -> None:
    c = [10.0, 12.0, 11.0]
    assert_values(compute("ema", closes(c), length=1), c)


def test_ema_missing_bar_restarts_warmup() -> None:
    # Gap at idx 3; after it the run 20, 22, 24 is seeded afresh: idx 5 = (20+22+24)/3 = 22,
    # idx 6 = 22 + .5(26-22) = 24.
    got = compute("ema", closes([10, 12, 11, NAN, 20, 22, 24, 26]), length=3)
    assert_values(got, [NAN, NAN, 11.0, NAN, NAN, NAN, 22.0, 24.0])


# Bollinger %B ----------------------------------------------------------------------


def test_bollinger_hand_values() -> None:
    # length 4, stdevs 2.
    # idx3 window 10,12,10,12: mean 11, deviations -1,1,-1,1, population var 1, std 1.
    #   bands 9 / 13; %B = (12-9)/(13-9) = 0.75
    # idx4 window 12,10,12,10: same bands; %B = (10-9)/4 = 0.25
    # idx5 window 10,12,10,11: mean 43/4 = 10.75, deviations -.75,1.25,-.75,.25,
    #   squares .5625+1.5625+.5625+.0625 = 2.75, var 2.75/4 = .6875, std sqrt(.6875);
    #   lower = 10.75 - 2 sqrt(.6875); %B = (11 - lower) / (4 sqrt(.6875))
    #   = (.25 + 2 sqrt(.6875)) / (4 sqrt(.6875)); sqrt(.6875) = 0.829156..
    #   -> (0.25 + 1.658312) / 3.316625 = 0.575378..
    s = math.sqrt(0.6875)
    got = compute("bollinger_pctb", closes([10, 12, 10, 12, 10, 11]), length=4, stdevs=2.0)
    assert_values(got, [NAN, NAN, NAN, 0.75, 0.25, (0.25 + 2 * s) / (4 * s)])
    assert got[5] == pytest.approx(0.575378, abs=1e-6)


def test_bollinger_stdevs_one_puts_an_up_move_on_the_upper_band() -> None:
    # length 2, stdevs 1: window (10, 12) mean 11, std 1 -> bands 10 / 12, %B = 1.
    # Window (12, 9): mean 10.5, std 1.5 -> bands 9 / 12, %B = 0.
    got = compute("bollinger_pctb", closes([10, 12, 9]), length=2, stdevs=1.0)
    assert_values(got, [NAN, 1.0, 0.0])


def test_bollinger_equal_bands_are_undefined() -> None:
    # 0.1 x 3: the float mean is not exactly 0.1, but the window is flat, so NaN.
    got = compute("bollinger_pctb", closes([0.1, 0.1, 0.1, 0.1, 0.2]), length=3)
    assert np.isnan(got[:4]).all()
    # idx4 window .1,.1,.2: close .2 is above the mean, %B > 0.5.
    assert got[4] > 0.5


def test_bollinger_undefined_while_a_gap_is_in_the_window() -> None:
    got = compute("bollinger_pctb", closes([10, 12, 10, NAN, 12, 10, 11, 12]), length=3)
    assert not np.isnan(got[2])
    assert np.isnan(got[3:6]).all()  # gap at idx 3 is in the windows ending 3, 4, 5
    assert not np.isnan(got[6])


# ROC -------------------------------------------------------------------------------


def test_roc_hand_values() -> None:
    # length 2: idx2 = 11/10 - 1 = 0.1; idx3 = 9.9/12 - 1 = -0.175; idx4 = 13.2/11 - 1 = 0.2
    got = compute("roc", closes([10, 12, 11, 9.9, 13.2]), length=2)
    assert_values(got, [NAN, NAN, 11 / 10 - 1, 9.9 / 12 - 1, 13.2 / 11 - 1])
    assert got[3] == pytest.approx(-0.175)
    assert got[4] == pytest.approx(0.2)


def test_roc_is_undefined_when_either_bar_is_missing() -> None:
    # length 2; idx 2 missing: idx 2 (today missing) and idx 4 (base missing) are NaN.
    # idx3 = 12/12 - 1 = 0; idx5 = 15/12 - 1 = 0.25
    got = compute("roc", closes([10, 12, NAN, 12, 14, 15]), length=2)
    assert_values(got, [NAN, NAN, NAN, 0.0, NAN, 0.25])


# Highest high / lowest low ---------------------------------------------------------


def ohlc(high: list[float], low: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"high": high, "low": low, "close": low})


def test_extremes_exclude_today() -> None:
    # length 3 over highs 5, 7, 6, 9, 4, 8 and lows 1, 3, 2, 4, 0, 2:
    # idx3 = max(5,7,6) = 7, min(1,3,2) = 1; idx4 = max(7,6,9) = 9, min(3,2,4) = 2;
    # idx5 = max(6,9,4) = 9, min(2,4,0) = 0. Today's 9 (idx 3) only counts from idx 4.
    f = ohlc([5, 7, 6, 9, 4, 8], [1, 3, 2, 4, 0, 2])
    assert_values(compute("highest_high", f, length=3), [NAN, NAN, NAN, 7, 9, 9])
    assert_values(compute("lowest_low", f, length=3), [NAN, NAN, NAN, 1, 2, 0])


def test_extremes_need_every_prior_session() -> None:
    # Gap at idx 2: windows (sessions t-2, t-1) holding idx 2 are idx 3 and idx 4.
    f = ohlc([5, 7, NAN, 9, 4, 8], [1, 3, NAN, 4, 0, 2])
    assert_values(compute("highest_high", f, length=2), [NAN, NAN, 7, NAN, NAN, 9])
    assert_values(compute("lowest_low", f, length=2), [NAN, NAN, 1, NAN, NAN, 0])


# Average volume --------------------------------------------------------------------


def test_avg_volume_hand_values_and_gap() -> None:
    # length 3: idx2 = (100+200+300)/3 = 200; idx3 = (200+300+700)/3 = 400; idx4 window
    # holds the gap -> NaN through idx 6; idx7 = (100+200+600)/3 = 300.
    vol = [100, 200, 300, 700, NAN, 100, 200, 600]
    got = compute("avg_volume", pd.DataFrame({"volume": vol}), length=3)
    assert_values(got, [NAN, NAN, 200, 400, NAN, NAN, NAN, 300])


# Warm-up and causality for all of them --------------------------------------------


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


# First defined 0-based index for length 5.
WARMUP = {
    "ema": 4,
    "bollinger_pctb": 4,
    "roc": 5,
    "highest_high": 5,
    "lowest_low": 5,
    "avg_volume": 4,
}


@pytest.mark.parametrize("ind", NEW)
def test_warmup_position(ind: str) -> None:
    got = compute(ind, random_frame(30, 2), length=5)
    first = WARMUP[ind]
    assert np.isnan(got[:first]).all()
    assert not np.isnan(got[first:]).any()


@pytest.mark.parametrize("ind", NEW)
def test_defaults_warm_up_at_documented_index(ind: str) -> None:
    got = compute(ind, random_frame(60, 4))
    first = WARMUP[ind] + 15  # default length 20 instead of 5
    assert np.isnan(got[:first]).all() and not np.isnan(got[first])


@pytest.mark.parametrize("ind", NEW)
def test_causal_prefix_unchanged_when_later_bars_change(ind: str) -> None:
    frame = random_frame(60, 5)
    full = compute(ind, frame, length=7)
    for cut in (10, 33, 59):
        # Truncating the future, or replacing it with very different bars, leaves
        # every value up to the cut exactly as it was.
        np.testing.assert_array_equal(compute(ind, frame.iloc[:cut], length=7), full[:cut])
        changed = frame.copy()
        changed.iloc[cut:] = changed.iloc[cut:] * 3.0
        np.testing.assert_array_equal(compute(ind, changed, length=7)[:cut], full[:cut])


@pytest.mark.parametrize(
    ("ind", "params", "message"),
    [
        ("ema", {"length": 0}, "length must be >= 1"),
        ("ema", {"length": 401}, "length must be <= 400"),
        ("ema", {"length": 2.5}, "length must be a whole number"),
        ("bollinger_pctb", {"length": 1}, "length must be >= 2"),
        ("bollinger_pctb", {"stdevs": 0}, "stdevs must be >= 0.1"),
        ("bollinger_pctb", {"stdevs": "2"}, "stdevs must be a number"),
        ("roc", {"length": 0}, "length must be >= 1"),
        ("highest_high", {"length": 0}, "length must be >= 1"),
        ("lowest_low", {"length": True}, "length must be an integer"),
        ("avg_volume", {"length": 0}, "length must be >= 1"),
        ("avg_volume", {"window": 5}, "unknown parameter"),
    ],
)
def test_parameter_validation(ind: str, params: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        get_component(ind).resolve_params(params)
    # The same error surfaces when a strategy is resolved.
    cond = Condition(
        kind="threshold", indicator=IndicatorRef(id=ind, params=params), params={"value": 0}
    )
    with pytest.raises(ValueError, match=re.escape(message)):
        resolve(strategy([cond]), Settings(_env_file=None))


# End-to-end strategies -------------------------------------------------------------

ENTER = ("enter", "exit")


def strategy(entry: list[Condition], hold: int = 2) -> StrategyConfig:
    return StrategyConfig(
        name="fixture",
        universe=["XA"],
        benchmark="XA",
        entry=entry,
        exit=ExitRule(params={"sessions": hold}),
        portfolio=PortfolioSettings(starting_cash=1000, target_allocation=0.5, cost_bps_per_side=0),
    )


def ind(id: str, **params: Any) -> IndicatorRef:
    return IndicatorRef(id=id, params=params)


def threshold(ref: IndicatorRef, op: str, value: float) -> Condition:
    return Condition(kind="threshold", indicator=ref, params={"op": op, "value": value})


def test_breakout_enters_when_close_crosses_the_prior_highest_high() -> None:
    # (open, close); high = max(open, close): 10, 11, 11, 10.5, 12, 12, 12.
    # highest_high(3): idx3 = max(10,11,11) = 11, idx4 = max(11,11,10.5) = 11,
    # idx5 = max(11,10.5,12) = 12. Idx 4: prev close 10.5 <= prev level 11 and close 12 > 11
    # -> breakout entry. Filled at the idx 5 open of 12: floor(500/12) = 41 shares, cash
    # 1000 - 492 = 508. Held 2 sessions at the idx 6 close -> exit (no later bar to fill).
    spec = [(10.0, 10.0), (10.0, 11.0), (11.0, 10.0), (10.0, 10.5), (10.5, 12.0)]
    spec += [(12.0, 12.0), (12.0, 12.0)]
    cond = Condition(
        kind="price_cross", indicator=ind("highest_high", length=3), params={"direction": "above"}
    )
    r = run(bars({"XA": spec}), strategy([cond]))
    assert decisions(r, *ENTER) == [
        (day(4), "XA", "enter", "all entry conditions passed"),
        (day(6), "XA", "exit", "held 2 sessions >= 2"),
    ]
    assert fills(r) == [(day(5), "XA", "buy", 41, 12.0, 0.0, 508.0)]
    row = r.decisions[r.decisions["action"] == "enter"].iloc[0]
    assert row["values"]["highest_high(length=3)"] == 11.0
    assert row["values"]["prev highest_high(length=3)"] == 11.0


def test_breakdown_below_lowest_low_is_a_valid_exit_signal() -> None:
    # Enter on close > 10.5 at idx 1 (close 11), filled idx 2 open 11: floor(500/11) = 45,
    # cash 505. lowest_low(2) (low = min(open, close)): idx3 = min(10, 11) = 10,
    # idx4 = min(11, 10.5) = 10.5. Idx 4: prev close 10.5 >= prev level 10, close 10 < 10.5
    # -> exit; sold at the idx 5 open of 10: 505 + 450 = 955.
    spec = [(10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (11.0, 10.5), (10.5, 10.0), (10.0, 10.0)]
    cfg = strategy([threshold(ind("sma", length=1), "above", 10.5)])
    cfg.exit = ExitRule(
        kind="signal",
        conditions=[
            Condition(
                kind="price_cross",
                indicator=ind("lowest_low", length=2),
                params={"direction": "below"},
            )
        ],
    )
    r = run(bars({"XA": spec}), cfg)
    assert decisions(r, "exit") == [(day(4), "XA", "exit", "exit signal")]
    assert fills(r)[1] == (day(5), "XA", "sell", 45, 10.0, 0.0, 955.0)


def test_ema_fast_above_slow_via_indicator_compare() -> None:
    # closes 10, 10, 10, 10, 14, 14, 14. ema(2) (alpha 2/3) and ema(4) (alpha 0.4) are both
    # 10 at idx 3 (10 > 10 fails). Idx 4: ema2 = 10 + (2/3)(4) = 12.667, ema4 = 10 + .4(4)
    # = 11.6 -> above, enter. Filled at the idx 5 open of 14: floor(500/14) = 35, cash 510.
    spec = [(10.0, 10.0)] * 4 + [(10.0, 14.0), (14.0, 14.0), (14.0, 14.0)]
    cond = Condition(
        kind="indicator_compare",
        indicator=ind("ema", length=2),
        other=ind("ema", length=4),
        params={"op": "above"},
    )
    r = run(bars({"XA": spec}), strategy([cond]))
    assert decisions(r, "enter") == [(day(4), "XA", "enter", "all entry conditions passed")]
    assert fills(r) == [(day(5), "XA", "buy", 35, 14.0, 0.0, 510.0)]
    row = r.decisions[r.decisions["action"] == "enter"].iloc[0]
    assert row["values"]["ema(length=2)"] == pytest.approx(10 + 8 / 3)
    assert row["values"]["ema(length=4)"] == pytest.approx(11.6)
    # EMA is in price units, so it may also be compared with SMA or crossed by the close.
    resolve(
        strategy(
            [
                Condition(
                    kind="indicator_compare",
                    indicator=ind("ema", length=5),
                    other=ind("sma", length=10),
                ),
                Condition(kind="price_cross", indicator=ind("ema", length=5)),
            ]
        ),
        Settings(_env_file=None),
    )


def test_bollinger_pctb_threshold_buys_the_dip() -> None:
    # closes 10, 12, 10, 12, 10, 12, 12 with length 4, stdevs 2: %B idx3 = 0.75, idx4 = 0.25
    # (see test_bollinger_hand_values) -> "below 0.5" enters at idx 4. Filled at the idx 5
    # open of 10: 50 shares, cash 500. Hold 1: exit at the idx 5 close, sold at the idx 6
    # open of 12: 500 + 600 = 1100.
    spec = [(10.0, 10.0), (10.0, 12.0), (12.0, 10.0), (10.0, 12.0), (12.0, 10.0)]
    spec += [(10.0, 12.0), (12.0, 12.0)]
    cond = threshold(ind("bollinger_pctb", length=4, stdevs=2.0), "below", 0.5)
    r = run(bars({"XA": spec}), strategy([cond], hold=1))
    assert decisions(r, *ENTER) == [
        (day(4), "XA", "enter", "all entry conditions passed"),
        (day(5), "XA", "exit", "held 1 sessions >= 1"),
    ]
    assert fills(r) == [
        (day(5), "XA", "buy", 50, 10.0, 0.0, 500.0),
        (day(6), "XA", "sell", 50, 12.0, 0.0, 1100.0),
    ]
    row = r.decisions[r.decisions["action"] == "enter"].iloc[0]
    assert row["values"]["bollinger_pctb(length=4,stdevs=2.0)"] == 0.25


def test_roc_threshold_enters_on_momentum() -> None:
    # roc(2): idx2 = 10/10 - 1 = 0, idx3 = 10.5/10 - 1 = 0.05, idx4 = 12.5/10 - 1 = 0.25
    # -> "above 0.1" enters at idx 4. Filled at the idx 5 open of 12.5: 40 shares, cash 500.
    spec = [(10.0, 10.0)] * 3 + [(10.0, 10.5), (10.5, 12.5), (12.5, 12.5), (12.5, 12.5)]
    r = run(bars({"XA": spec}), strategy([threshold(ind("roc", length=2), "above", 0.1)]))
    assert decisions(r, "enter") == [(day(4), "XA", "enter", "all entry conditions passed")]
    assert fills(r) == [(day(5), "XA", "buy", 40, 12.5, 0.0, 500.0)]
    row = r.decisions[r.decisions["action"] == "enter"].iloc[0]
    assert row["values"]["roc(length=2)"] == pytest.approx(0.25)


def test_price_cross_on_a_non_price_indicator_is_rejected_at_resolve() -> None:
    for bad in ("roc", "bollinger_pctb", "avg_volume"):
        cond = Condition(kind="price_cross", indicator=ind(bad), params={"direction": "above"})
        with pytest.raises(ValueError, match="not in price units"):
            resolve(strategy([cond]), Settings(_env_file=None))
    # And ROC cannot be compared with a price-unit indicator either.
    mixed = Condition(kind="indicator_compare", indicator=ind("roc"), other=ind("ema"))
    with pytest.raises(ValueError, match="not in the same units"):
        resolve(strategy([mixed]), Settings(_env_file=None))


def volume_bars(rows: list[tuple[float, float, int]]) -> pd.DataFrame:
    """Per-session (open, close, volume) for symbol XA."""
    return normalise(
        pd.DataFrame(
            [
                {
                    "date": SESSIONS[i],
                    "symbol": "XA",
                    "open": o,
                    "high": max(o, c),
                    "low": min(o, c),
                    "close": c,
                    "volume": v,
                }
                for i, (o, c, v) in enumerate(rows)
            ]
        )
    )


def test_avg_volume_threshold_enters_on_a_volume_surge() -> None:
    # avg_volume(2): idx1 = (1000+1000)/2 = 1000, idx2 = 1000, idx3 = (1000+3000)/2 = 2000
    # -> "above 1500" enters at idx 3. Filled at the idx 4 open of 10: 50 shares, cash 500.
    rows = [(10.0, 10.0, 1000)] * 3 + [(10.0, 10.0, 3000), (10.0, 10.0, 3000)]
    rows += [(10.0, 10.0, 1000)]
    cond = threshold(ind("avg_volume", length=2), "above", 1500)
    r = run(volume_bars(rows), strategy([cond]))
    assert decisions(r, "enter") == [(day(3), "XA", "enter", "all entry conditions passed")]
    assert fills(r)[0] == (day(4), "XA", "buy", 50, 10.0, 0.0, 500.0)
    row = r.decisions[r.decisions["action"] == "enter"].iloc[0]
    assert row["values"]["avg_volume(length=2)"] == 2000.0
