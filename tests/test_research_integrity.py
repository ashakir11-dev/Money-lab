"""Phase-1 research-loop remediation. T08 is a later phase.

T01 replays the reported decision, not the Equibles price path. The historical kill
stays on ``judge_seed``. New decisions use intervals and do not rewrite that object.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from fixtures_util import SESSIONS, bars, day, decisions, fills, run
from moneylab.data.actions import adjust
from moneylab.decision.estimands import audit_active_excess, exposure_matched
from moneylab.decision.integrity import (
    DEFERRED,
    RESEARCH_COST_BPS,
    ApprovedApproximation,
    EvidenceFloor,
    ResearchBranch,
    authorize_one_axis,
    bind_cost,
    decide_research,
    example_research_card,
    legacy_recovery_configs,
    moving_block_interval,
    proposer_status,
    recovery_swing_partitions,
    redact_for_proposer,
    registry_capabilities,
    validate_card,
)
from moneylab.decision.measure import changed_axes, measurement_from_equity
from moneylab.decision.rules import (
    SERIES_ACTIVE,
    SERIES_TERMINAL,
    Measurement,
    OwnerCard,
    judge_seed,
)
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    resolve,
)
from moneylab.settings import Settings

LEVEL = {"lookback": 50, "min_move": 2.0, "zone": 1.0, "atr_length": 14}


def _settings() -> Settings:
    return Settings(_env_file=None)


def _floor(**overrides: object) -> EvidenceFloor:
    base: dict[str, object] = {
        "delta_pp": 0.0,
        "min_events": 30,
        "min_folds": 3,
        "confidence": 0.95,
        "block_length": 2,
        "horizon_bars": 1,
        "timeframe": "1d",
    }
    base.update(overrides)
    return EvidenceFloor(**base)  # type: ignore[arg-type]


def _legacy_owner() -> OwnerCard:
    return OwnerCard(
        series=SERIES_ACTIVE,
        objective_op="gt",
        objective_threshold=0.0,
        falsifier_op="lte",
        falsifier_threshold=0.0,
        paired_op="gt",
        paired_threshold=0.0,
        uncertainty_low_above=0.0,
        max_drawdown=1.0,
        max_concentration=1.0,
        min_distinct_dates=1,
        min_folds=1,
        family_budget=3,
        discovery_budget=3,
        universe_id="nvda-recovery",
        idle_cash="cash",
        holdout_start=date(2025, 1, 1),
        holdout_end=date(2025, 12, 31),
    )


def _sma(length: int = 1) -> IndicatorRef:
    return IndicatorRef(id="sma", params={"length": length})


def _threshold(indicator: IndicatorRef, op: str, value: float) -> Condition:
    return Condition(kind="threshold", indicator=indicator, params={"op": op, "value": value})


def _book(**portfolio: object) -> StrategyConfig:
    fields: dict[str, object] = {
        "starting_cash": 1000.0,
        "target_allocation": 0.4,
        "max_positions": 1,
        "cost_bps_per_side": 0.0,
    }
    fields.update(portfolio)
    return StrategyConfig(
        name="fixture",
        universe=["XA"],
        benchmark="XA",
        timeframe="1d",
        entry=[_threshold(_sma(), "above", 10.0)],
        exit=ExitRule(kind="holding_period", params={"sessions": 1}),
        portfolio=PortfolioSettings(**fields),  # type: ignore[arg-type]
    )


def test_t01_legacy_zero_cost_replay_keeps_the_reported_kill() -> None:
    assert _settings().cost_bps_per_side == 0.0
    configs = legacy_recovery_configs()
    v0 = resolve(configs["v0"], _settings())
    control = resolve(configs["control"], _settings())
    v1 = resolve(configs["v1"], _settings())
    assert v0["portfolio"]["cost_bps_per_side"] == 0.0
    assert v0["portfolio"]["cost_model"] == "legacy_unapplied"
    assert v0["timeframe"] == "1h"
    assert changed_axes(control, v0) == ["entry"]
    assert {"entry", "exit"} <= set(changed_axes(v0, v1))

    reported = -0.00346
    measurement = Measurement(
        values={SERIES_ACTIVE: reported, SERIES_TERMINAL: -21.45},
        drawdown=0.0799,
        distinct_dates=996,
        folds=1,
        scope="development",
    )
    legacy = judge_seed(_legacy_owner(), measurement)
    assert legacy.outcome == "kill"
    assert legacy.reasons == ["falsifier"]
    fresh = decide_research(
        contract_ok=True,
        scope="family",
        point_pp=reported,
        interval_low_pp=None,
        interval_high_pp=None,
        delta_pp=0.0,
        events=996,
        folds=1,
        floor=_floor(min_events=1, min_folds=1),
    )
    assert fresh.state == "PARK"
    assert fresh.reasons == ("interval_absent",)
    assert legacy.outcome == "kill"
    assert legacy.reasons == ["falsifier"]


def test_t02_capabilities_crossing_inclusive_cooldown_and_frozen_snapshot() -> None:
    caps = registry_capabilities()
    assert caps["numeric_threshold_cross"] is True
    assert caps["default_cost_bps_per_side"] == 0.0
    assert "R09 event-level evidence" in DEFERRED

    bare = example_research_card()
    claimed = replace(
        bare,
        family="momentum",
        claims=("numeric_cross", "cooldown"),
        treatment=bare.treatment.model_copy(
            update={"entry": [_threshold(_sma(), "above", 40.0)]}
        ),
        control=bare.control.model_copy(
            update={"entry": [_threshold(_sma(), "above", 50.0)]}
        ),
    )
    # The example book is a bounded bounce. Momentum with a strict threshold does not
    # express a cross or a cooldown, so both claims are rejected.
    rejected = validate_card(claimed)
    assert rejected.ok is False
    assert "unsupported_numeric_cross" in rejected.reasons
    assert "unsupported_cooldown" in rejected.reasons

    approved = replace(
        claimed,
        approximations=(
            ApprovedApproximation(
                "numeric_cross",
                "approx/1",
                "RSI above 40 stands in for a cross above 40.",
            ),
            ApprovedApproximation(
                "cooldown",
                "approx/1",
                "No cooldown; a sale may re-enter next bar.",
            ),
        ),
    )
    visible = validate_card(approved)
    assert visible.ok is True
    assert {item.capability for item in visible.approximations} == {"cooldown", "numeric_cross"}

    stopped = replace(bare, family="momentum", claims=("frozen_stop",))
    assert "unsupported_frozen_stop" in validate_card(stopped).reasons

    flat = [(10.0, 10.0), (10.0, 10.0), (10.0, 12.0), (12.0, 12.0), (13.0, 13.0)]
    cross = _book()
    cross = cross.model_copy(
        update={
            "entry": [_threshold(_sma(), "crosses_above", 11.0)],
            "portfolio": PortfolioSettings(
                starting_cash=1000, target_allocation=0.4, max_positions=1, cost_bps_per_side=0
            ),
        }
    )
    crossed = run(bars({"XA": flat}), cross)
    assert [row[2] for row in decisions(crossed, "enter")] == ["enter"]
    assert decisions(crossed, "enter")[0][0] == day(2)

    short = [(10.0, 10.0)] * 5
    rsi_cross = cross.model_copy(
        update={
            "entry": [
                Condition(
                    kind="threshold",
                    indicator=IndicatorRef(id="rsi", params={"length": 14}),
                    params={"op": "crosses_above", "value": 40},
                )
            ]
        }
    )
    assert decisions(run(bars({"XA": short}), rsi_cross), "enter") == []

    inclusive_bars = [(9.0, 9.0), (10.0, 10.0), (11.0, 11.0)]
    at_level = _book().model_copy(
        update={"entry": [_threshold(_sma(), "at_or_above", 10.0)]}
    )
    strict = _book().model_copy(update={"entry": [_threshold(_sma(), "above", 10.0)]})
    assert decisions(run(bars({"XA": inclusive_bars}), at_level), "enter")[0][0] == day(1)
    assert decisions(run(bars({"XA": inclusive_bars}), strict), "enter")[0][0] == day(2)

    held = [(12.0, 12.0)] * 6
    cooled = _book().model_copy(
        update={
            "portfolio": PortfolioSettings(
                starting_cash=1000,
                target_allocation=0.4,
                max_positions=1,
                cost_bps_per_side=0,
                cooldown_bars=1,
            )
        }
    )
    open_gate = _book()
    cooled_days = [row[0] for row in decisions(run(bars({"XA": held}), cooled), "enter")]
    open_days = [row[0] for row in decisions(run(bars({"XA": held}), open_gate), "enter")]
    assert open_days[0] == day(0)
    assert open_days[1] == day(2)
    assert cooled_days[0] == day(0)
    assert cooled_days[1] == day(3)
    assert any(row[3] == "cooldown" for row in decisions(run(bars({"XA": held}), cooled), "skip"))

    frozen = _book().model_copy(
        update={
            "portfolio": PortfolioSettings(
                starting_cash=1000,
                target_allocation=0.4,
                max_positions=1,
                cost_bps_per_side=0,
                freeze_entry_levels=True,
            )
        }
    )
    path = [(12.0, 12.0), (15.0, 15.0), (18.0, 18.0)]
    bought = run(bars({"XA": path}), frozen)
    snapshot = bought.fills.loc[bought.fills["side"] == "buy", "frozen_levels"].iloc[0]
    assert snapshot["sma(length=1)"] == 12.0
    assert all(np.isfinite(value) for value in snapshot.values())


def test_t03_one_declared_axis() -> None:
    card = example_research_card()
    parent = resolve(card.treatment, _settings())
    both = card.treatment.model_copy(
        update={
            "entry": [_threshold(_sma(), "above", 0.0)],
            "exit": ExitRule(kind="holding_period", params={"sessions": 20}),
        }
    )
    rejected = authorize_one_axis(parent, resolve(both, _settings()), "entry")
    assert rejected.authorized is False
    assert rejected.spends_trial is False
    assert "multiple_axes" in rejected.reasons

    entry_only = card.treatment.model_copy(
        update={
            "entry": [
                *card.treatment.entry[:2],
                _threshold(
                    IndicatorRef(id="support_distance", params=LEVEL),
                    "below",
                    0.5,
                ),
            ]
        }
    )
    accepted = authorize_one_axis(parent, resolve(entry_only, _settings()), "entry")
    assert accepted.authorized is True
    assert accepted.axes == ("entry",)
    assert entry_only.portfolio == card.treatment.portfolio
    assert entry_only.exit == card.treatment.exit
    assert entry_only.universe == card.treatment.universe
    assert entry_only.timeframe == card.treatment.timeframe

    cash = card.treatment.model_copy(
        update={"portfolio": card.treatment.portfolio.model_copy(update={"idle_cash": "benchmark"})}
    )
    blocked = authorize_one_axis(parent, resolve(cash, _settings()), "execution")
    assert blocked.spends_trial is False
    assert "cash_policy_change" in blocked.reasons


def test_t04_research_costs_fills_gaps_calendar_splits_and_warmup() -> None:
    assert _settings().cost_bps_per_side == 0.0
    path = [(10.0, 10.0), (10.0, 12.0), (8.0, 8.0)]
    above_nine = [_threshold(_sma(), "above", 9.0)]
    legacy = _book(cost_bps_per_side=5).model_copy(update={"entry": above_nine})
    research = bind_cost(legacy, "research")
    assert research.portfolio.cost_bps_per_side == RESEARCH_COST_BPS
    assert research.portfolio.cost_model == "research_bps"
    free = fills(run(bars({"XA": path}), legacy))
    charged = fills(run(bars({"XA": path}), research))
    # 40 shares at the next open of 10. Gross 400. 5 bps is 0.20. Cash 599.80.
    # The sell fills at the gapped open of 8, not the signal close of 12.
    # Gross 320, cost 0.16, cash 919.64.
    assert free[0][4:] == (10.0, 0.0, 600.0)
    assert charged[0] == (day(1), "XA", "buy", 40, 10.0, 0.2, 599.8)
    assert charged[1] == (day(2), "XA", "sell", 40, 8.0, 0.16, 919.64)
    assert free[1][4] == 8.0

    assert not any(session.weekday() >= 5 for session in SESSIONS)
    assert date(2024, 1, 15) not in {session.date() for session in SESSIONS}

    raw = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
            "symbol": ["NVDA", "NVDA"],
            "open": [100.0, 51.0],
            "high": [100.0, 51.0],
            "low": [100.0, 51.0],
            "close": [100.0, 50.0],
            "volume": [10, 20],
        }
    )
    actions = pd.DataFrame(
        {"date": ["2024-01-03"], "symbol": ["NVDA"], "type": ["split"], "value": [2.0]}
    )
    adjusted = adjust(raw, actions)
    assert adjusted.iloc[0]["close"] == 50.0
    assert int(adjusted.iloc[0]["volume"]) == 20
    assert adjusted.iloc[1]["close"] == 50.0

    history = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (10.0, 10.0)]
    warmup = _book().model_copy(
        update={
            "entry": [_threshold(_sma(3), "crosses_above", 4.0)],
            "trade_start": date.fromisoformat(day(3)),
        }
    )
    traded = decisions(run(bars({"XA": history}), warmup), "enter")
    assert traded == [(day(3), "XA", "enter", "all entry conditions passed")]
    one_bar = _book().model_copy(
        update={"entry": [_threshold(_sma(3), "crosses_above", 4.0)]}
    )
    assert decisions(run(bars({"XA": [history[-1]]}), one_bar), "enter") == []


def _equity(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "session": [f"2024-01-0{i + 2}" for i in range(len(rows))],
            "equity": [row[0] for row in rows],
            "benchmark_equity": [row[1] for row in rows],
            "positions_value": [row[2] for row in rows],
        }
    )


def test_t05_estimands_reconcile_and_zero_exposure_is_undefined() -> None:
    frame = _equity([(100.0, 100.0, 0.0), (100.0, 100.0, 50.0), (110.0, 121.0, 0.0)])
    empty = pd.DataFrame()
    audit = audit_active_excess(frame, empty)
    legacy = measurement_from_equity(frame, empty)
    exposure = (frame["positions_value"] / frame["equity"]).to_numpy()
    strat = np.diff(frame["equity"].to_numpy()) / frame["equity"].to_numpy()[:-1]
    bench_equity = frame["benchmark_equity"].to_numpy()
    bench = np.diff(bench_equity) / bench_equity[:-1]
    invested = exposure[:-1] > 0
    hand = float((strat - bench)[invested].mean() * 100.0)
    assert hand == pytest.approx(-11.0)
    assert audit["active_excess_pp"] == pytest.approx(hand)
    assert legacy.values[SERIES_ACTIVE] == pytest.approx(hand)
    assert audit["active_excess_pp_v2"] == pytest.approx(hand)
    assert audit["aggregation"] == "arithmetic_mean"

    idle = _equity([(100.0, 100.0, 0.0), (100.0, 110.0, 0.0)])
    quiet = audit_active_excess(idle, empty)
    assert measurement_from_equity(idle, empty).values[SERIES_ACTIVE] == 0.0
    assert quiet["active_excess_pp"] == 0.0
    assert quiet["active_excess_pp_v2"] is None
    assert quiet["v2_undefined_reason"] == "zero_exposure"

    matched = exposure_matched(
        np.array([[0.5], [0.0]]),
        np.array([[0.10], [0.10]]),
        np.array([0.04, 0.04]),
    )
    assert matched["same_stock_matched_return"] == pytest.approx(0.05)
    assert matched["spy_matched_return"] == pytest.approx(0.02)
    holdings = 100.0
    holdings += 50.0 * 0.10
    assert holdings / 100.0 - 1.0 == pytest.approx(matched["same_stock_matched_return"])


def test_t06_decision_states_and_immutable_legacy_kill() -> None:
    floor = _floor()
    wide = decide_research(
        contract_ok=True,
        scope="family",
        point_pp=-1.0,
        interval_low_pp=-5.0,
        interval_high_pp=4.0,
        delta_pp=0.0,
        events=80,
        folds=4,
        floor=floor,
    )
    assert wide.state == "PARK"
    assert wide.reasons == ("interval_covers_delta",)

    dropped = decide_research(
        contract_ok=True,
        scope="child",
        point_pp=-1.0,
        interval_low_pp=-2.0,
        interval_high_pp=-0.1,
        delta_pp=0.0,
        events=80,
        folds=4,
        floor=floor,
    )
    assert dropped.state == "DROP"
    killed = decide_research(
        contract_ok=True,
        scope="family",
        point_pp=-1.0,
        interval_low_pp=-2.0,
        interval_high_pp=-0.1,
        delta_pp=0.0,
        events=80,
        folds=4,
        floor=floor,
        falsifier_op="lte",
        falsifier_level=0.0,
    )
    assert killed.state == "KILL"

    invalid = decide_research(
        contract_ok=False,
        contract_reasons=("unbounded_support",),
        scope="family",
        point_pp=5.0,
        interval_low_pp=1.0,
        interval_high_pp=6.0,
        delta_pp=0.0,
        events=80,
        folds=4,
        floor=floor,
    )
    assert invalid.state == "INVALID"

    kept = decide_research(
        contract_ok=True,
        scope="child",
        point_pp=1.5,
        interval_low_pp=0.4,
        interval_high_pp=2.0,
        delta_pp=0.2,
        events=80,
        folds=4,
        floor=floor,
    )
    assert kept.state == "KEEP"

    card = example_research_card()
    branch = ResearchBranch(card)
    thin = branch.judge(
        scope="family",
        point_pp=1.5,
        interval_low_pp=0.4,
        interval_high_pp=2.0,
        events=2,
        folds=1,
    )
    assert thin.state == "PARK"
    frozen = branch.freeze_manifest()
    assert frozen.state == "PARK"
    assert frozen.reasons == ("cannot_freeze",)
    assert branch.gate.frozen_hash == ""

    ready = ResearchBranch(card)
    assert ready.judge(
        scope="child",
        point_pp=1.5,
        interval_low_pp=0.4,
        interval_high_pp=2.0,
        events=80,
        folds=4,
    ).state == "KEEP"
    assert ready.freeze_manifest().state == "FREEZE"
    digest = ready.gate.frozen_hash
    mutated = resolve(card.treatment, _settings())
    mutated["portfolio"]["starting_cash"] = 1.0
    with pytest.raises(ValueError, match="immutable"):
        ready.gate.freeze(mutated)
    assert ready.gate.frozen_hash == digest

    legacy = judge_seed(
        _legacy_owner(),
        Measurement(
            values={SERIES_ACTIVE: -0.00346, SERIES_TERMINAL: 0.0},
            distinct_dates=100,
            folds=1,
        ),
    )
    assert legacy.outcome == "kill"
    branch.record_legacy(legacy)
    assert branch.legacy is legacy
    assert legacy.outcome == "kill"


def test_t07_holdout_gate_contamination_and_proposer_redaction() -> None:
    card = example_research_card()
    branch = ResearchBranch(card)
    loaded: list[str] = []

    def _load() -> str:
        loaded.append("loaded")
        return "rows"

    denied = branch.gate.request("holdout", "score", loader=_load)
    assert denied["allowed"] is False
    assert denied["reason"] == "not_frozen"
    assert loaded == []

    branch.judge(
        scope="child",
        point_pp=1.5,
        interval_low_pp=0.4,
        interval_high_pp=2.0,
        events=80,
        folds=4,
    )
    branch.freeze_manifest()
    opened = branch.gate.request("holdout", "score", loader=_load)
    assert opened["allowed"] is True
    assert loaded == ["loaded"]
    assert branch.gate.partitions["holdout"].contaminated is True

    again = branch.gate.request("holdout", "preview", loader=_load)
    assert again["reason"] == "contaminated"
    assert loaded == ["loaded"]

    proposer = branch.gate.request("holdout", "proposer", loader=_load)
    assert proposer["reason"] == "proposer_holdout_blocked"
    assert loaded == ["loaded"]

    recovery = ResearchBranch(
        replace(card, partitions=recovery_swing_partitions(), timeframe="1h")
    )
    # The recovery card's recipes are daily in the example, so this copy is not executed.
    # The partition flag itself is what the gate reads.
    recovery.gate.frozen_hash = "frozen-for-the-flag"
    flagged = recovery.gate.request("holdout", "score", loader=_load)
    assert flagged["reason"] == "contaminated"
    assert loaded == ["loaded"]

    rows = [
        {"partition": "discovery", "label": "active_excess_pp", "value": -0.00346},
        {"partition": "holdout", "role": "holdout", "label": "2025", "value": 1.0},
    ]
    shown = redact_for_proposer(rows)
    assert [row["partition"] for row in shown] == ["discovery"]
    assert all("2025" not in str(row) for row in shown)


def test_t09_proposer_status_timeframe_seed_evidence_and_terminal() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        for name in ("OPENAI_API_KEY", "MONEYLAB_MODEL_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        status = proposer_status()
    finally:
        monkeypatch.undo()
    assert status == {"mode": "fallback", "labeled": "fallback"}
    assert "key" not in "".join(status)

    card = example_research_card()
    mismatch = validate_card(replace(card, timeframe="1h"))
    assert mismatch.ok is False
    assert "timeframe_mismatch" in mismatch.reasons

    roc_only = card.treatment.model_copy(
        update={"entry": [card.treatment.entry[0]]}
    )
    seed = validate_card(replace(card, treatment=roc_only, control=roc_only))
    assert seed.ok is False
    assert "unbounded_support" in seed.reasons

    branch = ResearchBranch(card)
    child = card.treatment.model_copy(
        update={"exit": ExitRule(kind="holding_period", params={"sessions": 20})}
    )
    empty = branch.propose_child(child, evidence_refs=(), declared_axis="exit")
    assert empty.authorized is False
    assert empty.reasons == ("no_evidence",)
    assert branch.trials_spent == 0

    killed = ResearchBranch(card)
    assert (
        killed.judge(
            scope="family",
            point_pp=-1.0,
            interval_low_pp=-2.0,
            interval_high_pp=-0.2,
            events=80,
            folds=4,
        ).state
        == "KILL"
    )
    reopened = killed.propose_child(child, evidence_refs=("discovery",), declared_axis="exit")
    assert reopened.reasons == ("terminal",)
    assert killed.trials_spent == 0


def test_t10_seeded_uncertainty_is_stable_across_threads() -> None:
    values = np.array([0.1, -0.2, 0.05, 0.0, -0.1, 0.2, 0.15, -0.05])
    first = moving_block_interval(values, block_length=2, seed=11, draws=80, confidence=0.9)
    second = moving_block_interval(values, block_length=2, seed=11, draws=80, confidence=0.9)
    assert first == second

    found: list[object] = []

    def _run() -> None:
        found.append(
            moving_block_interval(values, block_length=2, seed=11, draws=80, confidence=0.9)
        )

    threads = [threading.Thread(target=_run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found == [first, first, first, first]

    short = moving_block_interval(
        values, block_length=1, seed=3, draws=20, horizon_bars=5, timeframe="1h"
    )
    parked = decide_research(
        contract_ok=True,
        scope="child",
        point_pp=short.point_pp,
        interval_low_pp=short.low_pp,
        interval_high_pp=short.high_pp,
        delta_pp=0.0,
        events=80,
        folds=4,
        floor=_floor(),
        assumptions=short.assumptions,
    )
    assert parked.state == "PARK"
    assert parked.reasons == ("assumption_violated",)


def test_example_card_validates_without_running() -> None:
    card = example_research_card()
    result = validate_card(card)
    assert result.ok is True
    assert result.cost_bps_per_side == RESEARCH_COST_BPS
    assert result.cost_label == "research"
    assert card.partitions[-1].contaminated is False
