"""As-of sector ETF and VIX joins.

The sector ETF for a symbol is the latest map row whose ``as_of`` is on or before
the bar. A later row is ignored. The close is taken from that ETF's bar with the
same session identity. A daily close is not copied onto an hourly bar, and a missing
session stays missing. Nothing is carried forward from the previous session.

VIX is the same session match. Equibles exposes stock bars, not an index route, so
an unknown VIX symbol is left missing instead of being replaced with a stock.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import pandas as pd

from moneylab.data.providers.base import ProviderNotFound


class _HistorySource(Protocol):
    def fetch_history(
        self,
        symbols: list[str],
        start: date,
        end: date,
        *,
        timeframe: str = "1h",
    ) -> Any: ...


def join_context_feeds(
    bars: pd.DataFrame,
    *,
    sector_map: pd.DataFrame | None = None,
    sector_bars: pd.DataFrame | None = None,
    vix_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return ``bars`` with ``sector_close`` and ``vix_close`` attached where supplied."""
    out = bars.copy()
    if (
        sector_map is not None
        and _usable_map(sector_map)
        and sector_bars is not None
        and not sector_bars.empty
    ):
        out["sector_close"] = _sector_closes(out, sector_map, sector_bars)
    if vix_bars is not None and not vix_bars.empty and "close" in vix_bars.columns:
        out["vix_close"] = _vix_closes(out, vix_bars)
    return out


def load_context_feeds(
    provider: _HistorySource,
    sector_map: pd.DataFrame | None,
    *,
    start: date,
    end: date,
    timeframe: str,
    vix_symbol: str | None = None,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Fetch ETF bars named by the map, and VIX when a symbol is given.

    A symbol the provider does not know is omitted. The result is not a substitute
    series. This function does not choose a date range; the caller does.
    """
    sector_bars = None
    if _usable_map(sector_map):
        assert sector_map is not None
        names = sorted(set(sector_map["sector_etf"].astype(str).str.upper()))
        sector_bars = _fetch_known(provider, names, start, end, timeframe)
    vix_bars = None
    if vix_symbol:
        vix_bars = _fetch_known(provider, [vix_symbol.strip().upper()], start, end, timeframe)
    return sector_bars, vix_bars


def _usable_map(sector_map: pd.DataFrame | None) -> bool:
    if sector_map is None or sector_map.empty:
        return False
    return {"as_of", "symbol", "sector_etf"}.issubset(sector_map.columns)


def _session_key(frame: pd.DataFrame) -> pd.Series:
    if "bar_start" in frame.columns:
        return pd.to_datetime(frame["bar_start"])
    return pd.to_datetime(frame["date"]).dt.normalize()


def _same_clock(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    return ("bar_start" in left.columns) == ("bar_start" in right.columns)


def _sector_closes(
    bars: pd.DataFrame, sector_map: pd.DataFrame, sector_bars: pd.DataFrame
) -> pd.Series:
    closes = pd.Series(pd.NA, index=bars.index, dtype="Float64")
    if not _same_clock(bars, sector_bars):
        return closes
    mapped = sector_map.copy()
    mapped["as_of"] = pd.to_datetime(mapped["as_of"])
    mapped["symbol"] = mapped["symbol"].astype(str).str.upper()
    mapped["sector_etf"] = mapped["sector_etf"].astype(str).str.upper()
    mapped = mapped.sort_values("as_of")
    prices = sector_bars.copy()
    prices["symbol"] = prices["symbol"].astype(str).str.upper()
    prices["_session"] = _session_key(prices)
    lookup = prices.drop_duplicates(["symbol", "_session"]).set_index(["symbol", "_session"])[
        "close"
    ]
    sessions = _session_key(bars)
    symbols = bars["symbol"].astype(str).str.upper()
    for i, (symbol, session) in enumerate(zip(symbols, sessions, strict=True)):
        history = mapped[(mapped["symbol"] == symbol) & (mapped["as_of"] <= session)]
        if history.empty:
            continue
        etf = str(history.iloc[-1]["sector_etf"])
        key = (etf, session)
        if key not in lookup.index:
            continue
        closes.iloc[i] = float(lookup.loc[key])
    return closes.astype(float)


def _vix_closes(bars: pd.DataFrame, vix_bars: pd.DataFrame) -> pd.Series:
    closes = pd.Series(pd.NA, index=bars.index, dtype="Float64")
    if not _same_clock(bars, vix_bars):
        return closes
    prices = vix_bars.copy()
    prices["_session"] = _session_key(prices)
    lookup = prices.drop_duplicates("_session").set_index("_session")["close"]
    sessions = _session_key(bars)
    for i, session in enumerate(sessions):
        if session not in lookup.index:
            continue
        closes.iloc[i] = float(lookup.loc[session])
    return closes.astype(float)


def _fetch_known(
    provider: _HistorySource,
    symbols: list[str],
    start: date,
    end: date,
    timeframe: str,
) -> pd.DataFrame | None:
    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        try:
            history = provider.fetch_history([symbol], start, end, timeframe=timeframe)
        except ProviderNotFound:
            continue
        raw = history.raw
        if raw is not None and not raw.empty:
            frames.append(raw)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)
