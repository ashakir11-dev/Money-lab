"""Paper trading through the API, as the dashboard's Paper trading page uses it.

Start a paper account from the saved RSI < 35 run, deliver new sessions (one of them
twice), read the latest processed session, cash and fills, and replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from moneylab.api.app import create_app
from moneylab.data.snapshot import read_import_file
from moneylab.demo import FIXTURE, rsi_strategy, run_rsi_demo
from moneylab.service import Lab
from test_equibles_provider import _provider
from tiingo_util import TEST_KEY, FakeTiingo, payloads_from_csv


def test_paper_start_deliver_and_replay(tmp_path: Path) -> None:
    lab = Lab.open(tmp_path / "home")
    demo = run_rsi_demo(lab)
    bars = read_import_file(FIXTURE)
    new = bars[(bars["date"] > "2023-03-31") & (bars["date"] <= "2023-04-06")]
    again = new[new["date"] == "2023-04-04"]

    with TestClient(create_app(lab, web_dir=tmp_path / "no-web")) as c:
        r = c.post("/api/paper", json={"run_id": demo["changed"], "start": "2023-03-31"})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        assert r.json()["start_session"] == "2023-03-31"
        assert [a["id"] for a in c.get("/api/paper").json()] == [pid]

        r = c.post(f"/api/paper/{pid}/sessions", json={"csv": new.to_csv(index=False)})
        assert [x["status"] for x in r.json()] == ["accepted"] * 4
        r = c.post(f"/api/paper/{pid}/sessions", json={"csv": again.to_csv(index=False)})
        assert [x["status"] for x in r.json()] == ["duplicate"]

        d = c.get(f"/api/paper/{pid}").json()
        st = d["status"]
        assert st["latest_processed"] == "2023-04-06"
        assert st["next_expected"] == "2023-04-10"
        assert len(st["positions"]) == 1
        assert round(st["cash"], 2) == 89962.06
        assert len(d["fills"]) == 1 and len(d["equity"]) == 4

        rep = c.post(f"/api/paper/{pid}/replay").json()
        assert rep["match"] is True

        bad = c.post("/api/paper", json={"run_id": demo["changed"], "start": "1999-01-01"})
        assert bad.status_code == 422
        assert c.get("/api/paper/nope").status_code == 404


def test_gates_endpoint_lists_six_gates(tmp_path: Path) -> None:
    with TestClient(create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no")) as c:
        gates = c.get("/api/gates").json()
    assert [g["number"] for g in gates] == [1, 2, 3, 4, 5, 6]
    assert all(g["evidence"] for g in gates)


def test_tiingo_import_then_paper_fetch_delivers_the_newer_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real-data path through the API with an offline fake Tiingo: import history,
    run, start a paper account, then fetch and process the sessions after it."""
    monkeypatch.setenv("TIINGO_API_KEY", TEST_KEY)
    fake = FakeTiingo(payloads_from_csv())

    def provider(self: Lab, name: str = "tiingo") -> Any:
        return fake.provider(self.settings.cache_dir)

    monkeypatch.setattr(Lab, "provider", provider)
    lab = Lab.open(tmp_path / "home")
    with TestClient(create_app(lab, web_dir=tmp_path / "no-web")) as c:
        r = c.post(
            "/api/snapshots/tiingo",
            json={"symbols": ["aaa", "BBB", "SPY"], "start": "2023-01-03", "end": "2023-03-31"},
        )
        assert r.status_code == 200, r.text
        snap = r.json()
        assert snap["source_kind"] == "real" and snap["last_date"].startswith("2023-03-31")
        cfg = rsi_strategy(35).model_copy(update={"universe": ["AAA", "BBB"]})
        run_id = lab.run(cfg, snap["id"])
        pid = c.post("/api/paper", json={"run_id": run_id}).json()["id"]

        got = c.post(f"/api/paper/{pid}/fetch").json()
        assert got and {g["status"] for g in got} == {"accepted"}
        assert got[0]["session"] == "2023-04-03"
        st = c.get(f"/api/paper/{pid}").json()["status"]
        assert st["latest_processed"] == got[-1]["session"]
        # Fetching again delivers nothing new: anything re-sent is a duplicate, no new fills.
        fills = len(c.get(f"/api/paper/{pid}").json()["fills"])
        again = c.post(f"/api/paper/{pid}/fetch").json()
        assert {g["status"] for g in again} <= {"duplicate"}
        assert len(c.get(f"/api/paper/{pid}").json()["fills"]) == fills
        assert c.post(f"/api/paper/{pid}/replay").json()["match"] is True


