"""Idle cash held in the benchmark (engine/0.4.0), hand-calculated.

All scenarios: universe XA, benchmark BM (not in the universe, never a signal), entry
"XA closes above 15", exit after 2 sessions. BM is bought and sold in whole shares at the
open; those sweep fills are not trades and use no position slot.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from fixtures_util import SESSIONS, bars, day, decisions
from moneylab.data.snapshot import MarketData
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    config_from_resolved,
    resolve,
)
from moneylab.engine.simulator import Account, MarketView, RunResult, simulate
from moneylab.settings import Settings

BM = [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (12.0, 12.0)]
XA = [(10.0, 10.0), (10.0, 20.0), (20.0, 20.0), (20.0, 20.0), (20.0, 20.0), (20.0, 20.0)]


def config(idle: str | None = "benchmark", **portfolio: Any) -> StrategyConfig:
    return StrategyConfig(
        name="idle",
        universe=["XA"],
        benchmark="BM",
        timeframe="1d",
        entry=[
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="sma", params={"length": 1}),
                params={"op": "above", "value": 15},
            )
        ],
        exit=ExitRule(kind="holding_period", params={"sessions": 2}),
        portfolio=PortfolioSettings(
            **{
                "starting_cash": 1000,
                "target_allocation": 0.5,
                "max_positions": 1,
                "cost_bps_per_side": 0,
                "idle_cash": idle,
                **portfolio,
            }
        ),
    )


def setup(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[dict[str, Any], MarketData]:
    n = int(df["date"].nunique())
    data = MarketData.build(df, ["BM", "XA"], day(0), day(n - 1))
    return resolve(cfg, Settings(_env_file=None)), data


def go(spec: dict[str, Any], cfg: StrategyConfig) -> RunResult:
    resolved, data = setup(bars(spec), cfg)
    return simulate(resolved, data)


def rows(r: RunResult) -> list[tuple[Any, ...]]:
    cols = ["session", "symbol", "side", "qty", "price", "cost", "cash_after"]
    return [tuple(x) for x in r.fills[cols].itertuples(index=False)]


def test_sweep_buys_at_first_open_sells_for_entries_and_rebuys() -> None:
    r = go({"BM": BM, "XA": XA}, config())
    # Day 0 open: 1000 / 10 = 100 BM, cash 0. Day 1 close: equity 1000, XA 20 > 15,
    # qty floor(500 / 20) = 25, affordable through the 100 BM held (max_positions 1 is
    # not used up by the sweep). Day 2 open: the 25 XA need 500, so sell ceil(500 / 10) =
    # 50 BM at 10 and buy XA. Day 3 close: held 2 sessions -> exit; day 4 open sells XA
    # (cash 500) and buys floor(500 / 11) = 45 BM at 11 (cash 5). Day 4 close: equity
    # 5 + 95 x 11 = 1050, qty floor(525 / 20) = 26. Day 5 open: need 520, cash 5, sell
    # ceil(515 / 12) = 43 BM at 12 (cash 521), buy 26 XA (cash 1); 1 / 12 buys nothing.
    assert rows(r) == [
        (day(0), "BM", "buy", 100, 10.0, 0.0, 0.0),
        (day(2), "BM", "sell", 50, 10.0, 0.0, 500.0),
        (day(2), "XA", "buy", 25, 20.0, 0.0, 0.0),
        (day(4), "XA", "sell", 25, 20.0, 0.0, 500.0),
        (day(4), "BM", "buy", 45, 11.0, 0.0, 5.0),
        (day(5), "BM", "sell", 43, 12.0, 0.0, 521.0),
        (day(5), "XA", "buy", 26, 20.0, 0.0, 1.0),
    ]
    sweep = r.fills[r.fills["note"] == "sweep"]
    assert list(sweep["symbol"]) == ["BM"] * 4
    assert sweep["decision_id"].isna().all()
    assert r.fills[r.fills["note"] != "sweep"]["decision_id"].notna().all()
    eq = r.equity
    # positions_value counts only XA; the BM shares are in sweep_value.
    d2 = eq.iloc[2]
    assert (d2["cash"], d2["positions_value"], d2["sweep_value"], d2["equity"]) == (
        0.0,
        500.0,
        500.0,
        1000.0,
    )
    assert list(eq["positions"]) == [0, 0, 1, 1, 0, 1]
    last = eq.iloc[5]
    # Day 5 close: XA 26 x 20 = 520, BM (95 - 43) x 12 = 624, cash 1.
    assert (last["positions_value"], last["sweep_value"], last["equity"]) == (520.0, 624.0, 1145.0)
    assert decisions(r, "enter") == [
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(4), "XA", "enter", "all entry conditions passed"),
    ]


def test_default_is_cash_and_adds_nothing() -> None:
    for idle in (None, "cash"):
        r = go({"BM": BM, "XA": XA}, config(idle))
        assert "sweep_value" not in r.equity
        assert not (r.fills["note"] == "sweep").any()
        assert list(r.fills["symbol"]) == ["XA", "XA", "XA"]


def test_configured_cost_does_not_change_sweep_fills() -> None:
    # A stored 100 bps cost is not charged, so the sweep matches the zero-cost run.
    charged = go({"BM": BM, "XA": XA}, config(cost_bps_per_side=100))
    plain = go({"BM": BM, "XA": XA}, config())
    assert rows(charged) == rows(plain)
    assert charged.equity.iloc[0]["equity"] == 1000.0


def test_no_benchmark_bar_means_no_sweep_that_session() -> None:
    bm = [*BM[:2], None, *BM[3:]]
    r = go({"BM": bm, "XA": XA}, config())
    # The day-2 open has no BM bar, so nothing can be sold: the XA buy needs 500 and cash
    # is 0 -> skipped; no sweep fills that day.
    assert (day(2), "XA", "skip", "insufficient_cash_at_open") in decisions(r, "skip")
    assert day(2) not in set(r.fills["session"])
    assert rows(r)[0] == (day(0), "BM", "buy", 100, 10.0, 0.0, 0.0)


def test_state_round_trip_resumes_exactly() -> None:
    resolved, data = setup(bars({"BM": BM, "XA": XA}), config())
    view = MarketView.build(resolved, data)
    full = Account(resolved)
    for i in range(len(view.sessions)):
        full.step(view, i)
    a = Account(resolved)
    for i in range(3):
        a.step(view, i)
    assert a.sweep_qty == 50
    b = Account.from_state(resolved, json.loads(json.dumps(a.state())))
    assert b.sweep_qty == 50
    for i in range(3, len(view.sessions)):
        b.step(view, i)
    assert b.fills == full.fills
    assert b.equity_rows == full.equity_rows
    assert b.sweep_qty == full.sweep_qty == 52


def test_old_state_without_sweep_qty_loads_with_zero() -> None:
    resolved, _ = setup(bars({"BM": BM, "XA": XA}), config(None))
    st = Account(resolved).state()
    del st["sweep_qty"]
    assert Account.from_state(resolved, st).sweep_qty == 0


def test_resolved_setting_and_old_resolved_dicts() -> None:
    on, _ = setup(bars({"BM": BM, "XA": XA}), config())
    off, _ = setup(bars({"BM": BM, "XA": XA}), config(None))
    assert on["portfolio"]["idle_cash"] == "benchmark"
    assert "idle_cash" in on["execution"]
    assert off["portfolio"]["idle_cash"] == "cash"
    assert "idle_cash" not in off["execution"]
    assert config_from_resolved(on).portfolio.idle_cash == "benchmark"
    del off["portfolio"]["idle_cash"]  # a run saved before engine/0.4.0
    assert config_from_resolved(off).portfolio.idle_cash is None
    assert len(SESSIONS) > 5
