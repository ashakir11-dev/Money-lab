"""Engine/0.3.0 plug-in points: lists of exits, indicator-vs-indicator conditions,
ranking of entry candidates, registered sizing, and compatibility with 0.2.0 runs.

Expected decisions and cash are worked out by hand next to each assertion. Prices are
chosen so every number is exact; costs are zero unless stated.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from fixtures_util import bars, day, decisions, fills, level_strategy, run
from moneylab.data.snapshot import MarketData
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    RankBy,
    SizingRule,
    StrategyConfig,
    VersionMismatch,
    check_versions,
    config_from_resolved,
    resolve,
)
from moneylab.engine.simulator import Account, simulate
from moneylab.settings import Settings

ENTER = ("enter", "exit", "skip")


def sma(n: int) -> IndicatorRef:
    return IndicatorRef(id="sma", params={"length": n})


def close_below(x: float) -> Condition:
    return Condition(kind="threshold", indicator=sma(1), params={"op": "below", "value": x})


def strategy(entry: list[Condition], **kw: Any) -> StrategyConfig:
    port = kw.pop("portfolio", {})
    return StrategyConfig(
        name="fixture",
        universe=kw.pop("universe", ["XA"]),
        benchmark="XA",
        timeframe="1d",
        entry=entry,
        portfolio=PortfolioSettings(
            starting_cash=1000, target_allocation=0.5, cost_bps_per_side=0, **port
        ),
        **kw,
    )


RISE = [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (10.0, 13.0), (13.0, 13.0), (13.0, 13.0)]


def test_indicator_compare_close_above_its_three_day_average() -> None:
    # SMA(1) is the close. SMA(3): day 2 = 10 (10 > 10 fails), day 3 = (10+10+13)/3 = 11
    # (13 > 11 passes), day 4 = 12 (13 > 12 passes again: already held), day 5 = 13
    # (13 > 13 fails). Entry on day 3, filled at the day 4 open of 13:
    # qty = floor(0.5 * 1000 / 13) = 38, cash 1000 - 494 = 506. Held 2 sessions at the
    # day 5 close -> exit (no later session to fill it).
    cond = Condition(
        kind="indicator_compare", indicator=sma(1), other=sma(3), params={"op": "above"}
    )
    r = run(bars({"XA": RISE}), strategy([cond], exit=ExitRule(params={"sessions": 2})))
    assert decisions(r, *ENTER) == [
        (day(3), "XA", "enter", "all entry conditions passed"),
        (day(4), "XA", "skip", "already_held"),
        (day(5), "XA", "exit", "held 2 sessions >= 2"),
    ]
    assert fills(r) == [(day(4), "XA", "buy", 38, 13.0, 0.0, 506.0)]
    row = r.decisions[r.decisions["action"] == "enter"].iloc[0]
    assert row["values"]["sma(length=3)"] == 11.0
    assert row["conditions"][0]["condition"] == "sma(length=1) above sma(length=3)"
    assert row["conditions"][0]["other_observed"] == 11.0


def test_indicator_cross_fires_only_on_the_crossing_session() -> None:
    # Day 3: previous SMA(1) 10 <= previous SMA(3) 10 and 13 > 11 -> cross above.
    # Day 4: previous 13 > previous 11, so it is not a new cross: nothing is recorded.
    cond = Condition(
        kind="indicator_cross", indicator=sma(1), other=sma(3), params={"direction": "above"}
    )
    r = run(bars({"XA": RISE}), strategy([cond], exit=ExitRule(params={"sessions": 2})))
    assert decisions(r, *ENTER) == [
        (day(3), "XA", "enter", "all entry conditions passed"),
        (day(5), "XA", "exit", "held 2 sessions >= 2"),
    ]


def test_indicator_pairs_must_share_units() -> None:
    rsi = IndicatorRef(id="rsi", params={"length": 14})
    bad = Condition(kind="indicator_compare", indicator=sma(20), other=rsi, params={"op": "above"})
    with pytest.raises(ValueError, match="not in the same units"):
        resolve(strategy([bad]), Settings(_env_file=None))
    # The same indicator with different settings is fine (RSI 7 above RSI 28).
    ok = Condition(
        kind="indicator_compare",
        indicator=IndicatorRef(id="rsi", params={"length": 7}),
        other=IndicatorRef(id="rsi", params={"length": 28}),
    )
    resolve(strategy([ok]), Settings(_env_file=None))
    with pytest.raises(ValidationError, match="needs a second indicator"):
        Condition(kind="indicator_cross", indicator=sma(1))
    with pytest.raises(ValidationError, match="does not take a second indicator"):
        Condition(kind="threshold", indicator=sma(1), other=sma(2))


# Enter at the day 1 close of 11 (qty floor(500 / 11) = 45), filled at the day 2 open of
# 11 (cash 1000 - 495 = 505). At the day 3 close (10) both exits fire: the signal
# (close < 10.5) and the holding period (held 2 sessions: day 2 and day 3).
DIP = [(10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (11.0, 10.0), (10.0, 10.0)]
ABOVE = Condition(kind="threshold", indicator=sma(1), params={"op": "above", "value": 10.5})


@pytest.mark.parametrize(
    ("first", "reason"),
    [("signal", "exit signal"), ("holding_period", "held 2 sessions >= 2")],
)
def test_first_exit_in_the_list_that_fires_wins(first: str, reason: str) -> None:
    signal = ExitRule(kind="signal", conditions=[close_below(10.5)])
    hold = ExitRule(kind="holding_period", params={"sessions": 2})
    exits = [signal, hold] if first == "signal" else [hold, signal]
    r = run(bars({"XA": DIP}), strategy([ABOVE], exits=exits))
    assert decisions(r, *ENTER) == [
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(2), "XA", "skip", "already_held"),
        (day(3), "XA", "exit", reason),
    ]
    # Sold 45 at the day 4 open of 10: 505 + 450 = 955.
    assert fills(r) == [
        (day(2), "XA", "buy", 45, 11.0, 0.0, 505.0),
        (day(4), "XA", "sell", 45, 10.0, 0.0, 955.0),
    ]


def test_an_exit_list_with_one_rule_matches_the_single_exit() -> None:
    df = bars({"XA": DIP})
    hold = ExitRule(kind="holding_period", params={"sessions": 2})
    a = run(df, strategy([ABOVE], exit=hold))
    b = run(df, strategy([ABOVE], exits=[hold]))
    assert a.decisions.equals(b.decisions) and a.fills.equals(b.fills)


FAN = {
    "XA": [(10.0, 10.0), (10.0, 11.0), (11.0, 11.0)],
    "XB": [(10.0, 10.0), (10.0, 13.0), (13.0, 13.0)],
    "XC": [(10.0, 10.0), (10.0, 12.0), (12.0, 12.0)],
}


@pytest.mark.parametrize(
    ("rank", "order"),
    [
        (None, ["XA", "XB", "XC"]),  # default: ascending symbol order
        (RankBy(indicator=sma(1), order="descending"), ["XB", "XC", "XA"]),  # 13, 12, 11
        (RankBy(indicator=sma(1), order="ascending"), ["XA", "XC", "XB"]),  # 11, 12, 13
    ],
)
def test_rank_by_decides_who_takes_the_only_slot(rank: RankBy | None, order: list[str]) -> None:
    # All three close above 10.5 on day 1; one position slot. The first in processing
    # order enters, the other two are skipped for max_positions, in that order.
    cfg = strategy([ABOVE], universe=list(FAN), rank_by=rank, portfolio={"max_positions": 1})
    r = run(bars(FAN), cfg)
    day1 = [x for x in decisions(r, *ENTER) if x[0] == day(1)]
    assert [x[1] for x in day1] == order
    assert [x[2:] for x in day1] == [
        ("enter", "all entry conditions passed"),
        ("skip", "max_positions"),
        ("skip", "max_positions"),
    ]


def test_sizing_and_rank_validate() -> None:
    with pytest.raises(ValueError, match="not a sizing rule"):
        resolve(strategy([ABOVE], sizing=SizingRule(id="sma")), Settings(_env_file=None))
    with pytest.raises(ValidationError, match="unknown exit rule"):
        ExitRule(kind="nope")
    with pytest.raises(ValidationError, match="not an exit rule"):
        ExitRule(kind="sma")
    with pytest.raises(ValidationError, match="at least one exit rule"):
        strategy([ABOVE], exits=[])


def test_resolved_settings_round_trip_with_the_new_options() -> None:
    cfg = strategy(
        [Condition(kind="indicator_cross", indicator=sma(5), other=sma(20))],
        exits=[
            ExitRule(kind="signal", conditions=[close_below(9)]),
            ExitRule(kind="holding_period", params={"sessions": 7}),
        ],
        rank_by=RankBy(indicator=IndicatorRef(id="rsi"), order="ascending"),
    )
    resolved = resolve(cfg, Settings(_env_file=None))
    assert resolved["exit"] == resolved["exits"][0]
    assert resolved["sizing"] == {"id": "percent_of_equity", "version": "1.0.0", "params": {}}
    again = resolve(config_from_resolved(resolved), Settings(_env_file=None))
    assert again == resolved


def _as_v020(resolved: dict[str, Any]) -> dict[str, Any]:
    """The shape engine/0.2.0 stored: one exit, no sizing or ranking keys."""
    old = copy.deepcopy(resolved)
    for k in ("exits", "sizing", "rank_by"):
        old.pop(k)
    old["execution"]["engine_version"] = "engine/0.2.0"
    return old


def test_runs_saved_by_engine_020_still_run_and_duplicate() -> None:
    cfg = level_strategy(["XA"], 10.5, hold=2, starting_cash=1000, target_allocation=0.5)
    resolved = resolve(cfg, Settings(_env_file=None))
    old = _as_v020(resolved)
    check_versions(old)  # compatible: no VersionMismatch
    df = bars({"XA": DIP})
    data = MarketData.build(df, ["XA"], day(0), day(len(DIP) - 1))
    a, b = simulate(resolved, data), simulate(old, data)
    assert a.decisions.equals(b.decisions) and a.fills.equals(b.fills)
    unknown = {**old, "execution": {**old["execution"], "engine_version": "engine/0.1.0"}}
    with pytest.raises(VersionMismatch, match=r"engine engine/0\.1\.0"):
        check_versions(unknown)


def test_account_state_from_before_peak_tracking_still_loads() -> None:
    resolved = resolve(
        level_strategy(["XA"], 10.5, hold=2, starting_cash=1000, target_allocation=0.5),
        Settings(_env_file=None),
    )
    acct = Account(resolved)
    state = acct.state()
    state["positions"] = {"XA": {"qty": 45, "fill_idx": 2, "entry_price": 11.0}}
    restored = Account.from_state(resolved, state)
    assert restored.positions["XA"].qty == 45
