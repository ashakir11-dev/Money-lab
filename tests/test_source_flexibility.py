"""Universe and source flexibility (acceptance gate 5), fully offline.

The Tiingo adapter is exercised against recorded-style responses served by
httpx.MockTransport; no test here touches the network.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from moneylab.data.actions import POLICY as ACTION_POLICY
from moneylab.demo import FIXTURE, rsi_strategy
from moneylab.engine.config import config_from_resolved
from moneylab.service import Lab
from tiingo_util import END, START, SYMBOLS, TIINGO_DIR, FakeTiingo, payloads_from_csv, recorded


def _ledgers_equal(a: Lab, ra: str, b: Lab, rb: str) -> None:
    la, lb = a.store.ledgers(ra), b.store.ledgers(rb)
    pd.testing.assert_frame_equal(la.decisions, lb.decisions)
    pd.testing.assert_frame_equal(
        la.fills.drop(columns="decision_id"), lb.fills.drop(columns="decision_id")
    )
    pd.testing.assert_frame_equal(la.equity, lb.equity)


def _trades(lab: Lab, run: str) -> list[tuple[object, ...]]:
    f = lab.store.ledgers(run).fills
    return [tuple(x) for x in f[["session", "symbol", "side", "qty"]].itertuples(index=False)]


@pytest.mark.acceptance
def test_same_data_through_tiingo_and_file_gives_identical_runs(tmp_path: Path) -> None:
    file_lab, api_lab = Lab.open(tmp_path / "file"), Lab.open(tmp_path / "api")
    file_snap = file_lab.import_file(FIXTURE)

    fake = FakeTiingo(payloads_from_csv())
    api_snap = api_lab.import_provider(
        SYMBOLS, START, END, provider=fake.provider(api_lab.settings.cache_dir)
    )
    assert api_snap.id == file_snap.id
    assert len(fake.requests) == len(SYMBOLS)  # one request per symbol for the whole range
    assert api_snap.meta["source"] == "tiingo"
    assert api_snap.meta["provenance"]["start"] == START.isoformat()
    assert api_snap.meta["provenance"]["fetched_at"]

    strategy = rsi_strategy(35)
    ra, rb = file_lab.run(strategy, file_snap.id), api_lab.run(strategy, api_snap.id)
    _ledgers_equal(file_lab, ra, api_lab, rb)
    assert _trades(file_lab, ra)  # the comparison is not vacuous


@pytest.mark.acceptance
def test_split_and_dividend_data_is_identical_through_both_adapters(tmp_path: Path) -> None:
    file_lab, api_lab = Lab.open(tmp_path / "file"), Lab.open(tmp_path / "api")
    file_snap = file_lab.import_file(
        TIINGO_DIR / "split_dividend_raw.csv",
        actions_path=TIINGO_DIR / "split_dividend_actions.csv",
    )
    fake = FakeTiingo(recorded("split_dividend"))
    api_snap = api_lab.import_provider(
        SYMBOLS, START, END, label="tiingo", provider=fake.provider(api_lab.settings.cache_dir)
    )
    assert api_snap.id == file_snap.id
    assert api_snap.meta["adjustment"] == file_snap.meta["adjustment"] == ACTION_POLICY
    prov = api_snap.meta["provenance"]
    assert prov["actions"] == 4
    # Our adjustment agrees with Tiingo's adjClose up to the raw prices' rounding.
    assert set(prov["tiingo_adj_ratio_spread"]) == set(SYMBOLS)
    assert max(prov["tiingo_adj_ratio_spread"].values()) < 1e-5
    # Raw prices and actions are stored next to the snapshot, so it can be re-derived.
    stored = api_lab.settings.snapshot_dir
    assert (stored / f"{api_snap.id}.raw.parquet").exists()
    acts = pd.read_parquet(stored / f"{api_snap.id}.actions.parquet")
    assert sorted(acts["type"]) == ["dividend", "dividend", "split", "split"]

    strategy = rsi_strategy(35)
    ra, rb = file_lab.run(strategy, file_snap.id), api_lab.run(strategy, api_snap.id)
    _ledgers_equal(file_lab, ra, api_lab, rb)

    # And the adjusted series reproduces the original (already adjusted) fixture's trades.
    plain_lab = Lab.open(tmp_path / "plain")
    plain = plain_lab.import_file(FIXTURE)
    pd.testing.assert_frame_equal(
        api_snap.bars[["open", "high", "low", "close"]],
        plain.bars[["open", "high", "low", "close"]],
        rtol=1e-5,
    )
    rp = plain_lab.run(strategy, plain.id)
    assert _trades(api_lab, rb) == _trades(plain_lab, rp)


def test_identical_data_from_two_sources_is_one_snapshot_with_two_import_records(
    lab: Lab,
) -> None:
    from moneylab.data.snapshot import list_imports

    a = lab.import_file(FIXTURE)
    fake = FakeTiingo(payloads_from_csv())
    b = lab.import_provider(SYMBOLS, START, END, provider=fake.provider(lab.settings.cache_dir))
    assert a.id == b.id
    log = list_imports(lab.settings.snapshot_dir)
    assert [(r["snapshot_id"], r["source"]) for r in log] == [
        (a.id, "file:rsi_divergence.csv"),
        (a.id, "tiingo"),
    ]


@pytest.mark.acceptance
def test_same_saved_strategy_on_two_watchlists_with_no_code_changes(tmp_path: Path) -> None:
    lab = Lab.open(tmp_path / "home")
    fake = FakeTiingo(recorded("split_dividend"))
    snap = lab.import_provider(SYMBOLS, START, END, provider=fake.provider(None))
    base = lab.run(rsi_strategy(35), snap.id)
    saved = config_from_resolved(lab.store.get(base)["resolved"])

    lab.save_watchlist("Large caps", ["AAA", "BBB"], label="user-selected, Jan 2023")
    lab.save_watchlist("Small caps", ["ccc", "DDD", "CCC"], label="user-selected, Jan 2023")
    ra = lab.run(saved, snap.id, watchlist="Large caps")
    rb = lab.run(saved, snap.id, watchlist="Small caps")

    a, b = lab.store.get(ra)["resolved"], lab.store.get(rb)["resolved"]
    assert a["universe"] == ["AAA", "BBB"] and b["universe"] == ["CCC", "DDD"]
    assert (a["watchlist"]["name"], a["watchlist"]["version"]) == ("Large caps", 1)
    assert b["watchlist"]["members"] == ["CCC", "DDD"]
    assert b["watchlist"]["label"] == "user-selected, Jan 2023"
    assert a["watchlist"]["created_at"]
    for key in ("entry", "exit", "portfolio", "benchmark", "execution", "data"):
        assert a[key] == b[key], key
    diff = {d["setting"] for d in lab.compare(ra, rb)["config_diff"]}
    assert diff == {"universe", "watchlist.name", "watchlist.members", "watchlist.created_at"}
    syms_a = set(lab.store.ledgers(ra).decisions["symbol"])
    syms_b = set(lab.store.ledgers(rb).decisions["symbol"])
    assert syms_a == {"AAA", "BBB"} and syms_b == {"CCC", "DDD"}

    # Changing a watchlist adds a version; the earlier run still records what it used,
    # and an exact duplicate keeps the watchlist it ran on.
    v2 = lab.save_watchlist("Large caps", ["AAA", "BBB", "CCC"], label="added CCC")
    assert v2["version"] == 2
    assert lab.watchlist("Large caps", 1)["members"] == ["AAA", "BBB"]
    assert lab.store.get(ra)["resolved"]["watchlist"]["members"] == ["AAA", "BBB"]
    dup = lab.duplicate(ra)
    assert lab.store.get(dup)["resolved"]["watchlist"]["version"] == 1
    _ledgers_equal(lab, ra, lab, dup)
    # Saving identical contents does not create a new version.
    assert lab.save_watchlist("Large caps", ["CCC", "BBB", "AAA"], "added CCC")["version"] == 2
    assert [w["name"] for w in lab.watchlists()] == ["Large caps", "Small caps"]
