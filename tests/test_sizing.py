"""ATR indicator and the fixed_dollar / volatility sizing rules.

Every expected number is worked out by hand next to its assertion. Prices are chosen
so the arithmetic is exact in binary floating point; costs are zero.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fixtures_util import SESSIONS, bars, day, decisions, fills, run
from moneylab.api.app import create_app
from moneylab.components import get_component
from moneylab.components.registry import SizingContext
from moneylab.data.snapshot import normalise
from moneylab.demo import FIXTURE, UNIVERSE, rsi_strategy
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    SizingRule,
    StrategyConfig,
    check_versions,
    config_from_resolved,
    required_indicators,
    resolve,
)
from moneylab.service import Lab
from moneylab.settings import Settings
from test_api import _snapshot
from test_paper import BARS, END, START, feed, plain, reference, rows, sessions_between

NAN = math.nan
OHLC = tuple[float, float, float, float]


def atr_values(hlc: list[tuple[float, float, float] | None], length: int) -> list[float]:
    """ATR over (high, low, close) rows; None is a session with no bar."""
    df = pd.DataFrame(
        [r if r is not None else (NAN, NAN, NAN) for r in hlc],
        columns=["high", "low", "close"],
    )
    out = get_component("atr").compute(df, {"length": length})  # type: ignore[misc]
    return [float(v) for v in out]


def same(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all(
        (math.isnan(x) and math.isnan(y)) or x == y for x, y in zip(a, b, strict=True)
    )


# (high, low, close); session 5 has no bar.
HLC: list[tuple[float, float, float] | None] = [
    (11.0, 9.0, 10.0),  # 0: first bar of a run, TR = 11 - 9 = 2
    (12.0, 10.0, 11.0),  # 1: max(2, |12-10|=2, |10-10|=0) = 2
    (15.0, 10.0, 14.0),  # 2: max(5, |15-11|=4, |10-11|=1) = 5
    (20.0, 19.0, 19.5),  # 3: gap up, max(1, |20-14|=6, |19-14|=5) = 6
    (20.0, 16.0, 17.0),  # 4: max(4, |20-19.5|=0.5, |16-19.5|=3.5) = 4
    None,  # 5: no bar
    (10.0, 8.0, 9.0),  # 6: new run, TR = 10 - 8 = 2 (the close of 17 before the gap is unused)
    (11.0, 9.0, 10.0),  # 7: max(2, |11-9|=2, |9-9|=0) = 2
    (12.0, 7.0, 8.0),  # 8: max(5, |12-10|=2, |7-10|=3) = 5
    (3.0, 2.0, 2.5),  # 9: gap down, max(1, |3-8|=5, |2-8|=6) = 6
]


def test_atr_wilder_values_with_a_gap() -> None:
    # length 3. Run one (sessions 0-4): ATR[2] = (2 + 2 + 5) / 3 = 3;
    # ATR[3] = (3 x 2 + 6) / 3 = 4; ATR[4] = (4 x 2 + 4) / 3 = 4.
    # Session 5 has no bar: no value. Run two restarts the warm-up: sessions 6 and 7 have
    # no value, ATR[8] = (2 + 2 + 5) / 3 = 3, ATR[9] = (3 x 2 + 6) / 3 = 4.
    expected = [NAN, NAN, 3.0, 4.0, 4.0, NAN, NAN, NAN, 3.0, 4.0]
    assert same(atr_values(HLC, 3), expected)


def test_atr_length_one_is_the_true_range_and_values_are_causal() -> None:
    assert same(atr_values(HLC, 1), [2.0, 2.0, 5.0, 6.0, 4.0, NAN, 2.0, 2.0, 5.0, 6.0])
    full = atr_values(HLC, 3)
    for n in range(1, len(HLC) + 1):
        assert same(atr_values(HLC[:n], 3), full[:n])
    # A run shorter than `length` has no value at all.
    assert same(atr_values(HLC[:2], 3), [NAN, NAN])


def test_atr_declaration() -> None:
    d = get_component("atr")
    assert d.version == "1.0.0" and d.uses == ("indicator",)
    assert d.inputs == ("high", "low", "close") and d.price_scale is False
    p = d.param("length")
    assert (p.default, p.min, p.max) == (14, 1, 200)
    with pytest.raises(ValueError, match=">= 1"):
        d.resolve_params({"length": 0})


# Engine runs ----------------------------------------------------------------------


def ohlc_bars(spec: dict[str, list[OHLC]]) -> pd.DataFrame:
    """symbol -> per-session (open, high, low, close)."""
    rows = [
        {"date": SESSIONS[i], "symbol": s, "open": o, "high": h, "low": lo, "close": c}
        | {"volume": 1000}
        for s, days in spec.items()
        for i, (o, h, lo, c) in enumerate(days)
    ]
    return normalise(pd.DataFrame(rows))


ALWAYS = Condition(
    kind="threshold",
    indicator=IndicatorRef(id="sma", params={"length": 1}),
    params={"op": "above", "value": 0},
)


def strategy(universe: list[str], sizing: SizingRule, cash: float) -> StrategyConfig:
    return StrategyConfig(
        name="sizing fixture",
        universe=universe,
        benchmark=universe[0],
        entry=[ALWAYS],
        exit=ExitRule(kind="holding_period", params={"sessions": 50}),
        portfolio=PortfolioSettings(
            starting_cash=cash, target_allocation=0.5, max_positions=5, cost_bps_per_side=0
        ),
        sizing=sizing,
    )


def test_fixed_dollar_quantities_and_cash() -> None:
    # Starting cash 1,000, amount 300 (percent_of_equity would use 0.5 x 1,000 = 500).
    # Day 0 close: XA floor(300 / 10) = 30 (est 300); XB floor(300 / 7) = 42 (est 294).
    # Day 1 open fills: XA 30 x 10 = 300 -> cash 700; XB 42 x 7 = 294 -> cash 406.
    df = bars({"XA": [(10.0, 10.0)] * 3, "XB": [(7.0, 7.0)] * 3})
    cfg = strategy(["XA", "XB"], SizingRule(id="fixed_dollar", params={"amount": 300}), 1000)
    r = run(df, cfg)
    assert fills(r) == [
        (day(1), "XA", "buy", 30, 10.0, 0.0, 700.0),
        (day(1), "XB", "buy", 42, 7.0, 0.0, 406.0),
    ]
    assert decisions(r, "enter") == [
        (day(0), "XA", "enter", "all entry conditions passed"),
        (day(0), "XB", "enter", "all entry conditions passed"),
    ]


def test_fixed_dollar_amount_above_cash_is_skipped_not_shrunk() -> None:
    # amount 2,000 on 1,000 cash: floor(2,000 / 10) = 200 shares, est 2,000 > 1,000.
    df = bars({"XA": [(10.0, 10.0)] * 2})
    r = run(df, strategy(["XA"], SizingRule(id="fixed_dollar", params={"amount": 2000}), 1000))
    assert fills(r) == []
    assert decisions(r, "skip")[0] == (day(0), "XA", "skip", "insufficient_cash")


# ATR(2) is first defined at session 1, so session 0 entries get zero_quantity.
VOL = {
    # TR 2 every session -> ATR(2) = 2 from session 1.
    "XA": [(50.0, 51.0, 49.0, 50.0)] * 3,
    # TR 1 every session -> ATR(2) = 1.
    "XB": [(20.0, 20.5, 19.5, 20.0)] * 3,
    # TR 0.25 every session -> ATR(2) = 0.25.
    "XC": [(100.0, 100.125, 99.875, 100.0)] * 3,
}


def test_volatility_sizing_by_atr_with_allocation_cap_and_warmup() -> None:
    # Starting cash 100,000, target allocation 0.5, risk_fraction 0.01, atr_multiple 2.
    # Session 0: ATR(2) not yet defined -> qty 0 -> skip zero_quantity for all three.
    # Session 1 (equity 100,000; risk budget 0.01 x 100,000 = 1,000;
    #            cap = floor(0.5 x 100,000 / close)):
    #   XA: floor(1,000 / (2 x 2)) = 250; cap floor(50,000 / 50) = 1,000 -> 250 (est 12,500)
    #   XB: floor(1,000 / (2 x 1)) = 500; cap floor(50,000 / 20) = 2,500 -> 500 (est 10,000)
    #   XC: floor(1,000 / (2 x 0.25)) = 2,000; cap floor(50,000 / 100) = 500 -> 500, the
    #       cap binds (est 50,000).
    # Session 2 open fills: cash 100,000 - 12,500 = 87,500; - 10,000 = 77,500;
    #   - 50,000 = 27,500.
    sizing = SizingRule(
        id="volatility", params={"risk_fraction": 0.01, "atr_length": 2, "atr_multiple": 2}
    )
    r = run(ohlc_bars(VOL), strategy(["XA", "XB", "XC"], sizing, 100_000))
    assert decisions(r, "enter", "skip") == [
        (day(0), "XA", "skip", "zero_quantity"),
        (day(0), "XB", "skip", "zero_quantity"),
        (day(0), "XC", "skip", "zero_quantity"),
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(1), "XB", "enter", "all entry conditions passed"),
        (day(1), "XC", "enter", "all entry conditions passed"),
        (day(2), "XA", "skip", "already_held"),
        (day(2), "XB", "skip", "already_held"),
        (day(2), "XC", "skip", "already_held"),
    ]
    assert fills(r) == [
        (day(2), "XA", "buy", 250, 50.0, 0.0, 87_500.0),
        (day(2), "XB", "buy", 500, 20.0, 0.0, 77_500.0),
        (day(2), "XC", "buy", 500, 100.0, 0.0, 27_500.0),
    ]


def test_volatility_rule_directly() -> None:
    fn = get_component("volatility").size_fn
    assert fn is not None
    p = {"risk_fraction": 0.01, "atr_length": 14, "atr_multiple": 2.0}

    def qty(atr: float, close: float = 50.0) -> int:
        ctx = SizingContext("XA", close, 100_000.0, 0.5, {"atr(length=14)": atr})
        return fn(ctx, p)

    assert qty(2.5) == 200  # floor(1,000 / 5) = 200; cap floor(50,000 / 50) = 1,000
    assert qty(0.25) == 1000  # floor(1,000 / 0.5) = 2,000, capped at 1,000
    assert qty(NAN) == 0 and qty(0.0) == 0  # warm-up, or no range at all
    assert qty(600.0) == 0  # floor(1,000 / 1,200) = 0


# Validation and resolved settings -------------------------------------------------


def _resolve(sizing: SizingRule) -> dict[str, Any]:
    return resolve(strategy(["XA"], sizing, 1000), Settings(_env_file=None))


@pytest.mark.parametrize(
    ("sizing", "message"),
    [
        (SizingRule(id="volatility", params={"risk_fraction": 0.5}), "risk_fraction must be <="),
        (SizingRule(id="volatility", params={"risk_fraction": 0}), "risk_fraction must be >="),
        (SizingRule(id="volatility", params={"atr_multiple": 0.05}), "atr_multiple must be >="),
        (SizingRule(id="volatility", params={"atr_multiple": 25}), "atr_multiple must be <="),
        (SizingRule(id="volatility", params={"atr_length": 0}), "atr_length must be >="),
        (SizingRule(id="volatility", params={"atr_length": 2.5}), "whole number"),
        (SizingRule(id="volatility", params={"stop": 1}), "unknown parameter"),
        (SizingRule(id="fixed_dollar", params={"amount": 0}), "amount must be >="),
        (SizingRule(id="fixed_dollar", params={"amount": "lots"}), "amount must be a number"),
        (SizingRule(id="atr"), "not a sizing rule"),
    ],
)
def test_bad_sizing_rejected_at_resolve(sizing: SizingRule, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _resolve(sizing)


VOLATILITY_RESOLVED = {
    "id": "volatility",
    "version": "1.0.0",
    "params": {"risk_fraction": 0.01, "atr_length": 14, "atr_multiple": 2.0},
    "requires": [{"id": "atr", "version": "1.0.0", "params": {"length": 14}}],
}


def test_resolved_settings_include_the_atr_requirement() -> None:
    resolved = _resolve(SizingRule(id="volatility"))
    assert resolved["sizing"] == VOLATILITY_RESOLVED
    assert {"id": "atr", "version": "1.0.0", "params": {"length": 14}} in required_indicators(
        resolved
    )
    check_versions(resolved)
    assert config_from_resolved(resolved).sizing == SizingRule(
        id="volatility", params=VOLATILITY_RESOLVED["params"]
    )
    fixed = _resolve(SizingRule(id="fixed_dollar"))["sizing"]
    assert fixed == {"id": "fixed_dollar", "version": "1.0.0", "params": {"amount": 10000.0}}


# API and paper --------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path):  # type: ignore[no-untyped-def]
    app = create_app(Lab.open(tmp_path / "home"), web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        yield c


def test_volatility_sizing_through_the_api(client: TestClient) -> None:
    comps = {c["id"]: c for c in client.get("/api/components").json()}
    assert comps["volatility"]["uses"] == ["sizing"]
    assert comps["fixed_dollar"]["uses"] == ["sizing"]
    assert comps["atr"]["uses"] == ["indicator"]

    sid = _snapshot(client)
    cfg = rsi_strategy(35).model_dump(mode="json")
    cfg["sizing"] = {"id": "volatility", "params": {}}
    v = client.post("/api/validate", json={"config": cfg, "snapshot_id": sid})
    assert v.status_code == 200, v.text
    assert v.json()["resolved"]["sizing"] == VOLATILITY_RESOLVED

    cfg["sizing"] = {"id": "volatility", "params": {"risk_fraction": 0.9}}
    bad = client.post("/api/validate", json={"config": cfg, "snapshot_id": sid})
    assert bad.status_code in (400, 422) and "risk_fraction" in bad.text


def test_volatility_strategy_paper_trades_like_the_historical_engine(tmp_path: Path) -> None:
    lab = Lab.open(tmp_path / "home")
    snap = lab.import_file(FIXTURE)
    sizing = SizingRule(id="volatility", params={"atr_length": 10, "atr_multiple": 10})
    cfg = rsi_strategy(35, name="RSI < 35, volatility sized").model_copy(update={"sizing": sizing})
    assert cfg.universe == UNIVERSE
    run_id = lab.run(cfg, snap.id)
    pid = lab.start_paper(run_id, START, name="volatility paper")
    results = feed(lab, pid, sessions_between(START, END))
    assert all(r["status"] == "accepted" for r in results)

    ref = reference(lab, run_id, START, END)
    paper_fills = rows(lab, pid, "fills")
    assert rows(lab, pid, "decisions") == plain(ref.decisions)
    assert paper_fills == plain(ref.fills)
    assert rows(lab, pid, "equity") == plain(ref.equity_rows)
    assert lab.paper_replay(pid)["match"] is True

    # Hand check (fresh account 100,000.00, target 10%, 5 bps, 10-session hold).
    # 2023-04-04 close 74.56: AAA enters (same signal as test_hand_checked_paper_fills).
    #   ATR(10) of AAA there is 1.3363 (it is causal: the paper account sees the same bars).
    #   qty = floor(0.01 x 100,000 / (10 x 1.3363)) = floor(74.83) = 74; the cap
    #   floor(0.10 x 100,000 / 74.56) = 134 does not bind (percent of equity would buy 134).
    # 2023-04-05 open 74.91: gross 74 x 74.91 = 5,543.34; cost 2.77167 -> 2.77;
    #   cash 100,000.00 - 5,543.34 - 2.77 = 94,453.89.
    # 2023-04-20 open 76.82: gross 74 x 76.82 = 5,684.68; cost 2.84234 -> 2.84;
    #   cash 94,453.89 + 5,684.68 - 2.84 = 100,135.73.
    bars_aaa = BARS[BARS["symbol"] == "AAA"].set_index("date").sort_index()
    atr10 = get_component("atr").compute(bars_aaa, {"length": 10})  # type: ignore[misc]
    assert round(float(atr10[pd.Timestamp("2023-04-04")]), 4) == 1.3363
    got = [
        (f["session"], f["side"], f["qty"], f["price"], f["cost"], f["cash_after"])
        for f in paper_fills
    ]
    assert got == [
        ("2023-04-05", "buy", 74, 74.91, 2.77, 94_453.89),
        ("2023-04-20", "sell", 74, 76.82, 2.84, 100_135.73),
    ]
