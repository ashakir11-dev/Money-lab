"""Gate 1, paper part: a MACD strategy runs in paper mode with no MACD-specific code.

The MACD indicator was added as one new component file; this checks it also works
in forward (paper) testing and matches the historical engine from the same state.
"""

from __future__ import annotations

from pathlib import Path

from moneylab.demo import FIXTURE, UNIVERSE
from moneylab.engine.config import Condition, IndicatorRef, StrategyConfig
from moneylab.service import Lab
from test_paper import END, START, feed, plain, reference, rows, sessions_between


def test_macd_strategy_paper_trades_like_the_historical_engine(tmp_path: Path) -> None:
    lab = Lab.open(tmp_path / "home")
    snap = lab.import_file(FIXTURE)
    cfg = StrategyConfig(
        name="SMA20 cross + MACD histogram > 0",
        universe=UNIVERSE,
        entry=[
            Condition(
                kind="price_cross",
                indicator=IndicatorRef(id="sma", params={"length": 20}),
                params={"direction": "above"},
            ),
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="macd", params={"fast": 8, "output": "histogram"}),
                params={"op": "above", "value": 0},
            ),
        ],
    )
    run_id = lab.run(cfg, snap.id)
    pid = lab.start_paper(run_id, START, name="macd paper")
    results = feed(lab, pid, sessions_between(START, END))
    assert all(r["status"] == "accepted" for r in results)

    decisions = rows(lab, pid, "decisions")
    key = "macd(fast=8,output=histogram,signal=9,slow=26)"
    assert any(key in (d.get("values") or {}) for d in decisions), "MACD values recorded"

    ref = reference(lab, run_id, START, END)
    assert decisions == plain(ref.decisions)
    assert rows(lab, pid, "fills") == plain(ref.fills)
    assert lab.paper_replay(pid)["match"] is True
