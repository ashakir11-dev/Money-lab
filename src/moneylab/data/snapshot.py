"""Immutable market-data snapshots and the file-import adapter.

File-import format (see docs/file-import-format.md): one CSV with columns
``date,symbol,open,high,low,close,volume``. Prices are already split- and
dividend-adjusted; the adjustment policy is stored in the snapshot metadata.

A snapshot's id is a hash of its canonical contents, so identical data always gets
the same id and a correction always gets a new one. Earlier runs keep pointing at the
snapshot they used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import pandas as pd

from moneylab.data.bars import label_in_range, minutes_of, regular_starts, session_label

COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]
INTRADAY_COLUMNS = ["bar_start", "date", "symbol", "open", "high", "low", "close", "volume"]
DEFAULT_ADJUSTMENT = "split_and_dividend_adjusted"


def read_import_file(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing column(s) {missing}")
    return normalise(df)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df[COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float).round(6)
    df["volume"] = df["volume"].astype("int64")
    if df.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate (date, symbol) rows; corrections must be a new snapshot")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("prices must be positive")
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


def content_id(df: pd.DataFrame) -> str:
    canon = df.to_csv(index=False, float_format="%.6f", date_format="%Y-%m-%d")
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


_SYMBOLS_SHOWN = 4


def snapshot_name(meta: dict[str, Any]) -> str:
    """The name a person sees. A typed label wins; otherwise the data configuration.

    ``{source} · {timeframe} · {universe} · {start} → {end}``. The timeframe is
    ``1d`` when the snapshot does not record one. A watchlist names the universe;
    otherwise up to four symbols are listed. Dates are the requested range when
    the snapshot has one, and the first and last bar otherwise.
    """
    label = str(meta.get("label") or "").strip()
    if label:
        return label
    raw_prov = meta.get("provenance")
    provenance: dict[str, Any] = raw_prov if isinstance(raw_prov, dict) else {}
    source = str(meta.get("source") or "unknown")
    timeframe = str(meta.get("timeframe") or provenance.get("timeframe") or "1d")
    return f"{source} · {timeframe} · {_universe(meta, provenance)} · {_span(meta, provenance)}"


def _universe(meta: dict[str, Any], provenance: dict[str, Any]) -> str:
    watchlist = provenance.get("watchlist")
    if isinstance(watchlist, dict) and watchlist.get("name"):
        version = watchlist.get("version")
        if version is None:
            return str(watchlist["name"])
        return f"{watchlist['name']} v{version}"
    symbols = sorted(str(s) for s in (meta.get("symbols") or []))
    if not symbols:
        return "no symbols"
    if len(symbols) <= _SYMBOLS_SHOWN:
        return ", ".join(symbols)
    shown = ", ".join(symbols[:_SYMBOLS_SHOWN])
    return f"{shown} + {len(symbols) - _SYMBOLS_SHOWN}"


def _span(meta: dict[str, Any], provenance: dict[str, Any]) -> str:
    start, end = provenance.get("start"), provenance.get("end")
    if not start or not end:
        start, end = meta.get("first_date"), meta.get("last_date")
    return f"{start or '—'} → {end or '—'}"


def normalise_intraday(df: pd.DataFrame) -> pd.DataFrame:
    """One row per bar start and symbol. Timestamps are kept, not truncated to midnight."""
    missing = [c for c in INTRADAY_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing column(s) {missing}")
    out = df[INTRADAY_COLUMNS].copy()
    out["bar_start"] = pd.to_datetime(out["bar_start"])
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].astype(float).round(6)
    out["volume"] = out["volume"].astype("int64")
    if out.duplicated(["bar_start", "symbol"]).any():
        raise ValueError("duplicate (bar_start, symbol) rows; corrections must be a new snapshot")
    if (out[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("prices must be positive")
    return out.sort_values(["symbol", "bar_start"]).reset_index(drop=True)


def intraday_content_id(df: pd.DataFrame) -> str:
    view = df[INTRADAY_COLUMNS].copy()
    view["bar_start"] = pd.to_datetime(view["bar_start"]).dt.strftime("%Y-%m-%dT%H:%M")
    view["date"] = pd.to_datetime(view["date"]).dt.strftime("%Y-%m-%d")
    canon = view.to_csv(index=False, float_format="%.6f")
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Snapshot:
    id: str
    bars: pd.DataFrame
    meta: dict[str, Any]


def save_snapshot(
    df: pd.DataFrame,
    root: Path,
    *,
    source: str,
    adjustment: str = DEFAULT_ADJUSTMENT,
    label: str = "",
    provenance: dict[str, Any] | None = None,
    raw: pd.DataFrame | None = None,
    actions: pd.DataFrame | None = None,
    timeframe: str | None = None,
) -> Snapshot:
    """Store ``df`` as an immutable snapshot (a no-op if identical contents exist).

    ``provenance`` (provider, request range, fetch time, ...) is kept in the metadata of
    a new snapshot. ``raw`` and ``actions``, when given, are stored next to it so the
    adjusted bars can be re-derived. Every call, new or not, is appended to the import
    log (``imports.jsonl``), so a second source yielding identical data is recorded too.
    """
    df = normalise(df)
    sid = content_id(df)
    root.mkdir(parents=True, exist_ok=True)
    data_path = root / f"{sid}.parquet"
    meta_path = root / f"{sid}.json"
    _log_import(root, sid, source, adjustment, label, provenance)
    if meta_path.exists():
        return load_snapshot(sid, root)
    if raw is not None:
        raw.to_parquet(root / f"{sid}.raw.parquet", index=False)
    if actions is not None:
        actions.to_parquet(root / f"{sid}.actions.parquet", index=False)
    meta = {
        "id": sid,
        "source": source,
        "label": label,
        "adjustment": adjustment,
        "created_at": datetime.now(UTC).isoformat(),
        "symbols": sorted(df["symbol"].unique().tolist()),
        "first_date": df["date"].min().date().isoformat(),
        "last_date": df["date"].max().date().isoformat(),
        "rows": len(df),
        "provenance": provenance or {},
    }
    if timeframe:
        meta["timeframe"] = timeframe
    df.to_parquet(data_path, index=False)
    meta_path.write_text(json.dumps(meta, indent=2))
    return Snapshot(sid, df, meta)


def save_intraday_snapshot(
    df: pd.DataFrame,
    root: Path,
    *,
    source: str,
    timeframe: str,
    adjustment: str,
    label: str = "",
    provenance: dict[str, Any] | None = None,
    raw: pd.DataFrame | None = None,
    actions: pd.DataFrame | None = None,
) -> Snapshot:
    """Store intraday bars. The content id includes each bar's start time."""
    df = normalise_intraday(df)
    sid = intraday_content_id(df)
    root.mkdir(parents=True, exist_ok=True)
    meta_path = root / f"{sid}.json"
    _log_import(root, sid, source, adjustment, label, provenance)
    if meta_path.exists():
        return load_snapshot(sid, root)
    if raw is not None:
        raw.to_parquet(root / f"{sid}.raw.parquet", index=False)
    if actions is not None:
        actions.to_parquet(root / f"{sid}.actions.parquet", index=False)
    meta = {
        "id": sid,
        "source": source,
        "label": label,
        "adjustment": adjustment,
        "timeframe": timeframe,
        "created_at": datetime.now(UTC).isoformat(),
        "symbols": sorted(df["symbol"].unique().tolist()),
        "first_date": df["date"].min().date().isoformat(),
        "last_date": df["date"].max().date().isoformat(),
        "rows": len(df),
        "provenance": provenance or {},
    }
    df.to_parquet(root / f"{sid}.parquet", index=False)
    meta_path.write_text(json.dumps(meta, indent=2))
    return Snapshot(sid, df, meta)


