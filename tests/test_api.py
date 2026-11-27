"""The research API (FastAPI over Lab): background runs, schemas, ledgers, comparison."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from moneylab.api.app import create_app
from moneylab.demo import FIXTURE, rsi_strategy
from moneylab.engine.analytics import TRADE_COLUMNS
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


def test_unlabelled_csv_snapshot_names_itself(client: TestClient) -> None:
    r = client.post(
        "/api/snapshots/csv",
        json={"csv": Path(FIXTURE).read_text(), "filename": Path(FIXTURE).name},
    )
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["label"] == ""
    assert snap["name"] == (
        "file:rsi_divergence.csv · 1d · AAA, BBB, CCC, DDD + 1 · 2023-01-03 → 2023-06-30"
    )
    detail = client.get(f"/api/runs/{_run(client, snap['id'], 30)}").json()
    assert detail["snapshot_name"] == snap["name"]


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
    assert page["rows"][0]["fill"]["qty"] == 135  # DDD bought at the next open

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
    original_name = client.get(f"/api/runs/{original}").json()["name"]
    assert client.get(f"/api/runs/{dup}").json()["name"] == f"{original_name} v2"
    assert (
        client.get(f"/api/runs/{dup}/fills").json()
        == client.get(f"/api/runs/{original}/fills").json()
    )
    cmp = client.get("/api/compare", params={"a": original, "b": dup}).json()
    assert cmp["config_diff"] == [] and cmp["first_difference"] is None


def test_new_strategy_options_through_the_api(client: TestClient) -> None:
    """Exit lists, ranking and sizing are declared and accepted without any UI change."""
    comps = {c["id"]: c for c in client.get("/api/components").json()}
    assert comps["holding_period"]["uses"] == ["exit_rule"]
    assert comps["percent_of_equity"]["uses"] == ["sizing"]
    assert {"indicator_compare", "indicator_cross"} <= set(comps)

    sid = _snapshot(client)
    parent = _run(client, sid, 35)
    cfg = rsi_strategy(35, name="RSI < 35, ranked, two exits").model_dump(mode="json")
    cfg["exits"] = [
        {
            "kind": "signal",
            "conditions": [
                {
                    "kind": "threshold",
                    "indicator": {"id": "rsi", "params": {"length": 14}},
                    "params": {"op": "above", "value": 70},
                },
            ],
        },
        {"kind": "holding_period", "params": {"sessions": 10}},
    ]
    cfg["rank_by"] = {"indicator": {"id": "rsi", "params": {"length": 14}}, "order": "ascending"}
    v = client.post("/api/validate", json={"config": cfg, "snapshot_id": sid}).json()
    assert [e["kind"] for e in v["resolved"]["exits"]] == ["signal", "holding_period"]
    assert "when RSI(14) above 70, or after 10 sessions" in v["summary"]

    r = client.post("/api/runs", json={"config": cfg, "snapshot_id": sid, "parent_id": parent})
    job = _wait(client, r.json())
    assert job["status"] == "complete", job
    detail = client.get(f"/api/runs/{job['run_ids'][0]}").json()
    assert detail["resolved"]["rank_by"]["order"] == "ascending"
    cmp = client.get("/api/compare", params={"a": parent, "b": job["run_ids"][0]}).json()
    changed = {d["setting"] for d in cmp["config_diff"]}
    assert any(s.startswith("exits") for s in changed)
    assert any(s.startswith("rank_by") for s in changed)
    assert not any(s.startswith("exit.") for s in changed)

    bad = {**cfg, "exits": [{"kind": "sma"}]}
    r = client.post("/api/validate", json={"config": bad, "snapshot_id": sid})
    assert r.status_code in (400, 422) and "not an exit rule" in r.text


def test_adding_a_stop_loss_shows_the_first_trade_it_changed(client: TestClient) -> None:
    """Backend version of the plan's check: add a stop to a saved strategy, compare."""
    sid = _snapshot(client)
    parent = _run(client, sid, 35)
    cfg = rsi_strategy(35, name="RSI < 35 with a 0.1% stop").model_dump(mode="json")
    cfg["exits"] = [
        {"kind": "stop_loss", "params": {"pct": 0.001}},
        {"kind": "holding_period", "params": {"sessions": 10}},
    ]
    r = client.post("/api/runs", json={"config": cfg, "snapshot_id": sid, "parent_id": parent})
    job = _wait(client, r.json())
    assert job["status"] == "complete", job
    cmp = client.get("/api/compare", params={"a": parent, "b": job["run_ids"][0]}).json()
    assert {d["setting"] for d in cmp["config_diff"]} >= {"exits[0].kind", "exits[0].params.pct"}
    first = cmp["first_difference"]
    assert first is not None
    assert "stop_loss" in str(first)


