"""Tiingo End-of-Day adapter.

Endpoint: ``GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=&endDate=``
with header ``Authorization: Token <key>``. Each item has ``date`` (ISO timestamp),
raw ``open/high/low/close/volume``, Tiingo's own ``adj*`` fields, ``divCash`` (cash
dividend with ex-date ``date``) and ``splitFactor`` (new shares per old share on the
split date, 1.0 otherwise).

Design for the free tier's small request budget:

* one request per symbol for the whole range;
* an on-disk response cache under ``<home>/cache/tiingo``. A response whose range
  ended well before it was fetched is final and is reused forever (``refresh=True``
  bypasses it); a range that reaches the last day or two is always re-fetched, so new
  sessions are never hidden;
* retries with exponential backoff (honouring ``Retry-After``) on 429, 5xx and network
  errors; clear errors for 401/403 (credential) and 404 (unknown ticker).

The key is read from settings (``TIINGO_API_KEY``), kept as a ``SecretStr``, sent only
in the request header and never logged, cached or written to snapshot metadata.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from pydantic import SecretStr

from moneylab.data.actions import ACTION_COLUMNS, adjust
from moneylab.data.providers.base import (
    ProviderAuthError,
    ProviderError,
    ProviderHistory,
    ProviderNotFound,
    ProviderRateLimited,
    empty_actions,
    empty_bars,
)
from moneylab.data.snapshot import COLUMNS

log = logging.getLogger(__name__)

BASE_URL = "https://api.tiingo.com"
PRICES_PATH = "/tiingo/daily/{ticker}/prices"
META_PATH = "/tiingo/daily/{ticker}"
# A cached range is final once its end is at least this many days before the fetch date.
FINAL_AFTER_DAYS = 2
RETRY_STATUSES = {429, 500, 502, 503, 504}


def tiingo_ticker(symbol: str) -> str:
    """Tiingo writes share classes with a dash (BRK-B); the lab uses BRK.B or BRK-B."""
    return symbol.strip().upper().replace(".", "-")


class TiingoProvider:
    name = "tiingo"

    def __init__(
        self,
        api_key: SecretStr | str | None,
        *,
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        max_retries: int = 4,
        backoff_seconds: float = 2.0,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if isinstance(api_key, str):
            api_key = SecretStr(api_key) if api_key.strip() else None
        self._key = api_key
        self.cache_dir = cache_dir / "tiingo" if cache_dir is not None else None
        self._client = client or httpx.Client(timeout=timeout)
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._now = now
        self.http_requests = 0
        self.cache_hits = 0

    def __repr__(self) -> str:
        return f"TiingoProvider(base_url={self.base_url!r}, key={'set' if self._key else 'unset'})"

    # Public API ---------------------------------------------------------------------
    def fetch_history(
        self, symbols: Iterable[str], start: date, end: date, *, refresh: bool = False
    ) -> ProviderHistory:
        syms = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not syms:
            raise ValueError("no symbols given")
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        fetched_at = self._now().isoformat()
        req0, hit0 = self.http_requests, self.cache_hits
        bars: list[pd.DataFrame] = []
        acts: list[pd.DataFrame] = []
        adj_check: dict[str, float] = {}
        for sym in syms:
            payload = self._prices(sym, start, end, refresh=refresh)
            b, a, tiingo_adj = parse_prices(sym, payload)
            bars.append(b)
            acts.append(a)
            if tiingo_adj is not None and not b.empty:
                spread = adjustment_spread(adjust(b, a), tiingo_adj)
                if spread is not None:
                    adj_check[sym] = spread
        raw = pd.concat(bars, ignore_index=True) if bars else empty_bars()
        actions = pd.concat(acts, ignore_index=True) if acts else empty_actions()
        if raw.empty:
            raw = empty_bars()
        if actions.empty:
            actions = empty_actions()
        provenance = {
            "provider": self.name,
            "endpoint": self.base_url + PRICES_PATH,
            "symbols": syms,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "fetched_at": fetched_at,
            "http_requests": self.http_requests - req0,
            "cache_hits": self.cache_hits - hit0,
            "price_basis": "raw open/high/low/close/volume",
            "actions_from": "divCash (ex-date) and splitFactor (split date)",
            "adjusted_by": "moneylab.data.actions.adjust",
            "actions": len(actions),
        }
        if adj_check:
            # Informational: spread of Tiingo adjClose / our adjusted close (~0 expected).
            provenance["tiingo_adj_ratio_spread"] = adj_check
        return ProviderHistory(
            raw.sort_values(["symbol", "date"]).reset_index(drop=True),
            actions.reset_index(drop=True),
            provenance,
        )

    def fetch_sessions(
        self, symbols: Iterable[str], after: date, end: date | None = None
    ) -> ProviderHistory:
        end = end or self._now().date()
        start = after + timedelta(days=1)
        if start > end:
            return ProviderHistory(empty_bars(), empty_actions(), {"provider": self.name})
        return self.fetch_history(symbols, start, end)

    def metadata(self, symbol: str) -> dict[str, Any]:
        """Name, exchange and first/last price dates for one ticker (one request)."""
        data = self._request(META_PATH.format(ticker=tiingo_ticker(symbol)), {}, symbol)
        if not isinstance(data, dict):
            raise ProviderError(f"unexpected Tiingo metadata response for {symbol}")
        return data

    # Cache --------------------------------------------------------------------------
    def _cache_path(self, symbol: str, start: date, end: date) -> Path | None:
        if self.cache_dir is None:
            return None
        return (
            self.cache_dir / f"{tiingo_ticker(symbol)}_{start.isoformat()}_{end.isoformat()}.json"
        )

    def _prices(self, symbol: str, start: date, end: date, *, refresh: bool) -> list[Any]:
        path = self._cache_path(symbol, start, end)
        if path is not None and path.exists() and not refresh:
            entry = json.loads(path.read_text())
            if entry.get("final"):
                self.cache_hits += 1
                log.info("tiingo cache hit %s %s..%s", symbol, start, end)
                return list(entry["payload"])
        params = {"startDate": start.isoformat(), "endDate": end.isoformat(), "format": "json"}
        data = self._request(PRICES_PATH.format(ticker=tiingo_ticker(symbol)), params, symbol)
        if not isinstance(data, list):
            raise ProviderError(f"unexpected Tiingo price response for {symbol}")
        if path is not None:
            fetched = self._now()
            final = end <= fetched.date() - timedelta(days=FINAL_AFTER_DAYS)
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "symbol": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "fetched_at": fetched.isoformat(),
                "final": final,
                "payload": data,
            }
            path.write_text(json.dumps(entry))
        return data

    # HTTP ---------------------------------------------------------------------------
    def _request(self, path: str, params: dict[str, str], symbol: str) -> Any:
        if self._key is None:
            raise ProviderAuthError(
                "TIINGO_API_KEY is not set. Add it to the environment or .env "
                "(see docs/providers.md)."
            )
        url = self.base_url + path
        headers = {
            "Authorization": f"Token {self._key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        attempt = 0
        while True:
            self.http_requests += 1
            log.info("tiingo GET %s %s (attempt %d)", path, params, attempt + 1)
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise ProviderError(
                        f"Tiingo unreachable for {symbol}: {type(exc).__name__}"
                    ) from None
                self._sleep(self._delay(attempt, None))
                attempt += 1
                continue
            status = resp.status_code
            if status in (401, 403):
                raise ProviderAuthError(
                    f"Tiingo rejected the API key (HTTP {status}). Check TIINGO_API_KEY."
                )
            if status == 404:
                raise ProviderNotFound(f"Tiingo has no daily prices for {symbol} (HTTP 404).")
            if status in RETRY_STATUSES:
                if attempt >= self.max_retries:
                    if status == 429:
                        raise ProviderRateLimited(
                            "Tiingo rate limit reached (HTTP 429) after "
                            f"{attempt + 1} attempts. The free tier allows few requests per "
                            "hour and day; retry later. Cached ranges cost no requests."
                        )
                    raise ProviderError(f"Tiingo server error HTTP {status} for {symbol}.")
                self._sleep(self._delay(attempt, resp.headers.get("Retry-After")))
                attempt += 1
                continue
            if status != 200:
                raise ProviderError(f"Tiingo returned HTTP {status} for {symbol}.")
            data = resp.json()
            if isinstance(data, dict) and "detail" in data and len(data) == 1:
                detail = str(data["detail"])
                if "not found" in detail.lower():
                    raise ProviderNotFound(f"Tiingo has no daily prices for {symbol}.")
                raise ProviderError(f"Tiingo error for {symbol}: {detail[:200]}")
            return data

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return float(self.backoff_seconds * (2**attempt))


def parse_prices(
    symbol: str, payload: list[dict[str, Any]]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series | None]:
    """Turn one Tiingo price response into raw bars, actions and Tiingo's adjClose.

    Rows with a missing or non-positive price are dropped (they become missing bars,
    which the lab never fills). Only ``divCash`` and ``splitFactor`` become actions;
    Tiingo's own adjusted fields are kept solely for the cross-check.
    """
    sym = symbol.strip().upper()
    if not payload:
        return empty_bars(), empty_actions(), None
    df = pd.DataFrame(payload)
    missing = [c for c in ("date", "open", "high", "low", "close", "volume") if c not in df]
    if missing:
        raise ProviderError(f"Tiingo response for {sym} lacks field(s) {missing}")
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None).dt.normalize()
    prices = ["open", "high", "low", "close"]
    df[prices] = df[prices].apply(pd.to_numeric, errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    ok = df[prices].notna().all(axis=1) & (df[prices] > 0).all(axis=1)
    df = df[ok].copy()
    df["symbol"] = sym
    df["volume"] = df["volume"].round().astype("int64")
    bars = df[COLUMNS].reset_index(drop=True)

    rows: list[dict[str, Any]] = []
    split = pd.to_numeric(df.get("splitFactor", pd.Series(1.0, index=df.index)), errors="coerce")
    div = pd.to_numeric(df.get("divCash", pd.Series(0.0, index=df.index)), errors="coerce")
    for d, v in zip(df["date"], split.fillna(1.0), strict=True):
        if abs(float(v) - 1.0) > 1e-12:
            rows.append({"date": d, "symbol": sym, "type": "split", "value": float(v)})
    for d, v in zip(df["date"], div.fillna(0.0), strict=True):
        if float(v) > 0:
            rows.append({"date": d, "symbol": sym, "type": "dividend", "value": float(v)})
    actions = pd.DataFrame(rows, columns=ACTION_COLUMNS) if rows else empty_actions()

    tiingo_adj: pd.Series | None = None
    if "adjClose" in df:
        tiingo_adj = pd.Series(
            pd.to_numeric(df["adjClose"], errors="coerce").to_numpy(), index=df["date"]
        ).dropna()
    return bars, actions, tiingo_adj


def adjustment_spread(adjusted: pd.DataFrame, tiingo_adj: pd.Series) -> float | None:
    """Relative spread of ``Tiingo adjClose / our adjusted close`` over the range.

    Both series are total-return adjusted; ours is anchored at the end of the requested
    range, Tiingo's at its latest date, so the ratio should be constant and the spread
    close to 0. A large value flags a methodology or data difference worth inspecting.
    """
    ours = adjusted.set_index("date")["close"]
    ratio = (tiingo_adj / ours).dropna()
    if ratio.empty:
        return None
    return round(float(ratio.max() / ratio.min() - 1.0), 9)
