"""Context books, point-in-time membership, and matched cohorts.

Every series here is either supplied by the caller or rejected. No sector close, VIX
close, index weight, or peer return is filled in.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from moneylab.components.context import ContextDataAbsent
from moneylab.data.snapshot import MarketData

_WEIGHT_COLUMNS = ("as_of", "symbol", "weight")
_KEY_COLUMNS = ("date", "symbol", "value")


class ContextBook:
    """Closes the recipe can join. Empty fields mean the series was not supplied."""

    def __init__(
        self,
        *,
        sector_close: dict[str, pd.Series] | None = None,
        benchmark_close: pd.Series | None = None,
        vix_close: pd.Series | None = None,
    ) -> None:
        self.sector_close = sector_close or {}
        self.benchmark_close = benchmark_close
        self.vix_close = vix_close

    def covers(self, feature_id: str) -> bool:
        vix_features = {"vix_level", "vix_change_1", "vix_change_5", "vix_percentile_252"}
        if feature_id in vix_features:
            return _has_values(self.vix_close)
        if feature_id == "stock_minus_spy":
            return _has_values(self.benchmark_close)
        if feature_id == "stock_minus_sector":
            return any(_has_values(series) for series in self.sector_close.values())
        if feature_id == "sector_minus_spy":
            return self.covers("stock_minus_sector") and self.covers("stock_minus_spy")
        return False


def _has_values(series: pd.Series | None) -> bool:
    return series is not None and bool(series.notna().any())


def bind_context(data: MarketData, book: ContextBook) -> MarketData:
    """Copy bars with the supplied closes attached. Missing series stay missing."""
    frames: dict[str, pd.DataFrame] = {}
    for symbol, frame in data.frames.items():
        copied = frame.copy()
        sector = book.sector_close.get(symbol)
        if sector is not None:
            copied["sector_close"] = sector.reindex(copied.index)
        if book.benchmark_close is not None:
            copied["benchmark_close"] = book.benchmark_close.reindex(copied.index)
        if book.vix_close is not None:
            copied["vix_close"] = book.vix_close.reindex(copied.index)
        frames[symbol] = copied
    return MarketData(data.sessions, frames, dict(data.first_bar))


def weights_problem(weights: object) -> str:
    """Empty string when the table can be used. A missing table is a hard rejection."""
    if weights is None:
        return "point_in_time_weights_unavailable"
    if not isinstance(weights, pd.DataFrame):
        return "point_in_time_weights_invalid"
    if any(column not in weights.columns for column in _WEIGHT_COLUMNS):
        return "point_in_time_weights_invalid"
    if weights.empty:
        return "point_in_time_weights_unavailable"
    if weights.duplicated(["as_of", "symbol"]).any():
        return "point_in_time_weights_invalid"
    if weights["weight"].isna().any():
        return "point_in_time_weights_invalid"
    return ""


def weight_records(weights: pd.DataFrame) -> list[dict[str, Any]]:
    """Rows the simulator can read. Later as-of dates are kept and ignored until then."""
    frame = weights.copy()
    frame["as_of"] = pd.to_datetime(frame["as_of"]).dt.strftime("%Y-%m-%d")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame["weight"] = frame["weight"].astype(float)
    frame = frame.sort_values(["symbol", "as_of"])
    weights_array = frame["weight"].to_numpy(dtype=float)
    return [
        {"as_of": str(as_of), "symbol": str(symbol), "weight": float(weight)}
        for as_of, symbol, weight in zip(
            frame["as_of"], frame["symbol"], weights_array, strict=True
        )
    ]


def members_on(weights: pd.DataFrame, session: str) -> dict[str, float]:
    """Latest weight at or before ``session`` for each symbol. Future rows are ignored.

    A symbol with no row yet, or whose latest weight is not positive, is not a member.
    """
    if weights_problem(weights):
        raise ContextDataAbsent("point_in_time_weights")
    day = pd.Timestamp(session)
    frame = weights.copy()
    frame["as_of"] = pd.to_datetime(frame["as_of"])
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame = frame[frame["as_of"] <= day]
    if frame.empty:
        return {}
    latest = frame.sort_values("as_of").groupby("symbol", as_index=False).tail(1)
    weights_array = latest["weight"].to_numpy(dtype=float)
    return {
        str(symbol): float(weight)
        for symbol, weight in zip(latest["symbol"], weights_array, strict=True)
        if weight > 0
    }


def key_series_usable(key_series: object) -> bool:
    if not isinstance(key_series, pd.DataFrame) or key_series.empty:
        return False
    if any(column not in key_series.columns for column in _KEY_COLUMNS):
        return False
    return bool(key_series["value"].notna().any())


def matched_cohort(
    entries: list[dict[str, str]],
    key_series: pd.DataFrame | None,
    bars: pd.DataFrame | None,
    *,
    tolerance: float,
    hold: int,
) -> list[dict[str, Any]]:
    """Pair each entry with names whose key on that date is within tolerance.

    Forward returns use each name's own later closes. A name without ``hold`` sessions
    after the decision is left out. It is not given a zero.
    """
    if not key_series_usable(key_series):
        raise ContextDataAbsent("matched_cohort")
    if bars is None or bars.empty or "close" not in bars.columns:
        raise ContextDataAbsent("matched_cohort_bars")
    assert key_series is not None
    keys = key_series.copy()
    keys["date"] = pd.to_datetime(keys["date"])
    keys["symbol"] = keys["symbol"].astype(str).str.upper()
    prices = bars.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    prices["symbol"] = prices["symbol"].astype(str).str.upper()
    out: list[dict[str, Any]] = []
    for entry in entries:
        symbol = str(entry["symbol"]).upper()
        day = pd.Timestamp(entry["date"])
        mine = keys[(keys["symbol"] == symbol) & (keys["date"] == day)]
        if mine.empty or pd.isna(mine["value"].iloc[0]):
            out.append(
                {
                    "symbol": symbol,
                    "date": day.date().isoformat(),
                    "matches": [],
                    "cohort_return": None,
                    "entry_return": _forward(prices, symbol, day, hold),
                    "reason": "key_absent_for_entry",
                }
            )
            continue
        anchor = float(mine["value"].iloc[0])
        same_day = keys[(keys["date"] == day) & (keys["symbol"] != symbol)]
        peers = same_day[(same_day["value"] - anchor).abs() <= tolerance]
        matches: list[dict[str, Any]] = []
        returns: list[float] = []
        peer_values = peers["value"].to_numpy(dtype=float)
        for symbol_name, key_value in zip(peers["symbol"].astype(str), peer_values, strict=True):
            forward = _forward(prices, str(symbol_name), day, hold)
            if forward is None:
                continue
            matches.append(
                {
                    "symbol": str(symbol_name),
                    "key": float(key_value),
                    "forward_return": forward,
                }
            )
            returns.append(forward)
        out.append(
            {
                "symbol": symbol,
                "date": day.date().isoformat(),
                "key": anchor,
                "matches": matches,
                "cohort_return": (sum(returns) / len(returns)) if returns else None,
                "entry_return": _forward(prices, symbol, day, hold),
                "reason": "matched" if matches else "no_peer",
            }
        )
    return out


def _forward(prices: pd.DataFrame, symbol: str, day: pd.Timestamp, hold: int) -> float | None:
    series = prices[prices["symbol"] == symbol].sort_values("date")
    dates = list(series["date"])
    if day not in dates:
        return None
    start = dates.index(day)
    end = start + hold
    if end >= len(dates):
        return None
    first = float(series["close"].iloc[start])
    last = float(series["close"].iloc[end])
    if first <= 0 or last != last or first != first:
        return None
    return last / first - 1.0