# Headline fields of a finished report. The export's summary.json is these, plus the
# metrics and analysis the report page shows — not the raw strategy settings.
_SUMMARY_KEYS = {
    "id",
    "name",
    "status",
    "created_at",
    "finished_at",
    "parent_id",
    "strategy_id",
    "summary",
    "snapshot_id",
    "snapshot_name",
    "source_kind",
    "source_label",
    "engine_version",
    "total_return",
    "benchmark_return",
    "benchmark_symbol",
    "max_drawdown",
    "sharpe",
    "trades",
    "open_positions",
    "win_rate",
    "profit_factor",
    "cagr",
    "edge_mean",
    "edge_t",
    "low_sample",
    "metrics",
    "analysis",
}


def test_export_is_a_zip_of_trades_csv_and_report_summary(client: TestClient) -> None:
    sid = _snapshot(client)
    run_id = _run(client, sid, 30)
    detail = client.get(f"/api/runs/{run_id}").json()
    trades = client.get(f"/api/runs/{run_id}/trades").json()
    assert detail["status"] == "completed"
    assert trades, "the demo run should close at least one trade"

    resp = client.get(f"/api/runs/{run_id}/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f"moneylab-report-{run_id}.zip" in disposition

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        assert archive.namelist() == ["summary.json", "trades.csv"]
        summary = json.loads(archive.read("summary.json"))
        rows = list(csv.DictReader(io.StringIO(archive.read("trades.csv").decode())))

    assert set(summary) == _SUMMARY_KEYS
    assert summary["id"] == run_id
    assert summary["status"] == "completed"
    assert summary["name"] == detail["name"]
    assert summary["summary"] == detail["summary"]
    assert summary["finished_at"] == detail["finished_at"]
    assert summary["snapshot_name"] == detail["snapshot_name"]
    assert summary["engine_version"] == detail["engine_version"]
    for key in (
        "total_return",
        "benchmark_return",
        "benchmark_symbol",
        "max_drawdown",
        "sharpe",
        "trades",
        "open_positions",
        "win_rate",
        "profit_factor",
        "cagr",
        "edge_mean",
        "edge_t",
        "low_sample",
    ):
        assert summary[key] == detail[key]
    assert summary["metrics"] == detail["metrics"]
    assert summary["analysis"] == detail["analysis"]
    assert "resolved" not in summary
    assert list(rows[0]) == TRADE_COLUMNS
    assert len(rows) == len(trades)
    for csv_row, trade in zip(rows, trades, strict=True):
        assert csv_row["symbol"] == trade["symbol"]
        assert csv_row["entry_session"] == trade["entry_session"]
        assert csv_row["exit_session"] == trade["exit_session"]
        assert csv_row["exit_kind"] == trade["exit_kind"]
        assert float(csv_row["qty"]) == pytest.approx(trade["qty"])
        assert float(csv_row["pnl"]) == pytest.approx(trade["pnl"])
        assert float(csv_row["ret"]) == pytest.approx(trade["ret"])
        assert float(csv_row["costs"]) == pytest.approx(trade["costs"])


def test_export_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope/export").status_code == 404


def test_export_refuses_a_run_that_is_not_finished(tmp_path: Path) -> None:
    home = tmp_path / "home"
    app = create_app(Lab.open(home), web_dir=tmp_path / "no-web")
    with TestClient(app) as unfinished:
        sid = _snapshot(unfinished)
        run_id = _run(unfinished, sid, 30)
        with sqlite3.connect(home / "moneylab.sqlite") as db:
            db.execute("UPDATE runs SET status = ? WHERE id = ?", ("running", run_id))
        resp = unfinished.get(f"/api/runs/{run_id}/export")
    assert resp.status_code == 409
    assert "finished" in resp.json()["detail"]
