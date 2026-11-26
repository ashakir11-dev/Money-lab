"""Strategy versions are immutable. Runs hang off a version."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moneylab.api.app import create_app
from moneylab.demo import rsi_strategy
from moneylab.service import Lab


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no-web")
    with TestClient(app) as api:
        yield api


def test_rule_change_is_a_child_and_a_rerun_is_not(demo: dict) -> None:
    lab: Lab = demo["lab"]
    original = lab.store.get(demo["original"])
    changed = lab.store.get(demo["changed"])
    assert original["strategy_id"]
    assert changed["strategy_id"] != original["strategy_id"]
    assert lab.strategy(changed["strategy_id"])["parent_id"] == original["strategy_id"]

    again = lab.run(
        rsi_strategy(30, name="same again"),
        demo["snapshot"],
        strategy_id=original["strategy_id"],
    )
    assert lab.store.get(again)["strategy_id"] == original["strategy_id"]
    assert len(lab.strategy_runs(original["strategy_id"])) == 2


def test_save_without_a_run_and_a_repeat_save_is_the_same_version(demo: dict) -> None:
    lab: Lab = demo["lab"]
    original = lab.store.get(demo["original"])
    saved = lab.save_strategy(rsi_strategy(30, name="Kept"), strategy_id=original["strategy_id"])
    assert saved == original["strategy_id"]
    assert lab.strategy(saved)["name"] == "Kept"
    assert lab.strategy_runs(saved)

    edited = lab.save_strategy(rsi_strategy(40, name="RSI 40"), strategy_id=saved)
    assert edited != saved
    assert lab.strategy(edited)["parent_id"] == saved
    assert lab.strategy_runs(edited) == []


def test_a_different_universe_does_not_create_a_version(demo: dict) -> None:
    lab: Lab = demo["lab"]
    sid = lab.store.get(demo["original"])["strategy_id"]
    cfg = rsi_strategy(30).model_copy(update={"universe": ["AAA", "BBB"]})
    run_id = lab.run(cfg, demo["snapshot"], strategy_id=sid)
    assert lab.store.get(run_id)["strategy_id"] == sid


def test_families_rank_by_each_runs_own_benchmark_gap(demo: dict) -> None:
    lab: Lab = demo["lab"]
    families = lab.strategy_families()
    assert families
    gaps = [row["best_gap"] for row in families if row["best_gap"] is not None]
    assert gaps == sorted(gaps, reverse=True)
    top = families[0]
    assert top["parent_id"] is None
    assert top["best_run_id"]
    scored = [row for row in top["versions"] if row["best_gap"] is not None]
    assert scored
    assert top["best_gap"] == max(row["best_gap"] for row in scored)


def test_old_runs_grow_strategy_versions_on_open(demo: dict) -> None:
    lab: Lab = demo["lab"]
    home = lab.settings.home
    with lab.store._conn() as conn:
        conn.execute("UPDATE runs SET strategy_id = NULL")
        conn.execute("DELETE FROM strategies")
    reopened = Lab.open(home)
    original = reopened.store.get(demo["original"])
    changed = reopened.store.get(demo["changed"])
    assert original["strategy_id"]
    assert reopened.strategy(changed["strategy_id"])["parent_id"] == original["strategy_id"]
    again = reopened.store.get(demo["original"])
    assert again["strategy_id"] == original["strategy_id"]


def test_api_lists_a_family_and_saves_a_child_without_a_run(client: TestClient) -> None:
    from moneylab.demo import FIXTURE

    loaded = client.post(
        "/api/snapshots/csv",
        json={"csv": Path(FIXTURE).read_text(), "filename": Path(FIXTURE).name, "label": "demo"},
    )
    assert loaded.status_code == 200, loaded.text
    snapshot = loaded.json()["id"]
    queued = client.post(
        "/api/runs",
        json={"config": rsi_strategy(30).model_dump(mode="json"), "snapshot_id": snapshot},
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()
    for _ in range(300):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("complete", "failed"):
            break
    assert job["status"] == "complete", job
    original = job["run_ids"][0]
    detail = client.get(f"/api/runs/{original}").json()
    assert detail["strategy_id"]

    families = client.get("/api/strategies").json()
    assert families[0]["id"] == detail["strategy_id"]
    assert families[0]["run_count"] == 1

    saved = client.post(
        "/api/strategies",
        json={
            "config": rsi_strategy(40, name="RSI 40").model_dump(mode="json"),
            "strategy_id": detail["strategy_id"],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["parent_id"] == detail["strategy_id"]
    assert body["run_count"] == 0
    assert body["name"] == "RSI 40"
