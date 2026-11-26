"""Trend detection: strength (ADX), statistical slope, and market structure.

- ``adx``: Wilder's directional movement system (1978). ADX measures trend strength
  regardless of direction; +DI against -DI gives the direction.
- ``linreg_trend``: least-squares slope of log close. Its t-statistic says whether
  the drift is distinguishable from noise; R squared says how straight it is.
- ``market_structure``: Dow-style market structure from confirmed swing points.

Every value uses only bars up to and including its own session (swing points only from
when they are confirmed). See docs/research/support-resistance-and-trend.md.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import ParamSpec, register_indicator
from moneylab.components.swings import LEFT, RIGHT, pivots
from moneylab.data.bars import bars_per_year, timeframe_of_index


def _runs(valid: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) ranges of consecutive valid sessions. A missing bar ends a run."""
    out: list[tuple[int, int]] = []
    start = 0
    n = valid.size
    while start < n:
        if not valid[start]:
            start += 1
            continue
        end = start
        while end < n and valid[end]:
            end += 1
        out.append((start, end))
        start = end
    return out


def _wilder_dmi(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int
) -> dict[str, np.ndarray]:
    size = close.size
    plus_di = np.full(size, np.nan)
    minus_di = np.full(size, np.nan)
    adx = np.full(size, np.nan)
    if size < n + 1:
        return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.maximum.reduce(
        [high[1:] - low[1:], np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])]
    )
    # Index j in pdm/mdm/tr is session j + 1. First smoothed value = mean of the first n.
    s_tr, s_p, s_m = float(tr[:n].mean()), float(pdm[:n].mean()), float(mdm[:n].mean())
    dx = np.full(size, np.nan)
    for j in range(n - 1, tr.size):
        if j >= n:
            s_tr = (s_tr * (n - 1) + float(tr[j])) / n
            s_p = (s_p * (n - 1) + float(pdm[j])) / n
            s_m = (s_m * (n - 1) + float(mdm[j])) / n
        i = j + 1
        if s_tr > 0:
            plus_di[i] = 100 * s_p / s_tr
            minus_di[i] = 100 * s_m / s_tr
        else:
            plus_di[i] = minus_di[i] = 0.0
        total = plus_di[i] + minus_di[i]
        dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / total if total > 0 else 0.0
    first = 2 * n - 1  # n sessions of DX, the first at session n
    if size > first:
        value = float(np.mean(dx[n : first + 1]))
        adx[first] = value
        for i in range(first + 1, size):
            value = (value * (n - 1) + float(dx[i])) / n
            adx[i] = value
    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


@register_indicator(
    id="adx",
    version="1.0.0",
    label="ADX / directional movement (Wilder)",
    params=(
        ParamSpec("length", "int", 14, "Lookback (sessions)", min=2, max=200),
        ParamSpec(
            "output",
            "choice",
            "adx",
            "Output",
            choices=("adx", "plus_di", "minus_di"),
            help=(
                "adx: trend strength 0-100 (above ~25 is trending). "
                "plus_di / minus_di: up / down pressure."
            ),
        ),
    ),
    inputs=("high", "low", "close"),
    description=(
        "Wilder's directional movement. +DM = high - previous high when that exceeds "
        "previous low - low and is positive, else 0; -DM mirrors it. TR as for ATR. +DM, "
        "-DM and TR are Wilder-smoothed over `length` (first value the simple mean, then "
        "(previous x (length - 1) + new) / length); +DI = 100 x smoothed +DM / smoothed TR, "
        "-DI likewise, first at run index `length`. DX = 100 x |+DI - -DI| / (+DI + -DI); "
        "ADX is DX Wilder-smoothed over `length`, first at run index 2 x `length` - 1. "
        "A missing bar restarts the warm-up. Compare +DI with -DI (same indicator, "
        "different output) for direction."
    ),
)
def adx(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    out = np.full(close.size, np.nan)
    valid = ~(np.isnan(high) | np.isnan(low) | np.isnan(close))
    for s, e in _runs(valid):
        out[s:e] = _wilder_dmi(high[s:e], low[s:e], close[s:e], n)[params["output"]]
    return pd.Series(out, index=bars.index)


@register_indicator(
    id="linreg_trend",
    version="1.0.0",
    label="Regression trend (slope of log price)",
    params=(
        ParamSpec("length", "int", 50, "Lookback (sessions)", min=3, max=1000),
        ParamSpec(
            "output",
            "choice",
            "slope_pct_year",
            "Output",
            choices=("slope_pct_year", "tstat", "r2"),
            help=(
                "slope_pct_year: trend speed in % per year. tstat: slope / its standard "
                "error (|t| above ~2 is unlikely to be noise). r2: 0-1, how straight."
            ),
        ),
    ),
    description=(
        "Least-squares fit of ln(close) on session number over the last `length` "
        "sessions, today included; undefined if any is missing. slope_pct_year = "
        "(exp(slope x bars per year) - 1) x 100, with bars per year from the bar size "
        "(252 daily, 252 x bars per session intraday). "
        "tstat = slope / sqrt(SSE / (length - 2) / Sxx); "
        "undefined for a perfect fit (SSE = 0). r2 = 1 - SSE / SST (undefined when price "
        "is flat). First value at index `length` - 1."
    ),
)
def linreg_trend(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["length"])
    what = params["output"]
    y_all = np.log(bars["close"].to_numpy(dtype=float))
    x = np.arange(n, dtype=float)
    xm = x - x.mean()
    sxx = float((xm**2).sum())
    per_year = bars_per_year(timeframe_of_index(bars.index))
    out = np.full(y_all.size, np.nan)
    for t in range(n - 1, y_all.size):
        y = y_all[t - n + 1 : t + 1]
        if np.isnan(y).any():
            continue
        ym = y - y.mean()
        slope = float((xm * ym).sum()) / sxx
        sse = float(((ym - slope * xm) ** 2).sum())
        sst = float((ym**2).sum())
        if what == "slope_pct_year":
            out[t] = (math.exp(slope * per_year) - 1) * 100
        elif what == "r2":
            out[t] = 1 - sse / sst if sst > 0 else np.nan
        else:
            se = math.sqrt(sse / (n - 2) / sxx) if n > 2 else 0.0
            out[t] = slope / se if se > 1e-15 else np.nan
    return pd.Series(out, index=bars.index)


@register_indicator(
    id="market_structure",
    version="1.0.0",
    label="Market structure (higher highs / lower lows)",
    params=(LEFT, RIGHT),
    inputs=("high", "low"),
    description=(
        "Market structure from the last two confirmed swing highs and last two confirmed "
        "swing lows (swing_high / swing_low rules, known `right` sessions after the "
        "swing). +1: higher high and higher low (uptrend). -1: lower high and lower low "
        "(downtrend). 0: mixed (range or turning). Undefined until two of each are known."
    ),
)
def market_structure(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    left, right = int(params["left"]), int(params["right"])
    ph = pivots(high, left, right, "high")
    pl = pivots(low, left, right, "low")
    highs: list[float] = []
    lows: list[float] = []
    out = np.full(high.size, np.nan)
    for t in range(high.size):
        k = t - right
        if k >= 0:
            if ph[k]:
                highs.append(high[k])
            if pl[k]:
                lows.append(low[k])
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[-1] > highs[-2]
            hl = lows[-1] > lows[-2]
            lh = highs[-1] < highs[-2]
            ll = lows[-1] < lows[-2]
            out[t] = 1.0 if hh and hl else -1.0 if lh and ll else 0.0
    return pd.Series(out, index=bars.index)
