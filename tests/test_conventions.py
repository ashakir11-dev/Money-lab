"""Accounting, benchmark and metric conventions from the design (step S1).

Every expected number below is worked out by hand next to its assertion.
"""

from __future__ import annotations

import pandas as pd

from fixtures_util import bars, day, fills, level_strategy, run
from moneylab.engine.reporting import metrics


def _worked_example():  # type: ignore[no-untyped-def]
    # Design s.4 worked check: $100,000 account, 10% target. A stored 5 bps cost is not charged.
    # Day 1 close 100 > 99.5 -> intent for floor(10,000 / 100) = 100 shares.
    # Day 2 open 101: gross 10,100.00, cost 0, cash 89,900.00. Day 2 close 102:
    # equity 89,900.00 + 100 x 102 = 100,100.00.
    df = bars({"WK": [(99.0, 99.0), (99.0, 100.0), (101.0, 102.0)]})
    cfg = level_strategy(
        ["WK"], 99.5, hold=50, starting_cash=100_000, target_allocation=0.1, cost_bps_per_side=5
    )
    return run(df, cfg)


def test_design_worked_accounting_check() -> None:
    r = _worked_example()
    assert fills(r) == [(day(2), "WK", "buy", 100, 101.0, 0.0, 89_900.00)]
    assert r.fills["gross"].tolist() == [10_100.00]
    assert r.equity["equity"].tolist() == [100_000.00, 100_000.00, 100_100.00]


def test_benchmark_is_bought_at_the_first_open_and_records_when() -> None:
    # The benchmark (WK here) is bought at the first session's open 99 with no transaction
    # cost: shares floor(100,000 / 99) = 1010, gross 99,990.00, cost 0, cash left 10.00.
    # Valued at closes 99, 100, 102: 100,000.00, 101,010.00, 103,030.00.
    r = _worked_example()
    assert r.meta["benchmark"] == {
        "symbol": "WK",
        "invested_session": day(0),
        "price": 99.0,
        "shares": 1010,
        "cost": 0.0,
        "cash_left": 10.0,
    }
    assert r.equity["benchmark_equity"].tolist() == [100_000.00, 101_010.00, 103_030.00]


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
    assert b["symbol"] == "SPY" and b["invested_session"] and b["shares"] > 0 and b["cost"] == 0
