"""Trade-chart window and payload."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from moneylab.api.app import create_app
from moneylab.demo import FIXTURE, rsi_strategy
from moneylab.engine.chart import ChartRejected, assemble_chart, chart_window
from moneylab.service import Lab


def test_window_in_the_middle() -> None:
    assert chart_window(400, 100, 250) == (20, 290)


def test_window_clamps_the_start() -> None:
    assert chart_window(400, 10, 11) == (0, 51)


def test_window_clamps_the_end() -> None:
    assert chart_window(100, 90, 95) == (10, 99)


def test_window_keeps_a_long_wait() -> None:
    start, end = chart_window(400, 100, 250)
    assert start <= 100 <= end
    assert start <= 250 <= end
    assert end - start + 1 > 80 + 40


def test_window_keeps_a_signal_after_the_fill() -> None:
    start, end = chart_window(200, 150, 10)
    assert start <= 10 <= end
    assert start <= 150 <= end


def test_window_rejects_an_empty_grid() -> None:
    with pytest.raises(ValueError, match="no bars"):
        chart_window(0, 0, 0)


def _lab(tmp_path: Path) -> tuple[Lab, str]:
    lab = Lab.open(tmp_path / "home")
    snap = lab.import_file(FIXTURE, label="demo")
    return lab, lab.run(rsi_strategy(30), snap.id)


def _pieces(lab: Lab, run_id: str, fill: Any) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    full = lab.store.get(run_id)
    resolved = full["resolved"]
    data = lab.market_data(
        full["snapshot_id"],
        [*list(resolved["universe"]), resolved["benchmark"]],
        resolved["start"],
        resolved["end"],
    )
    did = int(fill["decision_id"])
    row = lab.store.ledgers(run_id).decisions
    row = row[row["decision_id"] == did].iloc[0].to_dict()
    return resolved, data, row


def test_buy_chart_matches_the_stored_rsi(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    resolved, data, decision = _pieces(lab, run_id, buy)
    chart = assemble_chart(resolved, data, decision, buy.to_dict())

    from moneylab.data.bars import session_label

    labels = [session_label(ts) for ts in data.sessions]
    signal_i = labels.index(chart["signal"]["session"])
    fill_i = labels.index(chart["fill"]["session"])
    start, end = chart_window(len(labels), signal_i, fill_i)
    assert chart["sessions"] == labels[start : end + 1]
    assert len(chart["candles"]) == len(chart["sessions"])
    assert chart["fill"]["session"] in chart["sessions"]
    assert chart["signal"]["session"] in chart["sessions"]
    by_key = {s["key"]: s for s in chart["series"]}
    assert by_key["sma(length=20)"]["price_scale"] is True
    assert by_key["rsi(length=14)"]["price_scale"] is False
    at = chart["sessions"].index(chart["signal"]["session"])
    assert by_key["rsi(length=14)"]["values"][at] == decision["values"]["rsi(length=14)"]
    assert len(by_key["rsi(length=14)"]["values"]) == len(chart["sessions"])


def test_round_trip_chart_marks_the_buy_and_the_sell(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    sells = fills[(fills["symbol"] == buy["symbol"]) & (fills["side"] == "sell")]
    assert not sells.empty
    sell = sells.iloc[0]
    resolved, data, buy_decision = _pieces(lab, run_id, buy)
    _, _, sell_decision = _pieces(lab, run_id, sell)
    chart = assemble_chart(
        resolved,
        data,
        buy_decision,
        buy.to_dict(),
        paired=(sell_decision, sell.to_dict()),
    )

    from moneylab.data.bars import session_label

    labels = [session_label(ts) for ts in data.sessions]
    points = [
        labels.index(buy_decision["session"]),
        labels.index(str(buy["session"])),
        labels.index(sell_decision["session"]),
        labels.index(str(sell["session"])),
    ]
    start, end = chart_window(len(labels), min(points), max(points))
    assert chart["sessions"] == labels[start : end + 1]
    assert [leg["fill"]["side"] for leg in chart["legs"]] == ["buy", "sell"]
    assert chart["legs"][0]["fill"]["session"] == str(buy["session"])
    assert chart["legs"][1]["fill"]["session"] == str(sell["session"])
    assert chart["legs"][0]["signal"]["conditions"], "the entry signal is what decided the buy"
    assert chart["legs"][1]["signal"]["reason"] == sell_decision["reason"]
    assert str(buy["session"]) in chart["sessions"]
    assert str(sell["session"]) in chart["sessions"]


def test_holding_period_sell_has_no_conditions(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    sell = fills[fills["side"] == "sell"].iloc[0]
    resolved, data, decision = _pieces(lab, run_id, sell)
    chart = assemble_chart(resolved, data, decision, sell.to_dict())
    assert chart["signal"]["conditions"] == []
    assert chart["signal"]["values"] == {}
    assert chart["signal"]["reason"].startswith("held ")
    assert chart["fill"]["side"] == "sell"


def test_symbol_missing_from_the_grid_is_rejected(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0].to_dict()
    resolved, data, decision = _pieces(lab, run_id, buy)
    buy["symbol"] = "ZZZ"
    with pytest.raises(ChartRejected, match="no data for ZZZ in snapshot"):
        assemble_chart(resolved, data, decision, buy)


def test_signal_off_the_grid_is_rejected(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    resolved, data, decision = _pieces(lab, run_id, buy)
    decision["session"] = "1999-01-01"
    match = "signal session 1999-01-01 is not on the run's bar grid"
    with pytest.raises(ChartRejected, match=match):
        assemble_chart(resolved, data, decision, buy.to_dict())


def _client(lab: Lab, tmp_path: Path) -> TestClient:
    app = create_app(lab, web_dir=tmp_path / "no-web")
    return TestClient(app)


def test_chart_route_returns_the_buy(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    with _client(lab, tmp_path) as client:
        response = client.get(
            f"/api/runs/{run_id}/chart",
            params={"decision_id": int(buy["decision_id"])},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == buy["symbol"]
    assert body["fill"]["side"] == "buy"
    assert body["fill"]["session"] in body["sessions"]
    assert body["signal"]["session"] in body["sessions"]
    rsi = next(s for s in body["series"] if s["key"] == "rsi(length=14)")
    assert rsi["price_scale"] is False
    at = body["sessions"].index(body["signal"]["session"])
    stored = lab.store.ledgers(run_id).decisions
    stored = stored[stored["decision_id"] == int(buy["decision_id"])].iloc[0]
    assert rsi["values"][at] == stored["values"]["rsi(length=14)"]


def test_chart_route_returns_both_legs_of_a_round_trip(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    sell = fills[(fills["symbol"] == buy["symbol"]) & (fills["side"] == "sell")].iloc[0]
    with _client(lab, tmp_path) as client:
        response = client.get(
            f"/api/runs/{run_id}/chart",
            params={
                "decision_id": int(buy["decision_id"]),
                "paired_decision_id": int(sell["decision_id"]),
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [leg["fill"]["side"] for leg in body["legs"]] == ["buy", "sell"]
    assert body["legs"][0]["signal"]["session"] in body["sessions"]
    assert body["legs"][1]["signal"]["session"] in body["sessions"]
    assert body["legs"][0]["fill"]["session"] in body["sessions"]
    assert body["legs"][1]["fill"]["session"] in body["sessions"]


def test_chart_route_unknown_run(tmp_path: Path) -> None:
    lab, _run_id = _lab(tmp_path)
    with _client(lab, tmp_path) as client:
        response = client.get("/api/runs/missing/chart", params={"decision_id": 1})
    assert response.status_code == 404
    assert response.json()["detail"] == "run missing not found"


def test_chart_route_unknown_decision(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": 999999})
    assert response.status_code == 404
    assert response.json()["detail"] == "decision 999999 not found"


def test_chart_route_decision_without_a_fill(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    led = lab.store.ledgers(run_id)
    filled = {int(v) for v in led.fills["decision_id"]}
    open_ids = [int(v) for v in led.decisions["decision_id"] if int(v) not in filled]
    assert open_ids, "the demo run should record a decision that does not fill"
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": open_ids[0]})
    assert response.status_code == 404
    assert response.json()["detail"] == f"decision {open_ids[0]} not found"


def test_chart_route_duplicate_fill(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    path = lab.store.run_dir(run_id) / "fills.parquet"
    fills = pd.read_parquet(path)
    did = int(fills.iloc[0]["decision_id"])
    pd.concat([fills, fills.iloc[[0]]], ignore_index=True).to_parquet(path, index=False)
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": did})
    assert response.status_code == 422
    assert response.json()["detail"] == f"decision {did} has more than one fill"


def test_chart_route_missing_snapshot(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    full = lab.store.get(run_id)
    sid = full["snapshot_id"]
    for suffix in (".parquet", ".json"):
        (lab.settings.snapshot_dir / f"{sid}{suffix}").unlink()
    did = int(lab.store.ledgers(run_id).fills.iloc[0]["decision_id"])
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": did})
    assert response.status_code == 404
    assert response.json()["detail"] == f"snapshot {sid} not found"