IMPORT_LOG = "imports.jsonl"


def _log_import(
    root: Path,
    sid: str,
    source: str,
    adjustment: str,
    label: str,
    provenance: dict[str, Any] | None,
) -> None:
    rec = {
        "snapshot_id": sid,
        "source": source,
        "adjustment": adjustment,
        "label": label,
        "imported_at": datetime.now(UTC).isoformat(),
        "provenance": provenance or {},
    }
    with (root / IMPORT_LOG).open("a") as f:
        f.write(json.dumps(rec) + "\n")


def list_imports(root: Path) -> list[dict[str, Any]]:
    """Every import ever made into ``root``, oldest first (append-only log)."""
    path = root / IMPORT_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_snapshot(sid: str, root: Path) -> Snapshot:
    df = pd.read_parquet(root / f"{sid}.parquet")
    meta = json.loads((root / f"{sid}.json").read_text())
    return Snapshot(sid, df, meta)


def list_snapshots(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    metas = [json.loads(p.read_text()) for p in root.glob("*.json")]
    return sorted(metas, key=lambda m: m["created_at"], reverse=True)


@dataclass(frozen=True)
class MarketData:
    """Bars for a universe aligned to exchange sessions. Shared read-only across runs."""

    sessions: pd.DatetimeIndex
    frames: dict[str, pd.DataFrame]  # symbol -> OHLCV indexed by session, NaN where missing
    first_bar: dict[str, int]  # symbol -> index of its first bar (before it: not listed)

    @classmethod
    def build(
        cls,
        bars: pd.DataFrame,
        symbols: list[str],
        start: str | None = None,
        end: str | None = None,
        *,
        timeframe: str = "1d",
    ) -> MarketData:
        if timeframe != "1d":
            return cls._build_intraday(bars, symbols, start, end, timeframe)
        cal = xcals.get_calendar("XNYS")
        lo = pd.Timestamp(start) if start else bars["date"].min()
        hi = pd.Timestamp(end) if end else bars["date"].max()
        sessions = pd.DatetimeIndex(cal.sessions_in_range(lo, hi)).tz_localize(None)
        frames: dict[str, pd.DataFrame] = {}
        first: dict[str, int] = {}
        for sym in symbols:
            f = bars[bars["symbol"] == sym].set_index("date")[
                ["open", "high", "low", "close", "volume"]
            ]
            if f.empty:
                raise ValueError(f"no data for {sym} in snapshot")
            f = f.reindex(sessions)
            frames[sym] = f
            present = f["close"].notna().to_numpy().nonzero()[0]
            first[sym] = int(present[0]) if present.size else len(sessions)
        return cls(sessions, frames, first)

    @classmethod
    def _build_intraday(
        cls,
        bars: pd.DataFrame,
        symbols: list[str],
        start: str | None,
        end: str | None,
        timeframe: str,
    ) -> MarketData:
        if "bar_start" not in bars.columns:
            raise ValueError(f"a {timeframe} snapshot has no bar_start column")
        minutes = minutes_of(timeframe)
        cal = xcals.get_calendar("XNYS")
        stamped = bars.copy()
        stamped["bar_start"] = pd.to_datetime(stamped["bar_start"])
        lo = pd.Timestamp(start) if start else stamped["bar_start"].min()
        hi = pd.Timestamp(end) if end else stamped["bar_start"].max()
        days = pd.DatetimeIndex(cal.sessions_in_range(lo.normalize(), hi.normalize())).tz_localize(
            None
        )
        slots = [
            pd.Timestamp(s)
            for day in days
            for s in regular_starts(day.date(), minutes)
            if label_in_range(session_label(s), start, end)
        ]
        if not slots:
            raise ValueError(f"no {timeframe} sessions in range")
        sessions = pd.DatetimeIndex(slots)
        frames: dict[str, pd.DataFrame] = {}
        first: dict[str, int] = {}
        for sym in symbols:
            f = stamped[stamped["symbol"] == sym].set_index("bar_start")[
                ["open", "high", "low", "close", "volume"]
            ]
            if f.empty:
                raise ValueError(f"no data for {sym} in snapshot")
            f = f[~f.index.duplicated(keep="last")].reindex(sessions)
            frames[sym] = f
            present = f["close"].notna().to_numpy().nonzero()[0]
            first[sym] = int(present[0]) if present.size else len(sessions)
        return cls(sessions, frames, first)
