"""Tiingo adapter behaviour: errors, retries, cache, credential hygiene, new sessions.

All HTTP goes to httpx.MockTransport; no test here touches the network.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from moneylab.data.providers import (
    ProviderAuthError,
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    TiingoProvider,
    new_sessions,
)
from moneylab.data.snapshot import COLUMNS, read_import_file
from moneylab.demo import FIXTURE, rsi_strategy
from moneylab.service import Lab
from moneylab.settings import Settings
from tiingo_util import (
    END,
    START,
    SYMBOLS,
    TEST_KEY,
    TIINGO_DIR,
    FakeTiingo,
    payloads_from_csv,
    recorded,
)

D0, D1 = date(2023, 1, 3), date(2023, 1, 31)


def test_request_shape_one_call_per_symbol_with_token_header(tmp_path: Path) -> None:
    fake = FakeTiingo(payloads_from_csv())
    hist = fake.provider(tmp_path).fetch_history(["aaa", "SPY", "AAA"], D0, D1)
    assert [r.url.path for r in fake.requests] == [
        "/tiingo/daily/AAA/prices",
        "/tiingo/daily/SPY/prices",
    ]
    q = fake.requests[0].url.params
    assert (q["startDate"], q["endDate"], q["format"]) == ("2023-01-03", "2023-01-31", "json")
    assert "token" not in str(fake.requests[0].url)  # the key travels only in the header
    assert list(hist.raw.columns) == COLUMNS
    assert hist.raw["date"].min() == pd.Timestamp(D0) and hist.raw["date"].max() <= pd.Timestamp(D1)
    assert hist.actions.empty


def test_share_class_ticker_uses_a_dash() -> None:
    from moneylab.data.providers.tiingo import tiingo_ticker

    assert tiingo_ticker("brk.b") == "BRK-B"


def test_actions_come_from_divcash_and_splitfactor(tmp_path: Path) -> None:
    hist = FakeTiingo(recorded()).provider(tmp_path).fetch_history(SYMBOLS, START, END)
    got = [
        (d.date().isoformat(), s, t, v)
        for d, s, t, v in hist.actions[["date", "symbol", "type", "value"]].itertuples(index=False)
    ]
    assert sorted(got) == [
        ("2023-02-15", "AAA", "dividend", 0.85),
        ("2023-03-01", "BBB", "split", 3.0),
        ("2023-03-17", "SPY", "dividend", 1.5),
        ("2023-05-01", "DDD", "split", 2.0),
    ]


def test_missing_key_fails_clearly_without_a_request(tmp_path: Path) -> None:
    fake = FakeTiingo(payloads_from_csv())
    with pytest.raises(ProviderAuthError, match="TIINGO_API_KEY is not set"):
        fake.provider(tmp_path, key=None).fetch_history(["AAA"], D0, D1)
    assert fake.requests == []


def test_401_and_403_are_credential_errors_without_the_key(tmp_path: Path) -> None:
    fake = FakeTiingo(payloads_from_csv())
    with pytest.raises(ProviderAuthError, match=r"HTTP 401.*TIINGO_API_KEY") as exc:
        fake.provider(tmp_path, key="wrong-key-value").fetch_history(["AAA"], D0, D1)
    assert "wrong-key-value" not in str(exc.value)
    with pytest.raises(ProviderAuthError, match="HTTP 403"):
        FakeTiingo({}, always=403).provider(tmp_path).fetch_history(["AAA"], D0, D1)


def test_404_is_unknown_ticker(tmp_path: Path) -> None:
    fake = FakeTiingo(payloads_from_csv())
    with pytest.raises(ProviderNotFound, match="ZZZZ"):
        fake.provider(tmp_path).fetch_history(["ZZZZ"], D0, D1)
    assert len(fake.requests) == 1  # not retried


def test_429_and_5xx_are_retried_with_backoff(tmp_path: Path) -> None:
    fake = FakeTiingo(payloads_from_csv(), script={"AAA": [429, 503, 429]})
    hist = fake.provider(tmp_path).fetch_history(["AAA"], D0, D1)
    assert not hist.raw.empty
    assert len(fake.requests) == 4
    assert fake.sleeps == [2.0, 4.0, 8.0]
    assert hist.provenance["http_requests"] == 4


def test_retry_after_header_is_honoured(tmp_path: Path) -> None:
    fake = FakeTiingo(payloads_from_csv(), script={"AAA": [429]}, headers={"Retry-After": "7"})
    fake.provider(tmp_path).fetch_history(["AAA"], D0, D1)
    assert fake.sleeps == [7.0]


def test_persistent_429_gives_up_with_a_rate_limit_error(tmp_path: Path) -> None:
    fake = FakeTiingo({}, always=429)
    with pytest.raises(ProviderRateLimited, match="rate limit"):
        fake.provider(tmp_path).fetch_history(["AAA"], D0, D1)
    assert len(fake.requests) == 5  # first attempt + 4 retries
    with pytest.raises(ProviderError, match="HTTP 500"):
        FakeTiingo({}, always=500).provider(tmp_path).fetch_history(["AAA"], D0, D1)


def test_cache_hit_avoids_a_second_request(lab: Lab) -> None:
    fake = FakeTiingo(payloads_from_csv())
    first = lab.import_provider(SYMBOLS, START, END, provider=fake.provider(lab.settings.cache_dir))
    assert len(fake.requests) == len(SYMBOLS)
    # A new provider instance (a later session) reads the on-disk cache.
    again = fake.provider(lab.settings.cache_dir)
    second = lab.import_provider(SYMBOLS, START, END, provider=again)
    assert second.id == first.id
    assert len(fake.requests) == len(SYMBOLS)
    assert again.cache_hits == len(SYMBOLS) and again.http_requests == 0
    # refresh=True bypasses the cache.
    lab.import_provider(SYMBOLS, START, END, provider=again, refresh=True)
    assert len(fake.requests) == 2 * len(SYMBOLS)


def test_recent_ranges_are_not_final_so_new_sessions_are_not_hidden(tmp_path: Path) -> None:
    fake = FakeTiingo(payloads_from_csv())
    p = fake.provider(tmp_path, now=lambda: datetime(2023, 2, 1, 1, 0, tzinfo=UTC))
    p.fetch_history(["AAA"], D0, D1)
    p.fetch_history(["AAA"], D0, D1)
    assert len(fake.requests) == 2 and p.cache_hits == 0


def test_key_never_reaches_logs_files_or_metadata(
    lab: Lab, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeTiingo(payloads_from_csv())
    prov = fake.provider(lab.settings.cache_dir)
    snap = lab.import_provider(SYMBOLS, START, END, provider=prov)
    lab.run(rsi_strategy(35), snap.id)
    assert "tiingo" in caplog.text.lower()  # we did log the requests ...
    assert TEST_KEY not in caplog.text  # ... but never the key
    assert TEST_KEY not in repr(prov) and TEST_KEY not in str(snap.meta)
    for f in lab.settings.home.rglob("*"):
        if f.is_file():
            assert TEST_KEY.encode() not in f.read_bytes(), f


def test_settings_read_the_unprefixed_environment_secret(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", TEST_KEY)
    s = Settings(_env_file=None)
    assert s.tiingo_api_key is not None
    assert s.tiingo_api_key.get_secret_value() == TEST_KEY
    assert TEST_KEY not in repr(s) and TEST_KEY not in str(s.model_dump())
    monkeypatch.setenv("TIINGO_API_KEY", "  ")
    assert Settings(_env_file=None).tiingo_api_key is None
    monkeypatch.delenv("TIINGO_API_KEY")
    assert Settings(_env_file=None).tiingo_api_key is None
    assert isinstance(lab.provider("tiingo"), TiingoProvider)
    with pytest.raises(ValueError, match="unknown provider"):
        lab.provider("nope")


def _truncated_snapshot(lab: Lab, tmp_path: Path, csv: Path, last: str) -> str:
    df = pd.read_csv(csv)
    df = df[df["date"] <= last]
    path = tmp_path / "upto.csv"
    df.to_csv(path, index=False)
    return lab.import_file(path).id


def test_fetch_new_sessions_returns_only_newer_rows_in_import_format(
    lab: Lab, tmp_path: Path
) -> None:
    sid = _truncated_snapshot(lab, tmp_path, FIXTURE, "2023-05-31")
    fake = FakeTiingo(payloads_from_csv())
    new = lab.fetch_new_sessions(sid, end=date(2023, 6, 30), provider=fake.provider(None))
    assert fake.requests[0].url.params["startDate"] == "2023-06-01"
    assert list(new.bars.columns) == COLUMNS
    assert not new.basis_changed
    full = read_import_file(FIXTURE)
    expected = full[full["date"] > "2023-05-31"].reset_index(drop=True)
    pd.testing.assert_frame_equal(new.bars, expected, check_dtype=False)
    # Appending them to the snapshot gives exactly the full fixture's snapshot.
    joined = lab.import_frame(pd.concat([lab.snapshot(sid).bars, new.bars]), source="joined")
    assert joined.id == lab.import_file(FIXTURE).id


def test_new_sessions_report_actions_in_the_window(lab: Lab, tmp_path: Path) -> None:

    sid = _truncated_snapshot(lab, tmp_path, TIINGO_DIR / "split_dividend_raw.csv", "2023-04-28")
    new = lab.fetch_new_sessions(
        sid, ["DDD", "AAA"], end=date(2023, 5, 5), provider=FakeTiingo(recorded()).provider(None)
    )
    assert new.basis_changed
    assert new.actions[["symbol", "type", "value"]].values.tolist() == [["DDD", "split", 2.0]]
    assert sorted(new.bars["symbol"].unique()) == ["AAA", "DDD"]
    assert new.bars["date"].min() == pd.Timestamp("2023-05-01")


def test_new_sessions_is_pure_and_per_symbol() -> None:
    known = read_import_file(FIXTURE)
    known = known[(known["symbol"] != "BBB") | (known["date"] <= "2023-03-31")]
    raw = read_import_file(FIXTURE)
    empty_actions = pd.DataFrame(columns=["date", "symbol", "type", "value"])
    out = new_sessions(known, raw, empty_actions)
    assert set(out.bars["symbol"]) == {"BBB"}
    assert out.bars["date"].min() == pd.Timestamp("2023-04-03")
    assert len(raw) == len(read_import_file(FIXTURE))  # inputs untouched


def test_unknown_symbol_for_new_sessions_is_refused(lab: Lab) -> None:
    sid = lab.import_file(FIXTURE).id
    with pytest.raises(ValueError, match="ZZZ"):
        lab.fetch_new_sessions(sid, ["ZZZ"], provider=FakeTiingo({}).provider(None))


def test_cli_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from moneylab.cli import app

    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    # Empty env wins over a developer `.env` so the missing-key path stays testable.
    monkeypatch.setenv("TIINGO_API_KEY", "")
    args = [
        "fetch",
        "AAA",
        "SPY",
        "--start",
        "2023-01-03",
        "--end",
        "2023-06-30",
        "--source",
        "tiingo",
    ]
    res = CliRunner().invoke(app, args)
    assert res.exit_code == 1 and "TIINGO_API_KEY is not set" in res.output

    fake = FakeTiingo(payloads_from_csv())
    monkeypatch.setattr(Lab, "provider", lambda self, name="tiingo": fake.provider(None))
    res = CliRunner().invoke(app, args)
    assert res.exit_code == 0, res.output
    assert "2 symbols, 248 rows" in res.output and "source tiingo" in res.output


def test_cli_import_with_actions_matches_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from moneylab.cli import app

    home = tmp_path / "home"
    monkeypatch.setenv("MONEYLAB_HOME", str(home))
    raw, acts = TIINGO_DIR / "split_dividend_raw.csv", TIINGO_DIR / "split_dividend_actions.csv"
    res = CliRunner().invoke(app, ["import", str(raw), "--actions", str(acts)])
    assert res.exit_code == 0, res.output
    lab = Lab.open(home)
    snap = lab.import_provider(SYMBOLS, START, END, provider=FakeTiingo(recorded()).provider(None))
    assert f"snapshot {snap.id}" in res.output
