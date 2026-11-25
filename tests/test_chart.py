"""Trade-chart window and payload."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
