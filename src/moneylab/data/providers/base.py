"""Provider interface shared by every market-data source.

A provider returns *raw* (as traded) daily bars plus the corporate actions it knows
about, never adjusted prices. Adjustment happens in exactly one place,
``moneylab.data.actions.adjust``, which the file adapter uses too. So the same raw
reference data gives the same snapshot whichever adapter loaded it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import pandas as pd

from moneylab.data.actions import ACTION_COLUMNS, adjust
from moneylab.data.snapshot import COLUMNS, normalise


class ProviderError(RuntimeError):
    """A provider request failed. Messages never contain credentials."""


class ProviderAuthError(ProviderError):
    """Missing or rejected credentials (HTTP 401/403)."""


class ProviderNotFound(ProviderError):
    """The provider does not know a symbol (HTTP 404)."""


class ProviderRateLimited(ProviderError):
    """Still rate limited (HTTP 429) after the allowed retries."""


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS}).astype(
        {"date": "datetime64[ns]", "symbol": "object", "volume": "int64"}
    )


def empty_actions() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in ACTION_COLUMNS}).astype(
        {"date": "datetime64[ns]", "symbol": "object", "type": "object"}
    )


@dataclass(frozen=True)
class ProviderHistory:
    """What a provider returned for one request batch.

    ``raw`` uses the file-import columns with unadjusted prices; ``actions`` uses the
    actions-file columns (``date,symbol,type,value``). ``provenance`` describes the
    request (provider, range, fetch time, request and cache counts) and never holds a
    credential.
    """

    raw: pd.DataFrame
    actions: pd.DataFrame
    provenance: dict[str, Any] = field(default_factory=dict)

    def adjusted(self) -> pd.DataFrame:
        """Split- and dividend-adjusted bars through the shared adjustment path."""
        if self.raw.empty:
            raise ProviderError("the provider returned no bars for this request")
        return normalise(adjust(self.raw, self.actions))


class Provider(Protocol):
    """A daily-bar source for US stocks and ETFs."""

    name: str

    def fetch_history(
        self, symbols: Iterable[str], start: date, end: date, *, refresh: bool = False
    ) -> ProviderHistory:
        """Raw bars and corporate actions for ``symbols`` from ``start`` to ``end``."""
        ...

    def fetch_sessions(
        self, symbols: Iterable[str], after: date, end: date | None = None
    ) -> ProviderHistory:
        """Raw bars and actions for sessions strictly after ``after`` (to ``end`` or today)."""
        ...


@dataclass(frozen=True)
class NewSessions:
    """Sessions newer than a snapshot, ready for a paper-trading consumer.

    ``bars`` is in the file-import format (``date,symbol,open,high,low,close,volume``)
    with prices as traded. Adjustment only rewrites prices *before* an action, so when
    ``actions`` is empty these bars are exactly what the snapshot's adjusted series would
    contain for those sessions. When ``actions`` is not empty (a split or dividend went
    ex in the window), the consumer applies them to open positions (split: quantity x
    value; dividend: cash per share) and research runs should import a fresh snapshot.
    """

    bars: pd.DataFrame
    actions: pd.DataFrame

    @property
    def basis_changed(self) -> bool:
        return not self.actions.empty


def new_sessions(known: pd.DataFrame, raw: pd.DataFrame, actions: pd.DataFrame) -> NewSessions:
    """Pure: keep the rows of ``raw``/``actions`` that are newer than ``known``.

    ``known`` is a snapshot's bars (file-import columns). For each symbol, rows dated
    on or before that symbol's last known date are dropped; a symbol absent from
    ``known`` keeps all its rows. Nothing is fetched, filled or adjusted.
    """
    last = known.groupby("symbol")["date"].max() if not known.empty else pd.Series(dtype=object)

    def newer(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()
        cutoff = df["symbol"].map(last)
        keep = cutoff.isna() | (pd.to_datetime(df["date"]) > pd.to_datetime(cutoff))
        return df[keep]

    bars = newer(raw)
    bars = normalise(bars) if not bars.empty else empty_bars()
    acts = newer(actions).sort_values(["symbol", "date"]).reset_index(drop=True)
    return NewSessions(bars, acts if not acts.empty else empty_actions())
