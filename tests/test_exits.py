"""Price-level exit rules: stop_loss, take_profit and trailing_stop.

Every fixture enters one position: the entry passes only when the close is strictly
between 9.5 and 10.5 (so it passes once and never again while held or after the sale),
sizing is floor(0.5 x 1000 / decision close) and costs are zero. Expected decisions and
cash are worked out by hand next to each assertion; prices are chosen so every number
is exact.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fixtures_util import bars, day, decisions, fills, run
from moneylab.api.app import create_app
from moneylab.components.registry import ExitContext, get_component, list_components
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    resolve,
)
from moneylab.service import Lab
from moneylab.settings import Settings

TRADING = ("enter", "exit", "skip")
CLOSE = IndicatorRef(id="sma", params={"length": 1})  # SMA(1) is the close


def strategy(exits: list[ExitRule], universe: list[str] | None = None) -> StrategyConfig:
    universe = universe or ["XA"]
    return StrategyConfig(
        name="exit fixture",
        universe=universe,
        benchmark=universe[-1],
        entry=[
            Condition(kind="threshold", indicator=CLOSE, params={"op": "above", "value": 9.5}),
            Condition(kind="threshold", indicator=CLOSE, params={"op": "below", "value": 10.5}),
        ],
        exits=exits,
        portfolio=PortfolioSettings(starting_cash=1000, target_allocation=0.5, cost_bps_per_side=0),
    )


def exit_row(r: Any) -> dict[str, Any]:
    rows = r.decisions[r.decisions["action"] == "exit"]
    assert len(rows) == 1
    return dict(rows.iloc[0])


# Day 1 close 10 is the only close inside (9.5, 10.5): enter, qty = floor(500 / 10) = 50.
# Day 2 open 10 fills it: cash 1000 - 50 x 10 = 500. Entry price 10.
ENTRY = [(9.0, 9.0), (9.0, 10.0), (10.0, 9.5)]
BOUGHT = (day(2), "XA", "buy", 50, 10.0, 0.0, 500.0)


def test_stop_loss_fires_at_the_level_and_a_gap_down_sells_at_the_lower_open() -> None:
    # Level = 10 x (1 - 0.08) = 9.2. Day 2 close 9.5 > 9.2: hold. Day 3 close 9.2 <= 9.2:
    # exit (a close exactly at the level fires). Day 4 opens at 8.5, below the level:
    # the sale is at that open, 50 x 8.5 = 425, cash 500 + 425 = 925.00.
    df = bars({"XA": [*ENTRY, (9.5, 9.2), (8.5, 8.5)]})
    r = run(df, strategy([ExitRule(kind="stop_loss")]))
    assert decisions(r, *TRADING) == [
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(3), "XA", "exit", "stop_loss: close 9.2 <= 9.2 (8% below entry 10)"),
    ]
    assert fills(r) == [BOUGHT, (day(4), "XA", "sell", 50, 8.5, 0.0, 925.0)]
    row = exit_row(r)
    assert row["qty"] == 50
    assert {k: row[k] for k in ("close", "entry_price", "level")} == {
        "close": 9.2,
        "entry_price": 10.0,
        "level": 9.2,
    }
    assert r.equity["equity"].iloc[-1] == 925.0


def test_take_profit_fires_at_or_above_the_level() -> None:
    # Level = 10 x (1 + 0.15) = 11.5. Day 3 close 11 < 11.5: hold. Day 4 close 11.5 >= 11.5:
    # exit. Day 5 open 12: 50 x 12 = 600, cash 500 + 600 = 1100.00.
    df = bars({"XA": [*ENTRY, (9.5, 11.0), (11.0, 11.5), (12.0, 12.0)]})
    r = run(df, strategy([ExitRule(kind="take_profit")]))
    assert decisions(r, *TRADING) == [
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(4), "XA", "exit", "take_profit: close 11.5 >= 11.5 (15% above entry 10)"),
    ]
    assert fills(r) == [BOUGHT, (day(5), "XA", "sell", 50, 12.0, 0.0, 1100.0)]
    row = exit_row(r)
    assert (row["close"], row["entry_price"], row["level"]) == (11.5, 10.0, 11.5)


# Closes after the day 2 fill: 11, 12, 12.5, 11.5, 13, 11.7. Peak close and 10% level:
#   day 2: peak 11   level 9.9    close 11   hold
#   day 3: peak 12   level 10.8   close 12   hold
#   day 4: peak 12.5 level 11.25  close 12.5 hold
#   day 5: peak 12.5 level 11.25  close 11.5 hold (a dip, still above the level)
#   day 6: peak 13   level 11.7   close 13   hold (the peak rises again)
#   day 7: peak 13   level 11.7   close 11.7 exit (11.7 <= 11.7)
# Day 8 open 11.6: 50 x 11.6 = 580, cash 500 + 580 = 1080.00.
TRAIL = [
    (9.0, 9.0),
    (9.0, 10.0),
    (10.0, 11.0),
    (11.0, 12.0),
    (12.0, 12.5),
    (12.5, 11.5),
    (11.5, 13.0),
    (13.0, 11.7),
    (11.6, 11.6),
]


def test_trailing_stop_follows_the_peak_close_then_fires() -> None:
    r = run(bars({"XA": TRAIL}), strategy([ExitRule(kind="trailing_stop")]))
    assert decisions(r, *TRADING) == [
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(7), "XA", "exit", "trailing_stop: close 11.7 <= 11.7 (10% below peak close 13)"),
    ]
    assert fills(r) == [BOUGHT, (day(8), "XA", "sell", 50, 11.6, 0.0, 1080.0)]
    row = exit_row(r)
    assert {k: row[k] for k in ("close", "entry_price", "level", "peak_close")} == {
        "close": 11.7,
        "entry_price": 10.0,
        "level": 11.7,
        "peak_close": 13.0,
    }
    # A fixed stop from the entry (10 x 0.9 = 9) would still be holding at the end.
    r2 = run(bars({"XA": TRAIL}), strategy([ExitRule(kind="stop_loss", params={"pct": 0.1})]))
    assert decisions(r2, "exit") == []


@pytest.mark.parametrize(
    ("first", "reason"),
    [
        ("stop_loss", "stop_loss: close 9.2 <= 9.2 (8% below entry 10)"),
        ("holding_period", "held 2 sessions >= 2"),
    ],
)
def test_stop_loss_and_holding_period_on_the_same_close(first: str, reason: str) -> None:
    # Day 3 close 9.2 hits the stop, and day 3 is the second session held (day 2 fill is
    # session one): both fire, the first in the list gives the reason. Day 4 open 9:
    # 50 x 9 = 450, cash 500 + 450 = 950.00 either way.
    stop = ExitRule(kind="stop_loss")
    hold = ExitRule(kind="holding_period", params={"sessions": 2})
    exits = [stop, hold] if first == "stop_loss" else [hold, stop]
    r = run(bars({"XA": [*ENTRY, (9.5, 9.2), (9.0, 9.0)]}), strategy(exits))
    assert decisions(r, "exit") == [(day(3), "XA", "exit", reason)]
    assert fills(r) == [BOUGHT, (day(4), "XA", "sell", 50, 9.0, 0.0, 950.0)]


def test_a_missing_bar_never_fires_and_the_next_bar_does() -> None:
    # XA has no bar on day 3 (XB, the benchmark, does; it never enters: closes 20).
    # Day 3: no close, so no exit decision. Day 4 close 9 <= 9.2: exit.
    # Day 5 open 8.8: 50 x 8.8 = 440, cash 500 + 440 = 940.00.
    df = bars(
        {
            "XA": [*ENTRY, None, (9.0, 9.0), (8.8, 8.8)],
            "XB": [(20.0, 20.0)] * 6,
        }
    )
    r = run(df, strategy([ExitRule(kind="stop_loss")], universe=["XA", "XB"]))
    assert decisions(r, *TRADING) == [
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(4), "XA", "exit", "stop_loss: close 9 <= 9.2 (8% below entry 10)"),
    ]
    assert (day(3), "XA", "ineligible", "missing_bar") in decisions(r)
    assert fills(r) == [BOUGHT, (day(5), "XA", "sell", 50, 8.8, 0.0, 940.0)]


@pytest.mark.parametrize("rule", ["stop_loss", "take_profit", "trailing_stop"])
def test_no_close_means_hold(rule: str) -> None:
    # Called directly: a NaN close holds even where any real close would fire (pct 0.9
    # puts take_profit's level at 19; peak 100 puts the trailing level at 10).
    fn = get_component(rule).exit_fn
    assert fn is not None
    ctx = ExitContext("XA", 50, 10.0, 3, math.nan, 100.0, {})
    assert fn(ctx, {"pct": 0.9}) is None


def test_pct_is_validated_when_the_strategy_is_resolved() -> None:
    settings = Settings(_env_file=None)
    resolved = resolve(strategy([ExitRule(kind="trailing_stop")]), settings)
    assert resolved["exits"][0]["params"] == {"pct": 0.1}
    assert resolved["exits"][0]["version"] == "1.0.0"
    for bad, msg in [
        (0.95, r"pct must be <= 0\.9"),
        (0.0005, r"pct must be >= 0\.001"),
        (-0.1, r"pct must be >= 0\.001"),
        ("8%", "pct must be a number"),
    ]:
        with pytest.raises(ValueError, match=msg):
            resolve(strategy([ExitRule(kind="stop_loss", params={"pct": bad})]), settings)
    with pytest.raises(ValueError, match="unknown parameter"):
        resolve(strategy([ExitRule(kind="take_profit", params={"percent": 0.1})]), settings)
    # The bounds themselves are allowed.
    for ok in (0.001, 0.9):
        resolve(strategy([ExitRule(kind="stop_loss", params={"pct": ok})]), settings)


def test_the_three_exits_are_listed_as_exit_rules(tmp_path: Path) -> None:
    ids = {d.id for d in list_components("exit_rule")}
    assert {"stop_loss", "take_profit", "trailing_stop", "holding_period"} <= ids
    defaults = {
        i: get_component(i).param("pct").default
        for i in ("stop_loss", "take_profit", "trailing_stop")
    }
    assert defaults == {"stop_loss": 0.08, "take_profit": 0.15, "trailing_stop": 0.10}
    app = create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no-web")
    with TestClient(app) as client:
        comps = {c["id"]: c for c in client.get("/api/components").json()}
    for i in ("stop_loss", "take_profit", "trailing_stop"):
        assert comps[i]["uses"] == ["exit_rule"]
        assert comps[i]["version"] == "1.0.0"


def test_trailing_stop_paper_matches_the_historical_run_and_replays(tmp_path: Path) -> None:
    # The TRAIL prices one session later, so the paper account (history up to day 1)
    # starts flat: day 2 close 10 enters (qty 50), day 3 open 10 fills (cash 500), peak
    # rises to 13 on day 7, day 8 close 11.7 <= 11.7 exits, day 9 open 11.6 sells
    # (cash 1080.00).
    df = bars({"XA": [(9.0, 9.0), *TRAIL]})
    lab = Lab.open(tmp_path / "home")
    snap = lab.import_frame(df, source="test")
    run_id = lab.run(strategy([ExitRule(kind="trailing_stop")]), snap.id)
    hist = lab.store.ledgers(run_id)

    pid = lab.start_paper(run_id, day(1))
    for i in range(2, len(TRAIL) + 1):
        ev = df[df["date"] == pd.Timestamp(day(i))].copy()
        ev["date"] = ev["date"].dt.strftime("%Y-%m-%d")
        assert lab.paper_ingest(pid, ev.reset_index(drop=True))["status"] == "accepted"
    paper = lab.paper_ledgers(pid)

    after_start = [d for d in decisions(hist) if d[0] > day(1)]
    assert decisions(paper) == after_start
    assert decisions(paper, *TRADING) == [
        (day(2), "XA", "enter", "all entry conditions passed"),
        (day(8), "XA", "exit", "trailing_stop: close 11.7 <= 11.7 (10% below peak close 13)"),
    ]
    assert (
        fills(paper)
        == fills(hist)
        == [
            (day(3), "XA", "buy", 50, 10.0, 0.0, 500.0),
            (day(9), "XA", "sell", 50, 11.6, 0.0, 1080.0),
        ]
    )
    rep = lab.paper_replay(pid)
    assert rep["match"], rep
    assert rep["sessions"] == len(TRAIL) - 1 and rep["fills"] == 2
