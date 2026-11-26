"""Home-page summary: counts, the highest-return run, and the newest paper account."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from moneylab.api.app import _paper_line, create_app
from moneylab.service import Lab
from test_api import _run, _snapshot


@pytest.fixture()
def client(tmp_path: Path):  # type: ignore[no-untyped-def]
    app = create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        yield c


def test_paper_line_wording() -> None:
    blocked = _paper_line(
        {"paper_id": "p", "name": "Desk", "blocked": "engine moved", "missing": [], "waiting": []}
    )
    assert blocked == {
        "paper_id": "p",
        "name": "Desk",
        "status": "blocked",
        "detail": "engine moved",
    }
    waiting = _paper_line(
        {
            "paper_id": "p",
            "name": "",
            "blocked": None,
            "missing": ["2023-04-04"],
            "waiting": ["2023-04-05"],
        }
    )
    assert waiting["status"] == "waiting"
    assert waiting["name"] == "p"
    assert waiting["detail"] == "Waiting on 2 sessions"
    one = _paper_line(
        {"paper_id": "p", "name": "Desk", "blocked": None, "missing": ["2023-04-04"], "waiting": []}
    )
    assert one["detail"] == "Waiting on 1 session"
    caught = _paper_line(
        {
            "paper_id": "p",
            "name": "Desk",
            "blocked": None,
            "missing": [],
            "waiting": [],
            "latest_processed": "2023-06-30",
        }
    )
    assert caught["status"] == "caught_up"
    assert caught["detail"] == "Caught up through 2023-06-30"


def test_overview_is_empty_for_a_new_lab(client: TestClient) -> None:
    assert client.get("/api/overview").json() == {
        "snapshots": 0,
        "strategies": 0,
        "best": None,
        "paper": None,
    }


def test_overview_picks_the_highest_return_and_the_newest_paper(client: TestClient) -> None:
    sid = _snapshot(client)
    first = _run(client, sid, 30)
    second = _run(client, sid, 35)
    details = {rid: client.get(f"/api/runs/{rid}").json() for rid in (first, second)}
    winner = max(details.values(), key=lambda d: (d["total_return"], d["created_at"], d["id"]))
    before = client.get("/api/overview").json()
    assert before["snapshots"] == 1
    assert before["strategies"] == 2
    assert before["paper"] is None

    started = client.post("/api/paper", json={"run_id": first, "name": "Desk"})
    assert started.status_code == 200, started.text

    body = client.get("/api/overview").json()
    assert body["snapshots"] == len(client.get("/api/snapshots").json())
    assert body["strategies"] == 2
    best = body["best"]
    assert best["run_id"] == winner["id"]
    assert best["name"] == winner["name"]
    assert best["total_return"] == pytest.approx(winner["total_return"])
    start = winner["resolved"]["portfolio"]["starting_cash"]
    final = winner["metrics"]["final_equity"]
    assert best["pnl"] == pytest.approx(final - start)
    assert best["benchmark_symbol"] == "SPY"
    assert best["benchmark_return"] == pytest.approx(winner["benchmark_return"])
    paper = body["paper"]
    assert paper["paper_id"] == started.json()["id"]
    assert paper["name"] == "Desk"
    assert paper["status"] == "waiting"
    assert paper["detail"].startswith("Waiting on ")


def test_overview_skips_a_paper_account_it_cannot_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _snapshot(client)
    run_id = _run(client, sid, 30)
    good = client.post("/api/paper", json={"run_id": run_id, "name": "Good"}).json()["id"]
    lab: Any = client.app.state.lab  # type: ignore[attr-defined]
    real = lab.paper_status

    def flaky(paper_id: str, as_of: Any = None) -> dict[str, Any]:
        if paper_id != good:
            raise KeyError(paper_id)
        return real(paper_id, as_of)

    monkeypatch.setattr(lab, "paper_status", flaky)
    # Newest account is listed first. Insert a newer id the store does not have by
    # wrapping paper_accounts so the unreadable account is tried first.
    monkeypatch.setattr(
        lab,
        "paper_accounts",
        lambda: [{"id": "missing"}, {"id": good}],
    )
    paper = client.get("/api/overview").json()["paper"]
    assert paper["paper_id"] == good
    assert paper["status"] == "waiting"
