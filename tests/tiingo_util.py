"""An offline stand-in for the Tiingo End-of-Day API, built on httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from moneylab.data.providers import TiingoProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CSV = ROOT / "fixtures" / "data" / "rsi_divergence.csv"
TIINGO_DIR = ROOT / "fixtures" / "tiingo"
# Obviously fake; the tests check it never reaches logs, files or metadata.
TEST_KEY = "test-key-0123456789abcdef-do-not-leak"
START, END = date(2023, 1, 3), date(2023, 6, 30)
SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "SPY"]


def recorded(name: str = "split_dividend") -> dict[str, list[dict[str, Any]]]:
    """Recorded-style payloads committed under fixtures/tiingo/<name>/."""
    return {p.stem: json.loads(p.read_text()) for p in sorted((TIINGO_DIR / name).glob("*.json"))}


def payloads_from_csv(path: Path = FIXTURE_CSV) -> dict[str, list[dict[str, Any]]]:
    """Tiingo-shaped payloads for a file with no corporate actions (adj = raw)."""
    df = pd.read_csv(path)
    out: dict[str, list[dict[str, Any]]] = {}
    for sym, g in df.groupby("symbol"):
        out[str(sym)] = [
            {
                "date": f"{r.date}T00:00:00.000Z",
                "close": r.close,
                "high": r.high,
                "low": r.low,
                "open": r.open,
                "volume": int(r.volume),
                "adjClose": r.close,
                "adjHigh": r.high,
                "adjLow": r.low,
                "adjOpen": r.open,
                "adjVolume": int(r.volume),
                "divCash": 0.0,
                "splitFactor": 1.0,
            }
            for r in g.itertuples()
        ]
    return out


class FakeTiingo:
    """Serves payloads by ticker, filtered by startDate/endDate like the real API.

    ``script`` maps a ticker to a queue of status codes returned before the payload
    (e.g. ``[429, 429]``); ``always`` forces one status for every request.
    """

    def __init__(
        self,
        payloads: dict[str, list[dict[str, Any]]],
        *,
        script: dict[str, list[int]] | None = None,
        always: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payloads = payloads
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.always = always
        self.extra_headers = headers or {}
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Token {TEST_KEY}":
            return httpx.Response(401, json={"detail": "Invalid token."})
        parts = request.url.path.strip("/").split("/")  # tiingo/daily/{t}/prices
        ticker = parts[2].upper()
        if self.always is not None:
            return httpx.Response(self.always, headers=self.extra_headers, json={})
        queue = self.script.get(ticker)
        if queue:
            return httpx.Response(queue.pop(0), headers=self.extra_headers, json={})
        if ticker not in self.payloads:
            return httpx.Response(404, json={"detail": f"Error: Ticker '{ticker}' not found"})
        lo = request.url.params.get("startDate", "0000-00-00")
        hi = request.url.params.get("endDate", "9999-99-99")
        rows = [r for r in self.payloads[ticker] if lo <= r["date"][:10] <= hi]
        return httpx.Response(200, json=rows)

    def provider(
        self,
        cache_dir: Path | None,
        key: str | None = TEST_KEY,
        now: Callable[[], Any] | None = None,
    ) -> TiingoProvider:
        kw: dict[str, Any] = {} if now is None else {"now": now}
        return TiingoProvider(
            key,
            cache_dir=cache_dir,
            client=httpx.Client(transport=httpx.MockTransport(self)),
            sleep=self.sleeps.append,
            **kw,
        )
