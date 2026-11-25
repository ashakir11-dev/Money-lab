"""Accounting, benchmark and metric conventions from the design (step S1).

Every expected number below is worked out by hand next to its assertion.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from fixtures_util import bars, day, fills, level_strategy, run
from moneylab.demo import rsi_strategy, run_rsi_demo
from moneylab.engine.reporting import metrics
from moneylab.service import Lab

APP = Path(__file__).resolve().parents[1] / "src" / "moneylab" / "dashboard" / "app.py"


def _worked_example():  # type: ignore[no-untyped-def]
    # Design s.4 worked check: $100,000 account, 10% target, 5 bps per side.
    # Day 1 close 100 > 99.5 -> intent for floor(10,000 / 100) = 100 shares.
    # Day 2 open 101: gross 10,100.00, cost 5.05, cash 89,894.95. Day 2 close 102:
    # equity 89,894.95 + 100 x 102 = 100,094.95.
    df = bars({"WK": [(99.0, 99.0), (99.0, 100.0), (101.0, 102.0)]})
    cfg = level_strategy(
        ["WK"], 99.5, hold=50, starting_cash=100_000, target_allocation=0.1, cost_bps_per_side=5
    )
    return run(df, cfg)


def test_design_worked_accounting_check() -> None:
    r = _worked_example()
    assert fills(r) == [(day(2), "WK", "buy", 100, 101.0, 5.05, 89_894.95)]
    assert r.fills["gross"].tolist() == [10_100.00]
    assert r.equity["equity"].tolist() == [100_000.00, 100_000.00, 100_094.95]


def test_benchmark_is_bought_with_costs_and_records_when() -> None:
    # The benchmark (WK here) is bought at the first session's open 99 with the same
    # 5 bps: shares floor(100,000 / (99 x 1.0005)) = floor(1009.6) = 1009,
    # gross 99,891.00, cost 49.9455 -> 49.95, cash left 59.05.
    # Valued at closes 99, 100, 102: 99,950.05, 100,959.05, 102,977.05.
    r = _worked_example()
    assert r.meta["benchmark"] == {
        "symbol": "WK",
        "invested_session": day(0),
        "price": 99.0,
        "shares": 1009,
        "cost": 49.95,
        "cash_left": 59.05,
    }
    assert r.equity["benchmark_equity"].tolist() == [99_950.05, 100_959.05, 102_977.05]


def test_trades_are_round_trips_and_open_positions_are_separate() -> None:
    m = metrics(_worked_example().equity, _worked_example().fills)
    assert (m["trades"], m["open_positions"]) == (0, 1)


def test_sharpe_is_undefined_when_returns_never_vary() -> None:
    eq = pd.DataFrame({"equity": [100.0, 100.0, 100.0], "positions_value": [0.0] * 3})
    m = metrics(eq, pd.DataFrame(columns=["side", "cost"]))
    assert m["sharpe"] is None
    assert "undefined" in m["sharpe_assumptions"]


def test_saved_runs_carry_benchmark_purchase_and_open_positions(demo) -> None:  # type: ignore[no-untyped-def]
    m = demo["lab"].store.get(demo["changed"])["metrics"]
    assert m["open_positions"] == 0 and m["trades"] == 2
    b = m["benchmark"]
    assert b["symbol"] == "SPY" and b["invested_session"] and b["shares"] > 0 and b["cost"] > 0


def test_compare_defaults_to_the_newest_run_and_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    lab = Lab.open()
    demo = run_rsi_demo(lab)
    # An unrelated run made after the parent, then a child of the original: the default
    # comparison must be child vs original, not child vs the run just before it.
    lab.run(rsi_strategy(40, name="unrelated"), demo["snapshot"])
    child = lab.run(rsi_strategy(33, name="child"), demo["snapshot"], parent_id=demo["original"])
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["page"] = "Compare"
    at.run()
    assert not at.exception, at.exception
    assert at.selectbox(key="cmp.a").value == demo["original"]
    assert at.selectbox(key="cmp.b").value == child


def test_run_page_shows_benchmark_purchase_and_watchlist_selection_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    lab = Lab.open()
    demo = run_rsi_demo(lab)
    wl = lab.save_watchlist("core", ["AAA", "BBB", "CCC", "DDD"], label="test list")
    lab.run(rsi_strategy(35, name="on a watchlist"), demo["snapshot"], watchlist="core")
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["page"] = "Runs"
    at.run()
    assert not at.exception, at.exception
    text = " ".join(c.value for c in at.caption)
    assert "SPY bought" in text and "Open positions at the end: 0" in text
    assert f"Watchlist core v1 selected {str(wl['created_at'])[:10]}" in text
