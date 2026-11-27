"""Sector and VIX inputs. Each one reads a supplied series and refuses to fill gaps.

The stock frame must already carry the extra close (``sector_close``, ``benchmark_close``,
or ``vix_close``). Nothing here substitutes SPY, a sector ETF, or a flat VIX.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator

_WINDOW = ParamSpec("length", "int", 252, "Sessions in the percentile window", min=2, max=756)


class ContextDataAbsent(ValueError):
    """The recipe named a context series and the bars did not include it."""

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(f"{feature}: required series is absent")


def _require(frame: pd.DataFrame, column: str, feature: str) -> pd.Series:
    if column not in frame.columns:
        raise ContextDataAbsent(feature)
    series = frame[column].astype(float)
    if not bool(series.notna().any()):
        raise ContextDataAbsent(feature)
    return series


def _return_gap(left: pd.Series, right: pd.Series) -> pd.Series:
    """Close-to-close return difference. The first session stays undefined."""
    gap: pd.Series = left.astype(float).pct_change() - right.astype(float).pct_change()
    return gap


def _percentile(series: pd.Series, window: int) -> pd.Series:
    def _rank(window_values: Any) -> float:
        last = float(window_values[-1])
        return float((window_values <= last).mean())

    out: pd.Series = series.rolling(window, min_periods=window).apply(_rank, raw=True)
    return out


@register_indicator(
    id="stock_minus_sector",
    version="1.0.0",
    label="Stock return minus sector return",
    params=(),
    inputs=("close", "sector_close"),
    description="Today's close-to-close stock return minus the supplied sector close's return.",
)
def stock_minus_sector(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    del params
    return _return_gap(bars["close"], _require(bars, "sector_close", "stock_minus_sector"))


@register_indicator(
    id="sector_minus_spy",
    version="1.0.0",
    label="Sector return minus benchmark return",
    params=(),
    inputs=("sector_close", "benchmark_close"),
    description="Supplied sector close return minus the supplied benchmark close return.",
)
def sector_minus_spy(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    del params
    return _return_gap(
        _require(bars, "sector_close", "sector_minus_spy"),
        _require(bars, "benchmark_close", "sector_minus_spy"),
    )


@register_indicator(
    id="stock_minus_spy",
    version="1.0.0",
    label="Stock return minus benchmark return",
    params=(),
    inputs=("close", "benchmark_close"),
    description="Today's close-to-close stock return minus the supplied benchmark close's return.",
)
def stock_minus_spy(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    del params
    return _return_gap(bars["close"], _require(bars, "benchmark_close", "stock_minus_spy"))


@register_indicator(
    id="vix_level",
    version="1.0.0",
    label="VIX close",
    params=(),
    inputs=("vix_close",),
    description="The supplied VIX close. No level is invented when the column is missing.",
)
def vix_level(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    del params
    return _require(bars, "vix_close", "vix_level")


@register_indicator(
    id="vix_change_1",
    version="1.0.0",
    label="VIX one-session change",
    params=(),
    inputs=("vix_close",),
    description="VIX close divided by the prior session's VIX close, minus one.",
)
def vix_change_1(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    del params
    vix = _require(bars, "vix_close", "vix_change_1")
    changed: pd.Series = vix / vix.shift(1) - 1.0
    return changed


@register_indicator(
    id="vix_change_5",
    version="1.0.0",
    label="VIX five-session change",
    params=(),
    inputs=("vix_close",),
    description="VIX close divided by the close five sessions earlier, minus one.",
)
def vix_change_5(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    del params
    vix = _require(bars, "vix_close", "vix_change_5")
    changed: pd.Series = vix / vix.shift(5) - 1.0
    return changed


@register_indicator(
    id="vix_percentile_252",
    version="1.0.0",
    label="VIX percentile over 252 sessions",
    params=(_WINDOW,),
    inputs=("vix_close",),
    description=(
        "Share of the last `length` supplied VIX closes at or below today's close. "
        "The default window is 252 sessions. Undefined until that many closes exist."
    ),
)
def vix_percentile_252(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    window = int(params.get("length", 252))
    return _percentile(_require(bars, "vix_close", "vix_percentile_252"), window)
