"""The run analysis through the API: stored on new runs, computed lazily for old ones."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from moneylab.api.app import create_app
from moneylab.demo import rsi_strategy
from moneylab.service import Lab
from test_api import _run, _snapshot, _wait


@pytest.fixture()
def lab_client(tmp_path: Path):  # type: ignore[no-untyped-def]
    lab = Lab.open(tmp_path / "home")
    with TestClient(create_app(lab, web_dir=tmp_path / "no-web")) as c:
        yield lab, c


def test_new_run_has_analysis_trades_and_headline(lab_client: tuple[Lab, TestClient]) -> None:
    lab, c = lab_client
    rid = _run(c, _snapshot(c), 35)
    d = c.get(f"/api/runs/{rid}").json()
    a = d["analysis"]
    assert a["trades"]["count"] == d["trades"] > 0
    assert a["trades"]["low_sample"] is True  # a few trades on the demo fixture
    assert "analysis" not in d["metrics"] and d["win_rate"] == a["trades"]["win_rate"]
    trades = c.get(f"/api/runs/{rid}/trades").json()
    assert len(trades) == a["trades"]["count"]
    t = trades[0]
    assert t["exit_kind"] and t["hold_sessions"] >= 1 and "edge" in t and "mae" in t
    listed = {r["id"]: r for r in c.get("/api/runs").json()}[rid]
    assert listed["cagr"] == d["cagr"] and listed["low_sample"] is True
    assert (lab.store.run_dir(rid) / "trades.parquet").is_file()


def test_old_run_is_computed_on_first_open_and_saved(lab_client: tuple[Lab, TestClient]) -> None:
    lab, c = lab_client
    rid = _run(c, _snapshot(c), 35)
    before = c.get(f"/api/runs/{rid}").json()["analysis"]
    m = {k: v for k, v in lab.store.get(rid)["metrics"].items() if k != "analysis"}
    lab.store.update_metrics(rid, m)
    (lab.store.run_dir(rid) / "trades.parquet").unlink()
    assert not lab.has_analysis(rid)
    again = c.get(f"/api/runs/{rid}").json()
    assert again["analysis"] == before and lab.has_analysis(rid)
    assert len(c.get(f"/api/runs/{rid}/trades").json()) == before["trades"]["count"]


def test_missing_snapshot_skips_gracefully(lab_client: tuple[Lab, TestClient]) -> None:
    lab, c = lab_client
    rid = _run(c, _snapshot(c), 35)
    sid = lab.store.get(rid)["snapshot_id"]
    lab.store.update_metrics(
        rid, {k: v for k, v in lab.store.get(rid)["metrics"].items() if k != "analysis"}
    )
    (lab.settings.snapshot_dir / f"{sid}.parquet").unlink()
    d = c.get(f"/api/runs/{rid}")
    assert d.status_code == 200 and d.json()["analysis"] is None
    assert lab.recompute_all() == {"done": [], "skipped": [rid]}


def test_recompute_all_fills_missing_analysis(lab_client: tuple[Lab, TestClient]) -> None:
    lab, c = lab_client
    sid = _snapshot(c)
    a, b = _run(c, sid, 30), _run(c, sid, 35)
    for rid in (a, b):
        lab.store.update_metrics(
            rid, {k: v for k, v in lab.store.get(rid)["metrics"].items() if k != "analysis"}
        )
    assert sorted(lab.recompute_all()["done"]) == sorted([a, b])
    assert lab.recompute_all() == {"done": [], "skipped": []}
    assert sorted(lab.recompute_all(force=True)["done"]) == sorted([a, b])


def test_idle_cash_in_benchmark_through_the_api(lab_client: tuple[Lab, TestClient]) -> None:
    lab, c = lab_client
    sid = _snapshot(c)
    cfg: dict[str, Any] = rsi_strategy(35).model_dump(mode="json")
    cfg["portfolio"]["idle_cash"] = "benchmark"
    job = _wait(c, c.post("/api/runs", json={"config": cfg, "snapshot_id": sid}).json())
    assert job["status"] == "complete", job
    rid = job["run_ids"][0]
    d = c.get(f"/api/runs/{rid}").json()
    assert d["resolved"]["portfolio"]["idle_cash"] == "benchmark"
    fills = c.get(f"/api/runs/{rid}/fills").json()
    sweeps = [f for f in fills if f["note"] == "sweep"]
    assert sweeps and all(f["symbol"] == "SPY" and f["decision_id"] is None for f in sweeps)
    # Sweep fills are not trades: counts match the strategy's own fills.
    own = [f for f in fills if f["note"] != "sweep"]
    assert d["trades"] == sum(f["side"] == "sell" for f in own)
    assert d["analysis"]["trades"]["count"] == d["trades"]
    eq = c.get(f"/api/runs/{rid}/equity").json()
    assert eq[0]["equity"] > 0 and "sweep_value" in lab.store.ledgers(rid).equity
    # Every ledger endpoint copes with fills that have no decision (the sweeps).
    page = c.get(f"/api/runs/{rid}/decisions", params={"action": "all", "limit": 1000})
    assert page.status_code == 200, page.text
    assert c.get(f"/api/runs/{rid}/trades").status_code == 200
    # Duplicating keeps the setting and reproduces the ledger exactly.
    dup = _wait(c, c.post(f"/api/runs/{rid}/duplicate").json())["run_ids"][0]
    assert c.get(f"/api/runs/{dup}/fills").json() == fills
