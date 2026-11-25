"""Equibles intraday bars and splits.

``GET /v1/stocks/{ticker}/bars`` with ``adjustment=raw`` and ``session=regular``.
Daily snapshots are not fetched here: the caller resamples cached hourly bars.
Splits come from ``GET /v1/stocks/{ticker}/splits``. Indicator routes and the
dividend route are never called.

The key is ``EQUIBLES_API_KEY``, sent only as ``Authorization: Bearer``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from pydantic import SecretStr

from moneylab.data.bars import EQUIBLES_INTERVAL_NAME, on_grid
from moneylab.data.providers.base import (
    ProviderAuthError,
    ProviderError,
    ProviderHistory,
    ProviderNotFound,
    ProviderRateLimited,
    empty_actions,
    empty_bars,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.equibles.com"
NY = ZoneInfo("America/New_York")
BARS_PATH = "/v1/stocks/{ticker}/bars"
SPLITS_PATH = "/v1/stocks/{ticker}/splits"
RETRY_STATUSES = {500, 502, 503, 504}


def _ny_now(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(NY)


def to_new_york(iso: str) -> datetime:
    """Bar start as a naive America/New_York wall time, seconds dropped."""
    text = iso.replace("Z", "+00:00")
    ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    local = ts.astimezone(NY).replace(second=0, microsecond=0, tzinfo=None)
    return local


class EquiblesProvider:
    name = "equibles"

    def __init__(
        self,
        api_key: SecretStr | str | None,
        *,
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        max_retries: int = 4,
        backoff_seconds: float = 2.0,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if isinstance(api_key, str):
            api_key = SecretStr(api_key) if api_key.strip() else None
        self._key = api_key
        self.cache_dir = cache_dir / "equibles" if cache_dir is not None else None
        # Ignore HTTP_PROXY / HTTPS_PROXY. A local dev proxy turns a reachable host
        # into a ProxyError.
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._now = now
        self.http_requests = 0
        self.cache_hits = 0

    def __repr__(self) -> str:
        return f"EquiblesProvider(base_url={self.base_url!r}, key={'set' if self._key else 'unset'})"

    def fetch_history(
        self,
        symbols: Iterable[str],
        start: date,
        end: date,
        *,
        refresh: bool = False,
        timeframe: str = "1h",
    ) -> ProviderHistory:
        syms = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not syms:
            raise ValueError("no symbols given")
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        if timeframe not in EQUIBLES_INTERVAL_NAME:
            raise ValueError(f"unknown timeframe {timeframe!r}")
        interval = EQUIBLES_INTERVAL_NAME[timeframe]
        minutes = 60 if timeframe == "1d" else {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60}[timeframe]
        fetched_at = self._now().isoformat()
        req0, hit0 = self.http_requests, self.cache_hits
        bars: list[pd.DataFrame] = []
        acts: list[pd.DataFrame] = []
        for sym in syms:
            payload = self._bars(sym, start, end, interval, minutes, refresh=refresh)
            bars.append(payload)
            acts.append(self._splits(sym, start, end, refresh=refresh))
        raw = pd.concat(bars, ignore_index=True) if bars else empty_bars()
        actions = pd.concat(acts, ignore_index=True) if acts else empty_actions()
        if raw.empty:
            raw = _empty_raw()
        if actions.empty:
            actions = empty_actions()
        else:
            actions = actions[pd.to_datetime(actions["date"]) <= pd.Timestamp(end)]
            actions = actions.reset_index(drop=True)
            if actions.empty:
                actions = empty_actions()
        return ProviderHistory(
            raw.sort_values(["symbol", "bar_start"]).reset_index(drop=True),
            actions,
            {
                "provider": self.name,
                "endpoint": self.base_url + BARS_PATH,
                "symbols": syms,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timeframe": timeframe,
                "interval": interval,
                "adjustment": "raw",
                "session": "regular",
                "fetched_at": fetched_at,
                "http_requests": self.http_requests - req0,
                "cache_hits": self.cache_hits - hit0,
                "actions_from": "splits numerator/denominator",
                "actions": int(len(actions)),
            },
        )

    def fetch_sessions(
        self,
        symbols: Iterable[str],
        after: date,
        end: date | None = None,
        timeframe: str = "1h",
    ) -> ProviderHistory:
        end = end or _ny_now(self._now()).date()
        # The frontier's own date is included. The caller drops bars at or before it.
        start = after
        if start > end:
            return ProviderHistory(_empty_raw(), empty_actions(), {"provider": self.name})
        return self.fetch_history(symbols, start, end, timeframe=timeframe)

    def _bars(
        self,
        symbol: str,
        start: date,
        end: date,
        interval: str,
        minutes: int,
        *,
        refresh: bool,
    ) -> pd.DataFrame:
        today = _ny_now(self._now()).date()
        cacheable = end < today
        path = None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self.cache_dir / f"{symbol}_{interval}_{start.isoformat()}_{end.isoformat()}.json"
        if path is not None and path.exists() and not refresh and cacheable:
            self.cache_hits += 1
            log.info("equibles cache hit %s %s %s..%s", symbol, interval, start, end)
            cached = json.loads(path.read_text())
            return _frame(symbol, cached["bars"])
        records, complete = self._pages(symbol, start, end, interval, minutes)
        if path is not None and cacheable and complete:
            path.write_text(json.dumps({"bars": records}))
        return _frame(symbol, records)

    def _pages(
        self, symbol: str, start: date, end: date, interval: str, minutes: int
    ) -> tuple[list[dict[str, Any]], bool]:
        params: dict[str, str] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "interval": interval,
            "session": "regular",
            "adjustment": "raw",
            "format": "csv",
            "limit": "50000",
        }
        rows: list[dict[str, Any]] = []
        complete = True
        cutoff: datetime | None = None
        for _ in range(40):
            page = self._request(BARS_PATH.format(ticker=symbol), params, symbol, csv=True)
            self._reject_holes(symbol, start, page)
            for bar in page["bars"]:
                start_at = to_new_york(bar["time"])
                if not bar.get("final", True):
                    complete = False
                    if cutoff is None or start_at < cutoff:
                        cutoff = start_at
                    continue
                if not on_grid(start_at, minutes):
                    raise ProviderError(
                        f"Equibles returned {symbol} bar {start_at:%Y-%m-%dT%H:%M}, "
                        f"which is not on the {interval} regular-session grid."
                    )
                rows.append(
                    {
                        "time": start_at.strftime("%Y-%m-%dT%H:%M"),
                        "session": bar.get("session") or start_at.date().isoformat(),
                        "open": bar["open"],
                        "high": bar["high"],
                        "low": bar["low"],
                        "close": bar["close"],
                        "volume": bar["volume"],
                    }
                )
            if not page.get("truncated"):
                return _before(rows, cutoff), complete
            if not page["bars"]:
                raise ProviderError(f"Equibles truncated an empty bar page for {symbol}.")
            # Continue from the oldest bar of this page. The wire time is kept.
            params = {k: v for k, v in params.items() if k != "endDate"}
            params["endTime"] = str(page["bars"][-1]["time"])
        raise ProviderError(f"Equibles bar pages for {symbol} did not end.")

    def _reject_holes(self, symbol: str, start: date, page: dict[str, Any]) -> None:
        covered = page.get("coveredFrom")
        if covered and date.fromisoformat(covered) > start:
            raise ProviderError(
                f"Equibles history for {symbol} starts {covered}, after the requested {start}."
            )
        incomplete = page.get("incompleteSessions") or []
        if incomplete:
            raise ProviderError(
                f"Equibles has incomplete sessions for {symbol}: {', '.join(incomplete)}."
            )
        import exchange_calendars as xcals

        cal = xcals.get_calendar("XNYS")
        bad = []
        for raw in page.get("unstoredDates") or []:
            if cal.is_session(pd.Timestamp(raw)):
                bad.append(raw)
        if bad:
            raise ProviderError(
                f"Equibles is missing NYSE sessions for {symbol}: {', '.join(bad)}."
            )

    def _splits(self, symbol: str, start: date, end: date, *, refresh: bool) -> pd.DataFrame:
        today = _ny_now(self._now()).date()
        cacheable = end < today
        path = None
        if self.cache_dir is not None:
            path = self.cache_dir / f"{symbol}_splits_{start.isoformat()}_{end.isoformat()}.json"
        if path is not None and path.exists() and not refresh and cacheable:
            self.cache_hits += 1
            data = json.loads(path.read_text())
        else:
            data = self._split_pages(symbol, start, end)
            if path is not None and cacheable:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
        rows = []
        for item in data:
            when = pd.Timestamp(item["effectiveDate"])
            if when.date() > end:
                continue
            denom = float(item["denominator"])
            if denom == 0:
                raise ProviderError(f"Equibles split for {symbol} has a zero denominator.")
            rows.append(
                {
                    "date": when,
                    "symbol": symbol,
                    "type": "split",
                    "value": float(item["numerator"]) / denom,
                }
            )
        if not rows:
            return empty_actions()
        return pd.DataFrame(rows)

    def _split_pages(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._request(
                SPLITS_PATH.format(ticker=symbol),
                {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "limit": "500",
                    "offset": str(offset),
                },
                symbol,
                csv=False,
            )
            body = page["json"]
            rows.extend(body.get("data") or [])
            if not (body.get("meta") or {}).get("hasMore"):
                return rows
            offset += 500

    def _request(
        self, path: str, params: dict[str, str], symbol: str, *, csv: bool
    ) -> dict[str, Any]:
        if self._key is None:
            raise ProviderAuthError(
                "EQUIBLES_API_KEY is not set. Add it to the environment or .env "
                "(see docs/providers.md)."
            )
        url = self.base_url + path
        headers = {"Authorization": f"Bearer {self._key.get_secret_value()}"}
        attempt = 0
        while True:
            self.http_requests += 1
            log.info("equibles GET %s %s (attempt %d)", path, params, attempt + 1)
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise ProviderError(
                        f"Equibles unreachable for {symbol}: {type(exc).__name__}"
                    ) from None
                self._sleep(self.backoff_seconds * (2**attempt))
                attempt += 1
                continue
            status = resp.status_code
            if status == 401:
                raise ProviderAuthError(
                    "Equibles rejected the API key (HTTP 401). Check EQUIBLES_API_KEY."
                )
            if status == 403:
                raise ProviderAuthError(
                    "Equibles refused the request (HTTP 403). This plan does not include bars."
                )
            if status == 404:
                raise ProviderNotFound(f"Equibles has no listing for {symbol} (HTTP 404).")
            if status == 429:
                raise ProviderRateLimited(
                    "Equibles daily limit reached (HTTP 429). "
                    f"Retry after {resp.headers.get('Retry-After', 'the 00:00 UTC reset')}."
                )
            if status in RETRY_STATUSES:
                if attempt >= self.max_retries:
                    raise ProviderError(f"Equibles server error HTTP {status} for {symbol}.")
                self._sleep(self.backoff_seconds * (2**attempt))
                attempt += 1
                continue
            if status != 200:
                raise ProviderError(f"Equibles returned HTTP {status} for {symbol}.")
            if csv:
                return _csv_page(resp)
            return {"json": resp.json()}


def _before(rows: list[dict[str, Any]], cutoff: datetime | None) -> list[dict[str, Any]]:
    """Drop bars at or after a forming bar so a later slot is not delivered ahead of it."""
    if cutoff is None:
        return rows
    return [r for r in rows if datetime.fromisoformat(r["time"]) < cutoff]


def _csv_page(resp: httpx.Response) -> dict[str, Any]:
    import io

    frame = pd.read_csv(io.StringIO(resp.text))
    bars = []
    if not frame.empty:
        for row in frame.to_dict(orient="records"):
            bars.append(
                {
                    "time": str(row["time"]),
                    "session": str(row.get("session") or ""),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "final": str(row.get("final", "true")).lower() != "false",
                }
            )
    def header_list(name: str) -> list[str]:
        raw = resp.headers.get(name, "")
        return [p for p in raw.split(",") if p] if raw else []

    return {
        "bars": bars,
        "truncated": resp.headers.get("X-Bars-Truncated", "").lower() == "true",
        "coveredFrom": resp.headers.get("X-Bars-Covered-From") or None,
        "incompleteSessions": header_list("X-Bars-Incomplete-Sessions"),
        "unstoredDates": header_list("X-Bars-Unstored-Dates"),
    }


def _frame(symbol: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_raw()
    out = pd.DataFrame(rows)
    out["bar_start"] = pd.to_datetime(out["time"])
    out["date"] = pd.to_datetime(out["session"]).dt.normalize()
    out["symbol"] = symbol
    out["volume"] = out["volume"].astype("int64")
    return out[["bar_start", "date", "symbol", "open", "high", "low", "close", "volume"]]


def _empty_raw() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["bar_start", "date", "symbol", "open", "high", "low", "close", "volume"]
    )
