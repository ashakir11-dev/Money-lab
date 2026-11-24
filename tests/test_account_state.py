"""The step-by-step account used by historical runs can stop, persist and resume."""

from __future__ import annotations

import json

import pandas as pd

from moneylab.data.snapshot import MarketData
from moneylab.demo import rsi_strategy
from moneylab.engine.config import resolve
from moneylab.engine.simulator import Account, MarketView, simulate
from moneylab.settings import Settings


def test_resume_from_json_state_gives_identical_ledgers(demo) -> None:  # type: ignore[no-untyped-def]
    lab = demo["lab"]
    resolved = resolve(rsi_strategy(35), Settings(_env_file=None))
    data = MarketData.build(lab.snapshot(demo["snapshot"]).bars, [*resolved["universe"], "SPY"])
    full = simulate(resolved, data)

    view = MarketView.build(resolved, data)
    acc = Account(resolved)
    half = len(view.sessions) // 2
    for i in range(half):
        acc.step(view, i)
    saved = json.loads(json.dumps(acc.state()))  # what a restart would read back
    resumed = Account.from_state(resolved, saved)
    for i in range(half, len(view.sessions)):
        resumed.step(view, i)
    out = resumed.result(data)

    pd.testing.assert_frame_equal(out.fills, full.fills)
    pd.testing.assert_frame_equal(out.equity, full.equity)
    pd.testing.assert_frame_equal(out.decisions, full.decisions)


def test_sessions_must_be_processed_in_order(demo) -> None:  # type: ignore[no-untyped-def]
    resolved = resolve(rsi_strategy(35), Settings(_env_file=None))
    data = MarketData.build(demo["lab"].snapshot(demo["snapshot"]).bars, resolved["universe"])
    view = MarketView.build(resolved, data)
    acc = Account(resolved)
    acc.step(view, 0)
    try:
        acc.step(view, 0)
    except ValueError as e:
        assert "in order" in str(e)
    else:
        raise AssertionError("re-processing a session must be refused")
