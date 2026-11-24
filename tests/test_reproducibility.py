"""Historical inspection and reproducibility (acceptance gate 6)."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from moneylab.components import registry
from moneylab.demo import rsi_strategy
from moneylab.engine.config import VersionMismatch, _flatten_for_test
from moneylab.service import Lab


def _ledgers_equal(lab: Lab, a: str, b: str) -> None:
    la, lb = lab.store.ledgers(a), lab.store.ledgers(b)
    pd.testing.assert_frame_equal(
        la.fills.drop(columns="decision_id"), lb.fills.drop(columns="decision_id")
    )
    pd.testing.assert_frame_equal(la.equity, lb.equity)
    pd.testing.assert_frame_equal(la.decisions, lb.decisions)


def test_changed_default_does_not_change_an_old_run(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    lab: Lab = demo["lab"]
    before = lab.store.get(demo["original"])

    # Change current defaults after the run (costs 5 -> 25 bps, allocation 10% -> 20%).
    monkeypatch.setenv("MONEYLAB_COST_BPS_PER_SIDE", "25")
    monkeypatch.setenv("MONEYLAB_TARGET_ALLOCATION", "0.2")
    new_lab = Lab.open(lab.settings.home)
    assert new_lab.settings.cost_bps_per_side == 25

    reopened = new_lab.store.get(demo["original"])
    assert reopened["resolved"] == before["resolved"]
    p = reopened["resolved"]["portfolio"]
    assert (p["cost_bps_per_side"], p["target_allocation"]) == (5.0, 0.1)

    # Every active setting is present, defaults included.
    keys = set(_flatten_for_test(reopened["resolved"]))
    for k in [
        "entry[0].indicator.params.length",
        "entry[0].indicator.version",
        "entry[1].params.op",
        "entry[1].params.value",
        "exit.params.sessions",
        "portfolio.starting_cash",
        "portfolio.max_positions",
        "execution.engine_version",
        "execution.tie_break",
        "data.snapshot_id",
        "data.adjustment",
        "universe",
        "benchmark",
    ]:
        assert k in keys, k

    # An unchanged duplicate made under the new defaults still reproduces the ledger.
    dup = new_lab.duplicate(demo["original"])
    assert new_lab.store.get(dup)["resolved"]["portfolio"]["cost_bps_per_side"] == 5.0
    _ledgers_equal(new_lab, demo["original"], dup)
    cmp = new_lab.compare(demo["original"], dup)
    assert cmp["config_diff"] == [] and cmp["first_difference"] is None

    # Whereas a fresh run from the same editable config picks up the new defaults.
    fresh = new_lab.run(rsi_strategy(30), demo["snapshot"])
    diff = {d["setting"] for d in new_lab.compare(demo["original"], fresh)["config_diff"]}
    assert diff == {"portfolio.cost_bps_per_side", "portfolio.target_allocation"}


def test_one_threshold_edit_gives_one_difference(demo) -> None:  # type: ignore[no-untyped-def]
    cmp = demo["lab"].compare(demo["original"], demo["changed"])
    assert len(cmp["config_diff"]) == 1
    assert cmp["first_difference"]["symbol"] == "AAA"


def test_duplicate_refuses_when_a_component_version_changed(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    rsi = registry.get_component("rsi")
    monkeypatch.setitem(registry._REGISTRY, "rsi", replace(rsi, version="2.0.0"))
    with pytest.raises(VersionMismatch, match=r"rsi 1.0.0 -> 2.0.0"):
        demo["lab"].duplicate(demo["original"])


def test_corrected_data_is_a_new_snapshot_and_old_runs_still_reproduce(demo) -> None:  # type: ignore[no-untyped-def]
    lab: Lab = demo["lab"]
    bars = lab.snapshot(demo["snapshot"]).bars.copy()
    bars.loc[(bars["symbol"] == "AAA") & (bars["date"] == "2023-04-04"), "close"] = 70.0
    corrected = lab.import_frame(bars, source="correction")
    assert corrected.id != demo["snapshot"]
    dup = lab.duplicate(demo["changed"])
    _ledgers_equal(lab, demo["changed"], dup)
