"""Support and resistance: areas where price has already turned by a real amount.

A turn is confirmed only after price travels ``min_move`` usual bars away from it.
Usual bars are the Wilder average true range. Turns that land within ``zone`` usual
bars of each other are one area. The area just under the previous close is support;
the area just above it is resistance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from moneylab.components.atr import atr
from moneylab.components.registry import ParamSpec, register_indicator

_PARAMS = (
    ParamSpec("min_move", "float", 2.0, "Minimum move (usual bars)", min=0.5, max=10),
    ParamSpec("zone", "float", 1.0, "Area height (usual bars)", min=0.1, max=5),
    ParamSpec("lookback", "int", 120, "Memory (bars)", min=20, max=2000),
    ParamSpec("atr_length", "int", 14, "Usual bar (bars)", min=1, max=200),
)

_RULE = (
    "A turn is the first bar of an extreme, and it counts only once price has since "
    "traveled min_move usual bars away from it (usual bar = Wilder ATR of atr_length). "
    "Turns confirmed within the last lookback bars form areas: walking from the lowest "
    "price, a turn joins the open area while it is within zone usual bars of that area's "
    "lowest price. The area price is the median of its turns (the average of the two "
    "middle prices when the count is even). One turn is a complete area. zone must be "
    "less than min_move. The previous close picks the side: support is the highest area "
    "at or below it, resistance the lowest area at or above it. Same price: more turns, "
    "then the more recent turn. A missing high, low, or close ends the run and its turns "
    "are forgotten. A usual bar of zero blanks the bar. Counts are in bars."
)


def _check(params: dict[str, Any]) -> None:
    if params["zone"] >= params["min_move"]:
        raise ValueError(
            f"zone ({params['zone']}) must be less than min_move ({params['min_move']})"
        )


Side = Literal["support", "resistance"]


@dataclass(frozen=True)
class Area:
    """One price area on a single bar."""

    price: float
    tests: int
    latest: int


def choose_level(areas: list[Area], *, side: Side) -> Area | None:
    """The area on ``side`` of the previous close. ``areas`` are already on that side."""
    if not areas:
        return None
    if side == "support":
        return max(areas, key=lambda area: (area.price, area.tests, area.latest))
    return min(areas, key=lambda area: (area.price, -area.tests, -area.latest))


@dataclass(frozen=True)
class _Turn:
    extreme_i: int
    confirm_i: int
    price: float


def _first_extreme(
    values: np.ndarray, begin: int, end: int, *, want_low: bool
) -> tuple[int, float]:
    """First bar of the lowest low, or the highest high, from ``begin`` through ``end``."""
    idx = begin
    price = float(values[begin])
    for i in range(begin + 1, end + 1):
        value = float(values[i])
        if (want_low and value < price) or (not want_low and value > price):
            idx, price = i, value
    return idx, price


def _turns(
    high: np.ndarray,
    low: np.ndarray,
    usual: np.ndarray,
    min_move: float,
    start: int,
    end: int,
) -> list[_Turn]:
    origin: int | None = None
    for i in range(start, end):
        if np.isfinite(usual[i]) and usual[i] > 0:
            origin = i
            break
    if origin is None:
        return []
    turns: list[_Turn] = []
    hi_i: int | None = origin
    hi = float(high[origin])
    lo_i: int | None = origin
    lo = float(low[origin])
    for t in range(origin + 1, end):
        if hi_i is not None and float(high[t]) > hi:
            hi_i, hi = t, float(high[t])
        if lo_i is not None and float(low[t]) < lo:
            lo_i, lo = t, float(low[t])
        atr_t = usual[t]
        if not np.isfinite(atr_t) or atr_t <= 0:
            continue
        thresh = min_move * float(atr_t)
        confirmed = 0
        while confirmed < 2:
            high_ok = hi_i is not None and hi_i < t and hi - float(low[t]) >= thresh
            low_ok = lo_i is not None and lo_i < t and float(high[t]) - lo >= thresh
            if high_ok and low_ok:
                assert hi_i is not None and lo_i is not None
                if hi_i == lo_i:
                    break
                if hi_i < lo_i:
                    low_ok = False
                else:
                    high_ok = False
            if high_ok:
                assert hi_i is not None
                turns.append(_Turn(hi_i, t, hi))
                lo_i, lo = _first_extreme(low, hi_i, t, want_low=True)
                hi_i = None
                confirmed += 1
                continue
            if low_ok:
                assert lo_i is not None
                turns.append(_Turn(lo_i, t, lo))
                hi_i, hi = _first_extreme(high, lo_i, t, want_low=False)
                lo_i = None
                confirmed += 1
                continue
            break
    return turns


def _areas(turns: list[_Turn], t: int, lookback: int, width: float) -> list[Area]:
    remembered = [turn for turn in turns if t - lookback < turn.confirm_i <= t]
    remembered.sort(key=lambda turn: (turn.price, turn.extreme_i))
    groups: list[list[_Turn]] = []
    for turn in remembered:
        if not groups or turn.price - groups[-1][0].price > width:
            groups.append([turn])
        else:
            groups[-1].append(turn)
    areas: list[Area] = []
    for group in groups:
        prices = sorted(turn.price for turn in group)
        mid = len(prices) // 2
        median = prices[mid] if len(prices) % 2 else (prices[mid - 1] + prices[mid]) / 2.0
        latest = max(turn.confirm_i * 1_000_000 + turn.extreme_i for turn in group)
        areas.append(Area(median, len(group), latest))
    return areas


def _levels(bars: pd.DataFrame, params: dict[str, Any]) -> dict[str, pd.Series]:
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    usual = atr(bars, {"length": int(params["atr_length"])}).to_numpy(dtype=float)
    n = close.size
    columns = (
        "support",
        "resistance",
        "support_tests",
        "resistance_tests",
        "support_distance",
        "resistance_distance",
    )
    out = {name: np.full(n, np.nan) for name in columns}
    valid = np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
    min_move = float(params["min_move"])
    zone = float(params["zone"])
    lookback = int(params["lookback"])
    start = 0
    while start < n:
        if not valid[start]:
            start += 1
            continue
        end = start
        while end < n and valid[end]:
            end += 1
        turns = _turns(high, low, usual, min_move, start, end)
        for t in range(start + 1, end):
            atr_t = usual[t]
            if not np.isfinite(atr_t) or atr_t <= 0 or not np.isfinite(close[t - 1]):
                continue
            found = _areas(turns, t, lookback, zone * float(atr_t))
            prev = float(close[t - 1])
            support = choose_level([area for area in found if area.price <= prev], side="support")
            resistance = choose_level(
                [area for area in found if area.price >= prev], side="resistance"
            )
            if support is not None:
                out["support"][t] = support.price
                out["support_tests"][t] = support.tests
                out["support_distance"][t] = (float(close[t]) - support.price) / float(atr_t)
            if resistance is not None:
                out["resistance"][t] = resistance.price
                out["resistance_tests"][t] = resistance.tests
                out["resistance_distance"][t] = (resistance.price - float(close[t])) / float(atr_t)
        start = end
    return {name: pd.Series(values, index=bars.index) for name, values in out.items()}


def _register(name: str, label: str, *, price_scale: bool, detail: str) -> None:
    def compute(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
        return _levels(bars, params)[name]

    compute.__name__ = name
    compute.__doc__ = detail
    register_indicator(
        id=name,
        version="1.0.0",
        label=label,
        params=_PARAMS,
        inputs=("high", "low", "close"),
        description=f"{detail} {_RULE}",
        check=_check,
        price_scale=price_scale,
    )(compute)


_register(
    "support",
    "Support",
    price_scale=True,
    detail="Support is the highest turn-area at or below the previous close.",
)
_register(
    "resistance",
    "Resistance",
    price_scale=True,
    detail="Resistance is the lowest turn-area at or above the previous close.",
)
_register(
    "support_tests",
    "Support tests",
    price_scale=False,
    detail="How many turns sit in the support area. Blank when support is blank.",
)
_register(
    "resistance_tests",
    "Resistance tests",
    price_scale=False,
    detail="How many turns sit in the resistance area. Blank when resistance is blank.",
)
_register(
    "support_distance",
    "Distance above support",
    price_scale=False,
    detail=(
        "Close minus support, divided by the usual bar. Zero sits on support. "
        "Negative means the close is through it."
    ),
)
_register(
    "resistance_distance",
    "Distance below resistance",
    price_scale=False,
    detail=(
        "Resistance minus the close, divided by the usual bar. Zero sits on resistance. "
        "Negative means the close is through it."
    ),
)
