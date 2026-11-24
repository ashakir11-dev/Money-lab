"""The research API (FastAPI over Lab): background runs, schemas, ledgers, comparison."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from moneylab.api.app import create_app
from moneylab.demo import FIXTURE, rsi_strategy
from moneylab.service import Lab


@pytest.fixture()
def client(tmp_path: Path):  # type: ignore[no-untyped-def]
    app = create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        yield c


def _wait(c: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    for _ in range(300):
        job = c.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("complete", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job['id']} did not finish")


def _snapshot(c: TestClient) -> str:
    r = c.post(
        "/api/snapshots/csv",
        json={"csv": Path(FIXTURE).read_text(), "filename": Path(FIXTURE).name, "label": "demo"},
    )
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["source_kind"] == "demo" and "AAA" in snap["symbols"]
    return str(snap["id"])


def _run(c: TestClient, sid: str, threshold: float, parent: str | None = None) -> str:
    body = {
        "config": rsi_strategy(threshold).model_dump(mode="json"),
        "snapshot_id": sid,
        "parent_id": parent,
    }
    r = c.post("/api/runs", json=body)
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("queued", "running", "complete")
    job = _wait(c, r.json())
    assert job["status"] == "complete", job
    return str(job["run_ids"][0])


def test_health_and_component_schemas(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    comps = {c["id"]: c for c in client.get("/api/components").json()}
    assert {"sma", "rsi", "macd", "breadth"} <= set(comps)
    rsi = comps["rsi"]
    assert [p["name"] for p in rsi["params"]] == ["length"] and rsi["version"]
    assert comps["sma"]["price_scale"] is True and comps["breadth"]["universe"] is True
    assert client.get("/api/defaults").json()["max_positions"] == 10


def test_run_in_background_then_compare_with_parent(client: TestClient) -> None:
    sid = _snapshot(client)
    original = _run(client, sid, 30)
    changed = _run(client, sid, 35, parent=original)

    runs = client.get("/api/runs").json()
    assert [r["id"] for r in runs][:2] == [changed, original]
    assert runs[0]["parent_id"] == original and runs[0]["summary"].startswith("Buy when")

    detail = client.get(f"/api/runs/{changed}").json()
    assert detail["metrics"]["benchmark"]["symbol"] == "SPY"
    eq = client.get(f"/api/runs/{changed}/equity").json()
    assert eq[0]["equity"] == 100_000.0 and "benchmark_equity" in eq[0]
    fills = client.get(f"/api/runs/{changed}/fills").json()
    assert [(f["session"], f["symbol"], f["side"]) for f in fills][-2:] == [
        ("2023-04-05", "AAA", "buy"),
        ("2023-04-20", "AAA", "sell"),
    ]

    page = client.get(
        f"/api/runs/{changed}/decisions", params={"action": ["enter"], "limit": 1}
    ).json()
    assert page["total"] == 2 and len(page["rows"]) == 1

    cmp = client.get("/api/compare", params={"a": original, "b": changed}).json()
    assert cmp["is_parent"] is True and cmp["data_differs"] is False
    assert cmp["config_diff"] == [{"setting": "entry[1].params.value", "a": 30.0, "b": 35.0}]
    fd = cmp["first_difference"]
    assert (fd["session"], fd["symbol"]) == ("2023-04-04", "AAA")
    assert fd["b"]["fill"]["qty"] == 134


def test_invalid_strategy_is_rejected_before_queueing(client: TestClient) -> None:
    sid = _snapshot(client)
    cfg = rsi_strategy(30).model_dump(mode="json")
    cfg["entry"][1]["indicator"]["params"] = {"length": 0}
    r = client.post("/api/runs", json={"config": cfg, "snapshot_id": sid})
    assert r.status_code == 422
    assert client.get("/api/jobs").json() == []


def test_unknown_ids_are_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404
    body = {"config": rsi_strategy(30).model_dump(mode="json"), "snapshot_id": "nope"}
    assert client.post("/api/runs", json=body).status_code == 404


def test_duplicate_reproduces_the_ledger(client: TestClient) -> None:
    sid = _snapshot(client)
    original = _run(client, sid, 35)
    job = _wait(client, client.post(f"/api/runs/{original}/duplicate").json())
    dup = job["run_ids"][0]
    assert (
        client.get(f"/api/runs/{dup}/fills").json()
        == client.get(f"/api/runs/{original}/fills").json()
    )
    cmp = client.get("/api/compare", params={"a": original, "b": dup}).json()
    assert cmp["config_diff"] == [] and cmp["first_difference"] is None
