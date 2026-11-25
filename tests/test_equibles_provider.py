"""Equibles adapter: errors, cache, splits, and the regular-session grid."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from moneylab.data.providers.base import ProviderAuthError, ProviderError, ProviderNotFound, ProviderRateLimited
from moneylab.data.providers.equibles import EquiblesProvider
from moneylab.settings import Settings

KEY = "eq_test_key"
DAY = "2026-11-25"
# 09:00, 10:00 and 15:00 New York in November (EST, UTC-5), newest first.
CSV = """time,session,open,high,low,close,volume,vwap,tradeCount,final
2026-11-25T20:00:00Z,2026-11-25,11,11.5,8,9,50,,,true
2026-11-25T15:00:00Z,2026-11-25,10.5,12,10,11,200,,,true
2026-11-25T14:00:00Z,2026-11-25,10,11,9.5,10.5,100,,,true
"""
SPLITS = {"data": [], "meta": {"hasMore": False}}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "api_key" not in str(request.url)
    path = request.url.path
    if path.endswith("/splits"):
        return httpx.Response(200, json=SPLITS)
    return httpx.Response(200, text=CSV)


def _provider(tmp_path: Path, handler=_ok, **kw) -> EquiblesProvider:
    return EquiblesProvider(
        KEY,
        cache_dir=tmp_path,
        client=_client(handler),
        sleep=lambda _s: None,
        now=lambda: datetime(2026, 11, 26, 15, tzinfo=UTC),
        **kw,
    )


def test_hourly_bars_are_new_york_wall_times(tmp_path: Path) -> None:
    hist = _provider(tmp_path).fetch_history(["aapl"], date(2026, 11, 25), date(2026, 11, 25))
    starts = hist.raw["bar_start"].dt.strftime("%H:%M").tolist()
    assert starts == ["09:00", "10:00", "15:00"]
    assert hist.raw["symbol"].tolist() == ["AAPL", "AAPL", "AAPL"]
    assert hist.provenance["interval"] == "1hour"
    assert hist.actions.empty


def test_a_bar_off_the_grid_is_refused(tmp_path: Path) -> None:
    bad = CSV.replace("2026-11-25T20:00:00Z", "2026-11-25T21:00:00Z")  # 16:00 New York

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/splits"):
            return httpx.Response(200, json=SPLITS)
        return httpx.Response(200, text=bad)

    with pytest.raises(ProviderError, match="not on the 1hour"):
        _provider(tmp_path, handler).fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))


def test_split_value_is_numerator_over_denominator_and_a_later_split_is_dropped(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/splits"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"effectiveDate": "2026-11-25", "numerator": 4, "denominator": 1},
                        {"effectiveDate": "2026-12-01", "numerator": 2, "denominator": 1},
                    ],
                    "meta": {"hasMore": False},
                },
            )
        return httpx.Response(200, text=CSV)

    hist = _provider(tmp_path, handler).fetch_history(
        ["AAPL"], date(2026, 11, 25), date(2026, 11, 25)
    )
    assert hist.actions["value"].tolist() == [4.0]
    assert hist.actions["type"].tolist() == ["split"]


def test_a_closed_range_is_cached_and_a_forming_bar_is_not(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    calls = {"bars": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/splits"):
            return httpx.Response(200, json=SPLITS)
        calls["bars"] += 1
        return httpx.Response(200, text=CSV)

    prov = _provider(tmp_path, handler)
    prov.fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    prov.fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    assert calls["bars"] == 1
    assert prov.cache_hits == 2  # bars and splits
    text = "\n".join(p.read_text() for p in (tmp_path / "equibles").rglob("*") if p.is_file())
    assert KEY not in text
    assert KEY not in caplog.text

    forming = CSV.replace("true", "false", 1)  # newest bar still forming
    calls["bars"] = 0

    def live(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/splits"):
            return httpx.Response(200, json=SPLITS)
        calls["bars"] += 1
        return httpx.Response(200, text=forming)

    live_prov = EquiblesProvider(
        KEY,
        cache_dir=tmp_path / "live",
        client=_client(live),
        sleep=lambda _s: None,
        now=lambda: datetime(2026, 11, 25, 20, tzinfo=UTC),  # still that session
    )
    hist = live_prov.fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    assert "15:00" not in hist.raw["bar_start"].dt.strftime("%H:%M").tolist()
    live_prov.fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    assert calls["bars"] == 2  # today is never a cache hit


def test_auth_plan_missing_and_limit(tmp_path: Path) -> None:
    def make(status: int):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "no", "status": status}})

        return handler

    with pytest.raises(ProviderAuthError, match="EQUIBLES_API_KEY is not set"):
        EquiblesProvider(None, client=_client(make(200))).fetch_history(
            ["AAPL"], date(2026, 11, 25), date(2026, 11, 25)
        )
    with pytest.raises(ProviderAuthError, match="HTTP 401"):
        _provider(tmp_path, make(401)).fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    with pytest.raises(ProviderAuthError, match="does not include bars"):
        _provider(tmp_path, make(403)).fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    with pytest.raises(ProviderNotFound, match="AAPL"):
        _provider(tmp_path, make(404)).fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    with pytest.raises(ProviderRateLimited, match="429"):
        _provider(tmp_path, make(429)).fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))


def test_covered_from_and_incomplete_sessions_fail(tmp_path: Path) -> None:
    def covered(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/splits"):
            return httpx.Response(200, json=SPLITS)
        return httpx.Response(200, text=CSV, headers={"X-Bars-Covered-From": "2026-11-26"})

    with pytest.raises(ProviderError, match="starts 2026-11-26"):
        _provider(tmp_path, covered).fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))

    def incomplete(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/splits"):
            return httpx.Response(200, json=SPLITS)
        return httpx.Response(
            200, text=CSV, headers={"X-Bars-Incomplete-Sessions": "2026-11-25"}
        )

    with pytest.raises(ProviderError, match="incomplete"):
        _provider(tmp_path / "b", incomplete).fetch_history(
            ["AAPL"], date(2026, 11, 25), date(2026, 11, 25)
        )


def test_key_round_trip_and_blank_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EQUIBLES_API_KEY", KEY)
    assert Settings(_env_file=None).equibles_api_key.get_secret_value() == KEY  # type: ignore[union-attr]
    monkeypatch.setenv("EQUIBLES_API_KEY", "  ")
    assert Settings(_env_file=None).equibles_api_key is None


def test_client_ignores_an_environment_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    assert EquiblesProvider(KEY)._client.trust_env is False


def test_dividend_route_is_never_requested(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/splits"):
            return httpx.Response(200, json=SPLITS)
        return httpx.Response(200, text=CSV)

    _provider(tmp_path, handler).fetch_history(["AAPL"], date(2026, 11, 25), date(2026, 11, 25))
    assert not any("dividend" in p for p in seen)
    assert not any("technical" in p for p in seen)