def test_tiingo_import_uses_a_watchlist_as_the_symbol_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A watchlist supplies the symbols. Extra symbols are added. The snapshot records
    which version was used. An empty label is named from that configuration."""
    monkeypatch.setenv("TIINGO_API_KEY", TEST_KEY)
    fake = FakeTiingo(payloads_from_csv())

    def provider(self: Lab, name: str = "tiingo") -> Any:
        return fake.provider(self.settings.cache_dir)

    monkeypatch.setattr(Lab, "provider", provider)
    lab = Lab.open(tmp_path / "home")
    lab.save_watchlist("core", ["aaa", "BBB"], "names I follow")
    lab.save_watchlist("just-ddd", ["DDD"], "")
    with TestClient(create_app(lab, web_dir=tmp_path / "no-web")) as c:
        r = c.post(
            "/api/snapshots/tiingo",
            json={
                "watchlist": "core",
                "symbols": ["spy"],
                "start": "2023-01-03",
                "end": "2023-03-31",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["symbols"] == ["AAA", "BBB", "SPY"]
        assert body["label"] == ""
        assert body["name"].startswith("tiingo · ") and "core v1" in body["name"]
        recorded_list = lab.snapshot(body["id"]).meta["provenance"]["watchlist"]
        assert recorded_list["name"] == "core"
        assert recorded_list["version"] == 1
        assert recorded_list["label"] == "names I follow"
        assert recorded_list["created_at"]

        named = c.post(
            "/api/snapshots/tiingo",
            json={
                "watchlist": "just-ddd",
                "start": "2023-01-03",
                "end": "2023-03-31",
                "label": "mine",
            },
        )
        assert named.status_code == 200, named.text
        assert named.json()["symbols"] == ["DDD"]
        assert named.json()["label"] == "mine"
        assert named.json()["name"] == "mine · tiingo · 1d · just-ddd v1 · 2023-01-03 → 2023-03-31"


def test_equibles_import_merges_a_typed_label_into_the_snapshot_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equibles stores the same display name as Tiingo: the label leads the configuration."""

    def provider(self: Lab, name: str = "equibles") -> Any:
        assert name == "equibles"
        return _provider(self.settings.cache_dir)

    monkeypatch.setattr(Lab, "provider", provider)
    lab = Lab.open(tmp_path / "home")
    lab.save_watchlist("just-aapl", ["AAPL"], "")
    with TestClient(create_app(lab, web_dir=tmp_path / "no-web")) as c:
        named = c.post(
            "/api/snapshots/equibles",
            json={
                "watchlist": "just-aapl",
                "start": "2026-11-25",
                "end": "2026-11-25",
                "label": "  mine  ",
                "timeframe": "1h",
            },
        )
        assert named.status_code == 200, named.text
        body = named.json()
        assert body["label"] == "  mine  "
        assert body["timeframe"] == "1h"
        assert body["name"] == "mine · equibles · 1h · just-aapl v1 · 2026-11-25 → 2026-11-25"

        daily = c.post(
            "/api/snapshots/equibles",
            json={
                "symbols": ["AAPL"],
                "start": "2026-11-25",
                "end": "2026-11-25",
                "label": "daily book",
                "timeframe": "1d",
            },
        )
        assert daily.status_code == 200, daily.text
        assert daily.json()["name"] == "daily book · equibles · 1d · AAPL · 2026-11-25 → 2026-11-25"


def test_tiingo_import_rejects_a_missing_watchlist_before_calling_tiingo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "")
    with TestClient(create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no")) as c:
        missing = c.post(
            "/api/snapshots/tiingo",
            json={"watchlist": "nope", "start": "2023-01-03", "end": "2023-03-31"},
        )
        assert missing.status_code == 404
        assert "nope" in missing.text
        empty = c.post(
            "/api/snapshots/tiingo",
            json={"symbols": [], "start": "2023-01-03", "end": "2023-03-31"},
        )
        assert empty.status_code == 422


def test_tiingo_import_without_a_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "")
    with TestClient(create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no")) as c:
        r = c.post(
            "/api/snapshots/tiingo",
            json={"symbols": ["AAA"], "start": "2023-01-03", "end": "2023-03-31"},
        )
    assert r.status_code == 400 and "key" in r.text.lower()
