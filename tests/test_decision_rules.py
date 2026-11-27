"""Decision rules for the family-seed loop, including the seven dry-test closures."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from moneylab.components.registry import get_component
from moneylab.decision import (
    CLOSURES,
    CONTEXT_FEATURES,
    ENGINE_LIMITATIONS,
    SERIES_ACTIVE,
    SERIES_TERMINAL,
    Case,
    Contrast,
    FamilyLoop,
    MatchSpec,
    Measurement,
    OwnerCard,
    Thesis,
    WindowBook,
    changed_axes,
    check_seed,
    falsifier_fires,
    feature_executable,
    fold_windows,
    judge_pair,
    judge_seed,
    measurement_from_equity,
    pick_blame,
    sample_cases,
    settings_diff,
    small_batch_allows,
    tie_break,
    windows_overlap,
)
from moneylab.engine.batch import Job
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    RankBy,
    StrategyConfig,
    config_hash,
    resolve,
)
from moneylab.engine.reporting import metrics

UNIVERSE = ["AAPL", "MSFT", "NVDA"]


def card(**overrides: object) -> OwnerCard:
    base: dict[str, object] = {
        "series": SERIES_ACTIVE,
        "objective_op": "gt",
        "objective_threshold": 0.0,
        "falsifier_op": "lte",
        "falsifier_threshold": 0.0,
        "paired_op": "gt",
        "paired_threshold": 2.0,
        "uncertainty_low_above": 0.0,
        "max_drawdown": 0.25,
        "max_concentration": 0.40,
        "min_distinct_dates": 30,
        "min_folds": 3,
        "family_budget": 10,
        "discovery_budget": 30,
        "universe_id": "static-list",
        "idle_cash": "cash",
        "fold_boundary": "flatten_and_restart",
        "case_seed": 7,
        "holdout_start": date(2022, 1, 3),
        "holdout_end": date(2024, 12, 31),
    }
    base.update(overrides)
    return OwnerCard(**base)  # type: ignore[arg-type]


def _entry_breakout() -> Condition:
    return Condition(
        kind="price_cross",
        indicator=IndicatorRef(id="highest_high", params={"length": 20}),
        params={"direction": "above"},
    )


def _entry_roc() -> Condition:
    return Condition(
        kind="threshold",
        indicator=IndicatorRef(id="roc", params={"length": 20}),
        params={"op": "above", "value": 0},
    )


def recipe(
    *,
    hold: int = 10,
    idle: str = "cash",
    signals: list[Condition] | None = None,
    rank: RankBy | None = None,
    entry: list[Condition] | None = None,
    name: str = "breakout",
) -> StrategyConfig:
    return StrategyConfig(
        name=name,
        universe=UNIVERSE,
        benchmark="SPY",
        timeframe="1d",
        entry=entry or [_entry_breakout()],
        entry_signals=signals or [],
        exit=ExitRule(kind="holding_period", params={"sessions": hold}),
        portfolio=PortfolioSettings(
            max_positions=5,
            target_allocation=0.2,
            idle_cash=idle,  # type: ignore[arg-type]
            cost_bps_per_side=5,
            starting_cash=100_000,
        ),
        rank_by=rank,
    )


def strength(*, hold: int = 10, idle: str = "cash") -> StrategyConfig:
    return recipe(hold=hold, idle=idle, entry=[_entry_roc()], name="prior-strength")


def thesis(**overrides: object) -> Thesis:
    base: dict[str, object] = {
        "claim": "Close above the prior 20-session high, held ten sessions.",
        "recipe": recipe(),
        "control": strength(),
        "falsifier_series": SERIES_ACTIVE,
    }
    base.update(overrides)
    return Thesis(**base)  # type: ignore[arg-type]


def meas(series: float, **overrides: object) -> Measurement:
    values = {SERIES_ACTIVE: series, SERIES_TERMINAL: series}
    extra = overrides.pop("values", None)
    if isinstance(extra, dict):
        values.update(extra)
    measurement = Measurement(
        values=values,
        drawdown=float(overrides.pop("drawdown", 0.19)),  # type: ignore[arg-type]
        distinct_dates=int(overrides.pop("distinct_dates", 140)),  # type: ignore[arg-type]
        folds=int(overrides.pop("folds", 3)),  # type: ignore[arg-type]
        scope=str(overrides.pop("scope", "development")),
        interval_low_pp=overrides.pop("interval_low_pp", 0.5),  # type: ignore[arg-type]
        interval_high_pp=overrides.pop("interval_high_pp", 5.0),  # type: ignore[arg-type]
        concentration=overrides.pop("concentration", 0.1),  # type: ignore[arg-type]
        turnover=float(overrides.pop("turnover", 1.0)),  # type: ignore[arg-type]
        exposure=overrides.pop("exposure", 0.68),  # type: ignore[arg-type]
        trades=int(overrides.pop("trades", 90)),  # type: ignore[arg-type]
        recipe_id=str(overrides.pop("recipe_id", "seed")),
        invalid=bool(overrides.pop("invalid", False)),
        invalid_reason=str(overrides.pop("invalid_reason", "")),
    )
    if "paired_interval_low_pp" in overrides:
        measurement.paired_interval_low_pp = overrides.pop("paired_interval_low_pp")  # type: ignore[assignment]
    if "paired_interval_high_pp" in overrides:
        measurement.paired_interval_high_pp = overrides.pop("paired_interval_high_pp")  # type: ignore[assignment]
    if overrides:
        raise TypeError(f"unexpected measurement fields {sorted(overrides)}")
    return measurement


def fold_meas(series: float, **overrides: object) -> Measurement:
    overrides.setdefault("scope", "fold")
    overrides.setdefault("folds", 1)
    overrides.setdefault("distinct_dates", 40)
    overrides.setdefault("paired_interval_low_pp", 0.4)
    return meas(series, **overrides)


def _contrast(
    question: str,
    effect: float,
    *,
    low: float = 0.2,
    dates: int = 45,
    feature: str | None = None,
    sides: bool = False,
) -> Contrast:
    side_a = (Case("a-best", 3.0), Case("a-worst", -2.0)) if sides else ()
    side_b = (Case("b-best", 1.0), Case("b-worst", -1.5)) if sides else ()
    return Contrast(
        question=question,  # type: ignore[arg-type]
        effect_pp=effect,
        interval_low_pp=low,
        interval_high_pp=effect + 1,
        distinct_dates=dates,
        feature_id=feature,
        side_a=side_a,
        side_b=side_b,
    )


def _open(loop: FamilyLoop | None = None) -> tuple[FamilyLoop, object]:
    branch = loop or FamilyLoop(card())
    seed = branch.submit_seed(thesis())
    assert seed.accepted
    return branch, seed


def _read_exit(branch: FamilyLoop, measurement: Measurement | None = None) -> None:
    branch.read(
        measurement or meas(7.0, interval_low_pp=-1.0),
        [
            _contrast("filter", 2.2, feature="stock_minus_sector"),
            _contrast("exit", 0.9, sides=True),
            _contrast("ranking", 0.8),
            _contrast("entry", 0.15, low=-0.2),
        ],
        report_id="r1",
        outcome_dates=[date(2020, 6, 1)],
    )


def _propose_exit(branch: FamilyLoop, *, hold: int = 5, experiment_id: str = "exp-exit") -> object:
    return branch.propose(
        question="exit",
        child=recipe(hold=hold),
        experiment_id=experiment_id,
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1), date(2021, 7, 1), date(2020, 6, 1)],
        fit_dates=[date(2020, 1, 2)],
    )


def test_context_features_reject_when_the_series_is_absent() -> None:
    for feature_id in CONTEXT_FEATURES:
        assert get_component(feature_id).id == feature_id
        ok, reason = feature_executable(feature_id)
        assert not ok
        assert reason == "context_data_absent"
    assert "sector" in ENGINE_LIMITATIONS[1]
    assert "VIX" in ENGINE_LIMITATIONS[1]


def test_closures_name_the_seven_rules() -> None:
    assert set(CLOSURES) == {
        "one_metric",
        "blame_picker",
        "selection_end",
        "embargo",
        "control_contract",
        "closed_definitions",
        "two_counters",
    }
    assert "active_excess_pp" in CLOSURES["one_metric"]
    assert "terminal_net_excess_pp" in CLOSURES["one_metric"]
    assert "filter, then exit, then ranking, then entry" in CLOSURES["blame_picker"]
    assert "no follow-up" in CLOSURES["selection_end"]
    assert "flatten and restart" in CLOSURES["closed_definitions"]
    assert "same universe id" in CLOSURES["two_counters"]


def test_cash_falsifier_must_be_the_active_series() -> None:
    rejected = check_seed(card(series=SERIES_TERMINAL), thesis(falsifier_series=SERIES_TERMINAL))
    assert not rejected.accepted
    assert "series_not_allowed_for_cash" in rejected.reasons
    assert rejected.spends_trial is False
    assert rejected.trials_reserved == 0

    allowed = check_seed(
        card(series=SERIES_TERMINAL, idle_cash="benchmark"),
        thesis(
            falsifier_series=SERIES_TERMINAL,
            recipe=recipe(idle="benchmark"),
            control=strength(idle="benchmark"),
        ),
    )
    assert allowed.accepted
    assert allowed.trials_reserved == 2


def test_plan_seed_is_rejected_without_spending_a_trial() -> None:
    branch = FamilyLoop(card())
    result = branch.submit_seed(
        Thesis(
            claim="Twenty largest SPY holdings by the latest published weight.",
            recipe=recipe(),
            requires_historical_weights=True,
            match=MatchSpec(key=None, tolerance=None, as_of=None),
            falsifier_series=SERIES_ACTIVE,
        )
    )
    assert not result.accepted
    assert "point_in_time_weights_unavailable" in result.reasons
    assert "invalid_match" in result.reasons
    assert result.spends_trial is False
    assert branch.trials_spent == 0
    assert branch.discovery_spent == 0
    assert result.jobs == []


def test_complete_match_is_recorded_and_not_executed() -> None:
    result = check_seed(
        card(),
        Thesis(
            claim="Matched on prior strength.",
            recipe=recipe(),
            match=MatchSpec(key="roc", tolerance=0.02, as_of="decision"),
        ),
    )
    assert not result.accepted
    assert "matched_cohort_data_absent" in result.reasons
    assert result.limitations
    assert "not filled in" in result.limitations[0]
    assert result.spends_trial is False


def test_executable_control_reserves_two_family_trials() -> None:
    branch, seed = _open()
    assert seed.trials_reserved == 2  # type: ignore[attr-defined]
    assert branch.trials_spent == 2
    assert len(seed.jobs) == 2  # type: ignore[attr-defined]
    assert all(isinstance(job, Job) for job in seed.jobs)  # type: ignore[attr-defined]
    axes = changed_axes(seed.resolved_recipe, seed.resolved_control)  # type: ignore[attr-defined]
    assert axes == ["entry"]


def test_control_must_share_execution_and_differ_in_entry() -> None:
    different_hold = check_seed(card(), thesis(control=strength(hold=5)))
    assert "control_not_same_execution" in different_hold.reasons
    duplicate = check_seed(card(), thesis(control=recipe(name="copy")))
    assert "control_not_different" in duplicate.reasons
    assert duplicate.trials_reserved == 0


def test_unknown_indicator_is_a_technical_rejection() -> None:
    bad_entry = Condition(
        kind="threshold",
        indicator=IndicatorRef(id="not_a_feature"),
        params={"op": "above", "value": 1},
    )
    bad = recipe(entry=[bad_entry])
    result = check_seed(card(), thesis(recipe=bad))
    assert not result.accepted
    assert "recipe_invalid" in result.reasons
    assert result.spends_trial is False


def test_unset_concentration_and_carry_positions_block_the_seed() -> None:
    missing = check_seed(card(max_concentration=None), thesis())
    assert "unset:max_concentration" in missing.reasons
    carry = check_seed(card(fold_boundary="carry_positions"), thesis())
    assert "fold_boundary" in carry.reasons


def test_falsifier_uses_only_the_bound_series() -> None:
    cash_card = card()
    values = {SERIES_ACTIVE: 3.0, SERIES_TERMINAL: -8.0}
    assert not falsifier_fires(cash_card, meas(3.0, values=values, exposure=0.2))
    benchmark_card = card(series=SERIES_TERMINAL, idle_cash="benchmark")
    assert falsifier_fires(benchmark_card, meas(-8.0, values=values))
    alive = judge_seed(cash_card, meas(3.0, values=values, interval_low_pp=-1.0, exposure=0.2))
    assert alive.outcome == "alive"
    killed = judge_seed(cash_card, meas(-1.0, interval_low_pp=0.2))
    assert killed.outcome == "kill"
    assert killed.reasons == ["falsifier"]


def test_active_excess_ignores_sessions_spent_in_cash() -> None:
    equity = pd.DataFrame(
        {
            "equity": [100.0, 101.0, 101.0, 101.0],
            "benchmark_equity": [100.0, 100.5, 102.0, 103.0],
            "positions_value": [100.0, 0.0, 0.0, 0.0],
        }
    )
    fills = pd.DataFrame()
    got = measurement_from_equity(equity, fills, distinct_dates=3, folds=1)
    own = metrics(equity, fills)
    bench = metrics(equity, fills, column="benchmark_equity")
    gap = (own["total_return"] - bench["total_return"]) * 100
    assert got.values[SERIES_TERMINAL] == pytest.approx(gap)
    assert got.values[SERIES_TERMINAL] == pytest.approx(-2.0)
    assert got.values[SERIES_ACTIVE] == pytest.approx(0.5)
    assert got.drawdown == pytest.approx(abs(own["max_drawdown"]))
    assert not falsifier_fires(card(), got)


def test_crowded_day_is_inconclusive_even_with_many_trades() -> None:
    judgment = judge_seed(card(), meas(12.0, concentration=0.85, trades=200, distinct_dates=80))
    assert judgment.outcome == "inconclusive"
    assert "inconclusive_concentration" in judgment.reasons
    equal_share = judge_seed(
        card(max_concentration=0.40), meas(12.0, concentration=0.40, trades=200)
    )
    assert equal_share.outcome == "qualifier"


def test_hard_rejection_is_only_a_drawdown_breach() -> None:
    at_limit = hard = judge_seed(card(), meas(4.0, drawdown=0.25))
    assert at_limit.outcome == "qualifier"
    del hard
    breach = judge_seed(card(), meas(4.0, drawdown=0.30))
    assert breach.outcome == "kill"
    assert breach.reasons == ["drawdown_breach"]


def test_paired_equality_is_the_owner_operator() -> None:
    parent = fold_meas(6.0)
    child = fold_meas(8.0, interval_low_pp=0.4)
    strict = judge_pair(
        card(paired_op="gt"),
        parent,
        child,
        axis="exit",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert strict.outcome == "drop"
    assert "hurdle_miss" in strict.reasons
    inclusive = judge_pair(
        card(paired_op="gte"),
        parent,
        child,
        axis="exit",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert inclusive.outcome == "keep"
    assert inclusive.paired_pp == pytest.approx(2.0)


def test_filter_drop_and_exit_keep() -> None:
    parent = fold_meas(6.4)
    filtered = fold_meas(8.2, paired_interval_low_pp=-0.2, distinct_dates=36)
    dropped = judge_pair(
        card(),
        parent,
        filtered,
        axis="filter",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert dropped.outcome == "drop"
    assert "hurdle_miss" in dropped.reasons
    assert "uncertainty" in dropped.reasons
    assert dropped.parent_kept is True
    assert dropped.failed_entry_count == 0

    kept = judge_pair(
        card(),
        fold_meas(7.0),
        fold_meas(10.0, drawdown=0.14, interval_low_pp=0.4, paired_interval_low_pp=0.4),
        axis="exit",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert kept.outcome == "keep"
    assert kept.qualifier
    assert kept.paired_pp == pytest.approx(3.0)


def test_failed_entry_counts_only_a_conclusive_miss_or_falsifier() -> None:
    thin = judge_pair(
        card(),
        fold_meas(5.0),
        fold_meas(6.0, distinct_dates=18),
        axis="entry",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert thin.outcome == "inconclusive"
    assert thin.failed_entry_count == 0
    assert thin.entry_closed is False

    missed = judge_pair(
        card(),
        fold_meas(5.0),
        fold_meas(6.0, paired_interval_low_pp=0.2),
        axis="entry",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert missed.outcome == "drop"
    assert missed.failed_entry_count == 1
    assert missed.entry_closed is True

    fired = judge_pair(
        card(),
        fold_meas(5.0),
        fold_meas(-1.0),
        axis="entry",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert fired.outcome == "kill"
    assert fired.failed_entry_count == 1

    drawdown = judge_pair(
        card(),
        fold_meas(5.0),
        fold_meas(5.0, drawdown=0.40),
        axis="entry",
        failed_entry_count=0,
        entry_closed=False,
    )
    assert drawdown.outcome == "kill"
    assert drawdown.reasons == ["drawdown_breach"]
    assert drawdown.failed_entry_count == 0
    assert drawdown.entry_closed is True


def test_blame_picker_skips_what_the_catalog_cannot_run() -> None:
    contrasts = [
        _contrast("entry", 0.15, low=-0.1, dates=70),
        _contrast("filter", 2.2, feature="stock_minus_sector"),
        _contrast("exit", 0.9, dates=70),
        _contrast("ranking", 0.8, dates=40),
    ]
    selected, skipped = pick_blame(card(), contrasts, entry_closed=False)
    assert selected is not None
    assert selected.question == "exit"
    assert any(item.reason == "context_data_absent" for item in skipped)
    assert any(item.question == "entry" and item.reason == "not_supported" for item in skipped)

    tie = [
        _contrast("exit", 1.5),
        _contrast("filter", 1.5, feature="roc"),
        _contrast("ranking", 1.5),
    ]
    winner, _ = pick_blame(card(), tie, entry_closed=False)
    assert winner is not None
    assert winner.question == "filter"

    closed, skipped_entry = pick_blame(card(), [_contrast("entry", 3.0)], entry_closed=True)
    assert closed is None
    assert skipped_entry[0].reason == "entry_closed"


def test_read_order_starts_at_the_portfolio_and_samples_both_sides() -> None:
    report = FamilyLoop(card()).read(
        meas(7.0, exposure=0.68, drawdown=0.19),
        [
            _contrast("exit", 0.9, sides=True),
            _contrast("filter", 2.2, feature="stock_minus_sector"),
        ],
        report_id="r1",
        kind="portfolio",
    )
    assert [step.kind for step in report.steps] == [
        "portfolio_vs_benchmark",
        "control_and_parent",
        "contrasts",
        "cases",
    ]
    assert report.steps[0].detail["exposure"] == pytest.approx(0.68)
    assert report.selected is not None
    assert report.selected.question == "exit"
    cases = report.steps[-1].detail
    assert cases["side_a"] == ["a-best", "a-worst"]
    assert cases["side_b"] == ["b-best", "b-worst"]

    prediction = FamilyLoop(card()).read(
        meas(1.0),
        [],
        report_id="p",
        kind="prediction",
        group_effect_pp=1.5,
    )
    assert prediction.steps[0].kind == "group_difference"
    assert prediction.steps[0].detail["effect_pp"] == pytest.approx(1.5)


def test_case_sample_is_seeded_and_keeps_the_extremes() -> None:
    rows = [Case(f"id-{i}", float(i)) for i in range(20)]
    first = sample_cases(rows, 1)
    assert first == sample_cases(rows, 1)
    assert "id-0" in first
    assert "id-19" in first
    assert len(first) == 5
    assert sample_cases(rows, 2) != first
    assert sample_cases(rows[:2], 1) == ["id-0", "id-1"]


def test_one_change_authorizes_the_hold_and_rejects_two_axes() -> None:
    branch, _ = _open()
    _read_exit(branch)
    spent = branch.trials_spent
    auth = _propose_exit(branch)
    assert auth.authorized  # type: ignore[attr-defined]
    assert auth.spends_trial  # type: ignore[attr-defined]
    assert auth.axes == ["exit"]  # type: ignore[attr-defined]
    assert branch.trials_spent == spent + 1
    assert date(2020, 6, 1) not in auth.scoring_dates  # type: ignore[attr-defined]
    assert date(2021, 6, 1) in auth.scoring_dates  # type: ignore[attr-defined]
    assert auth.settings_diff  # type: ignore[attr-defined]
    assert all("exits" in row["setting"] for row in auth.settings_diff)  # type: ignore[attr-defined]

    other = _propose_exit(branch, experiment_id="exp-exit")
    assert other.reasons == ["retry"]  # type: ignore[attr-defined]
    assert other.spends_trial is False  # type: ignore[attr-defined]
    assert branch.trials_spent == spent + 1

    revised = branch.propose(
        question="exit",
        child=recipe(hold=7),
        experiment_id="exp-exit",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[],
    )
    assert revised.reasons == ["experiment_id_reused"]
    assert branch.trials_spent == spent + 1

    wrong = branch.propose(
        question="filter",
        child=recipe(signals=[_entry_roc()]),
        experiment_id="exp-filter",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[],
    )
    assert wrong.reasons == ["not_the_selected_change"]
    assert branch.trials_spent == spent + 1

    both = branch.propose(
        question="exit",
        child=recipe(
            hold=5,
            rank=RankBy(
                indicator=IndicatorRef(id="roc", params={"length": 20}),
                order="descending",
            ),
        ),
        experiment_id="exp-both",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[],
    )
    assert both.reasons == ["multiple_axes"]
    assert both.spends_trial is False


def test_embargo_and_small_batch_gate_do_not_spend() -> None:
    branch, _ = _open()
    _read_exit(branch)
    empty = branch.propose(
        question="exit",
        child=recipe(hold=5),
        experiment_id="exp-empty",
        cited_reports=["r1"],
        score_dates=[date(2020, 6, 1)],
        fit_dates=[],
    )
    assert empty.reasons == ["embargo_empty"]
    overlap = branch.propose(
        question="exit",
        child=recipe(hold=5),
        experiment_id="exp-fit",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[date(2021, 6, 1)],
    )
    assert overlap.reasons == ["fit_overlaps_scoring"]

    blocked = FamilyLoop(card())
    blocked.submit_seed(thesis())
    _read_exit(blocked)
    blocked.one_axis_done = 10
    blocked.one_axis_qualifiers = 0
    spent = blocked.trials_spent
    gate = _propose_exit(blocked, experiment_id="exp-11")
    assert gate.reasons == ["small_batch_gate"]  # type: ignore[attr-defined]
    assert blocked.trials_spent == spent
    assert small_batch_allows(10, 1, 1) is True
    assert small_batch_allows(10, 0, 1) is False
    assert small_batch_allows(9, 0, 1) is True
    assert small_batch_allows(3, 0, 0) is False


def test_context_feature_proposal_is_rejected() -> None:
    branch, _ = _open()
    branch.read(
        meas(7.0, interval_low_pp=-1.0),
        [_contrast("filter", 2.2, feature="roc")],
        report_id="r1",
        outcome_dates=[],
    )
    auth = branch.propose(
        question="filter",
        child=recipe(signals=[_entry_roc()]),
        experiment_id="exp-vix",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[],
        feature_id="vix_level",
    )
    assert not auth.authorized
    assert auth.reasons == ["context_data_absent"]
    assert auth.limitations
    assert branch.trials_spent == 2

    legal = branch.propose(
        question="filter",
        child=recipe(signals=[_entry_roc()]),
        experiment_id="exp-roc",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[],
        feature_id="roc",
    )
    assert legal.authorized
    assert legal.axes == ["filter"]


def test_no_follow_up_freezes_a_qualifier_and_parks_otherwise() -> None:
    branch, seed = _open()
    current = meas(7.0, interval_low_pp=0.4, recipe_id="")
    judgment = branch.judge_current(current)
    assert judgment.qualifier
    assert branch.trials_spent == 2
    _read_exit(branch, current)
    end = branch.end("no_follow_up")
    assert end.outcome == "freeze"
    assert end.end_record == "no_follow_up"
    assert end.recipe_id == config_hash(seed.resolved_recipe)  # type: ignore[attr-defined]

    parked = FamilyLoop(card())
    parked.submit_seed(thesis())
    parked.judge_current(meas(7.0, interval_low_pp=-1.0))
    end_park = parked.end("no_follow_up")
    assert end_park.outcome == "park"
    assert end_park.end_record == "no_follow_up"
    assert parked.trials_spent == 2


def test_failed_child_leaves_the_qualified_parent() -> None:
    branch, seed = _open()
    parent_id = config_hash(seed.resolved_recipe)  # type: ignore[attr-defined]
    branch.judge_current(meas(7.0, interval_low_pp=0.4))
    _read_exit(branch)
    _propose_exit(branch)
    dropped = branch.judge_child(fold_meas(8.0, paired_interval_low_pp=-0.1))
    assert dropped.outcome == "drop"
    end = branch.end("no_authorized_experiment")
    assert end.outcome == "freeze"
    assert end.recipe_id == parent_id
    assert end.end_record == "no_authorized_experiment"


def test_keep_replaces_the_parent_and_budget_left_does_not_freeze_early() -> None:
    branch, _ = _open()
    branch.judge_current(meas(7.0, interval_low_pp=-1.0))
    _read_exit(branch)
    auth = _propose_exit(branch)
    assert branch.selection is None
    kept = branch.judge_child(
        fold_meas(10.0, drawdown=0.14, interval_low_pp=0.4, paired_interval_low_pp=0.4)
    )
    assert kept.outcome == "keep"
    assert kept.qualifier
    assert branch.selection is None
    assert branch.status == "qualifier"
    end = branch.end("no_follow_up")
    assert end.outcome == "freeze"
    assert end.recipe_id == config_hash(resolve(recipe(hold=5)))
    assert auth.authorized  # type: ignore[attr-defined]


def test_budget_exhausted_ends_selection() -> None:
    frozen = FamilyLoop(card(family_budget=2))
    frozen.submit_seed(thesis())
    frozen.judge_current(meas(4.0, interval_low_pp=0.3))
    assert frozen.selection is not None
    assert frozen.selection.outcome == "freeze"
    assert frozen.selection.end_record == "budget_exhausted"

    parked = FamilyLoop(card(family_budget=2))
    parked.submit_seed(thesis())
    parked.judge_current(meas(4.0, interval_low_pp=-1.0))
    assert parked.selection is not None
    assert parked.selection.outcome == "park"
    assert parked.selection.end_record == "budget_exhausted"


def test_paper_gate_queues_one_run_and_locks_the_window() -> None:
    book = WindowBook()
    branch = FamilyLoop(card(), book)
    _, seed = _open(branch)
    branch.judge_current(meas(4.0, interval_low_pp=0.4))
    branch.end("no_follow_up")
    passing = meas(
        4.0,
        distinct_dates=45,
        folds=1,
        scope="holdout",
        drawdown=0.16,
        interval_low_pp=0.2,
    )
    first = branch.score_holdout(passing)
    assert first.outcome == "pass"
    assert first.queue_paper
    assert first.paper_runs == 1
    assert first.payload["recipe_hash"] == config_hash(seed.resolved_recipe)  # type: ignore[attr-defined]
    second = branch.score_holdout(passing)
    assert second.queue_paper is False
    assert second.paper_runs == 1
    assert "paper_already_queued" in second.reasons

    other = FamilyLoop(card(), book)
    other.submit_seed(thesis(recipe=recipe(hold=5), control=strength(hold=5)))
    other.judge_current(meas(4.0, interval_low_pp=0.4))
    other.end("no_follow_up")
    blocked = other.score_holdout(passing)
    assert blocked.outcome == "blocked"
    assert "holdout_disclosed" in blocked.reasons
    assert blocked.queue_paper is False

    later = FamilyLoop(
        card(holdout_start=date(2025, 1, 2), holdout_end=date(2025, 12, 31)),
        book,
    )
    later.submit_seed(thesis())
    later.judge_current(meas(4.0, interval_low_pp=0.4))
    later.end("no_follow_up")
    opened = later.score_holdout(passing)
    assert opened.outcome == "pass"
    assert opened.queue_paper

    elsewhere = FamilyLoop(card(universe_id="other-list"), book)
    elsewhere.submit_seed(thesis())
    elsewhere.judge_current(meas(4.0, interval_low_pp=0.4))
    elsewhere.end("no_follow_up")
    separate = elsewhere.score_holdout(passing)
    assert separate.outcome == "pass"


def test_holdout_fail_and_thin_sample_do_not_queue_paper() -> None:
    failed = FamilyLoop(card())
    failed.submit_seed(thesis())
    failed.judge_current(meas(4.0, interval_low_pp=0.4))
    failed.end("no_follow_up")
    fail = failed.score_holdout(meas(-1.0, distinct_dates=45, folds=1, scope="holdout"))
    assert fail.outcome == "fail"
    assert fail.queue_paper is False
    assert failed.status == "closed"

    thin = FamilyLoop(card())
    thin.submit_seed(thesis())
    thin.judge_current(meas(4.0, interval_low_pp=0.4))
    thin.end("no_follow_up")
    parked = thin.score_holdout(meas(4.0, distinct_dates=10, folds=1, scope="holdout"))
    assert parked.outcome == "park"
    assert parked.queue_paper is False

    early = FamilyLoop(card())
    assert early.score_holdout(meas(4.0)).reasons == ["not_frozen"]


def test_related_windows_overlap_only_for_the_same_universe() -> None:
    start, end = date(2022, 1, 3), date(2024, 12, 31)
    assert windows_overlap(
        "static-list", start, end, "static-list", date(2024, 12, 31), date(2025, 1, 1)
    )
    assert not windows_overlap(
        "static-list", start, end, "static-list", date(2025, 1, 2), date(2025, 12, 31)
    )
    assert not windows_overlap("static-list", start, end, "other-list", start, end)


def test_tie_break_uses_objective_then_turnover_then_recipe_id() -> None:
    rows = [
        meas(5.0, turnover=2.0, recipe_id="b"),
        meas(5.0, turnover=1.0, recipe_id="c"),
        meas(4.0, turnover=0.1, recipe_id="a"),
        meas(5.0, turnover=1.0, recipe_id="a"),
    ]
    assert tie_break(card(), rows).recipe_id == "a"


def test_fold_windows_flatten_and_restart() -> None:
    resolved = resolve(recipe())
    folds = fold_windows(
        resolved,
        [
            (date(2016, 1, 4), date(2018, 12, 31)),
            (date(2019, 1, 2), date(2021, 12, 31)),
        ],
    )
    assert folds[0]["start"] == "2016-01-04"
    assert folds[1]["end"] == "2021-12-31"
    assert folds[0]["execution"]["fold_boundary"] == "flatten_and_restart"
    assert resolved["start"] is None
    assert "fold_boundary" not in resolved["execution"]


def test_compare_helpers_see_identical_ledgers() -> None:
    resolved = resolve(recipe())
    diff = settings_diff(resolved, resolved)
    assert diff["settings"] == []
    empty = pd.DataFrame(columns=["session", "symbol", "action", "reason", "qty"])
    equity = pd.DataFrame(columns=["session", "equity"])
    compared = settings_diff(
        resolved,
        resolved,
        {
            "decisions_a": empty,
            "decisions_b": empty,
            "fills_a": empty,
            "fills_b": empty,
            "equity_a": equity,
            "equity_b": equity,
        },
    )
    assert compared["first_divergence"] is None


def test_entry_replacement_closes_after_one_conclusive_miss() -> None:
    branch, _ = _open()
    branch.judge_current(meas(3.0, interval_low_pp=-1.0))
    branch.read(
        meas(3.0, interval_low_pp=-1.0),
        [_contrast("entry", 1.2)],
        report_id="r1",
        outcome_dates=[],
    )
    first = branch.propose(
        question="entry",
        child=strength(),
        experiment_id="exp-entry",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[],
    )
    assert first.authorized
    missed = branch.judge_child(fold_meas(4.0, paired_interval_low_pp=0.3))
    assert missed.failed_entry_count == 1
    assert branch.entry_closed
    branch.read(
        meas(3.0, interval_low_pp=-1.0),
        [_contrast("entry", 1.2)],
        report_id="r2",
        outcome_dates=[],
    )
    assert branch.last_read is not None
    assert branch.last_read.selected is None
    second = branch.propose(
        question="entry",
        child=strength(),
        experiment_id="exp-entry-2",
        cited_reports=["r2"],
        score_dates=[date(2021, 8, 2)],
        fit_dates=[],
    )
    assert second.reasons == ["entry_closed"]
    assert branch.trials_spent == 3


def test_inconclusive_entry_does_not_use_the_replacement() -> None:
    branch, _ = _open()
    branch.judge_current(meas(3.0, interval_low_pp=-1.0), fresh_interval=True)
    branch.read(meas(3.0, interval_low_pp=-1.0), [_contrast("entry", 1.2)], report_id="r1")
    branch.propose(
        question="entry",
        child=strength(),
        experiment_id="exp-thin",
        cited_reports=["r1"],
        score_dates=[date(2021, 6, 1)],
        fit_dates=[],
    )
    thin = branch.judge_child(fold_meas(4.0, distinct_dates=18))
    assert thin.outcome == "inconclusive"
    assert branch.failed_entry_count == 0
    assert branch.entry_closed is False
    branch.read(meas(3.0, interval_low_pp=-1.0), [_contrast("entry", 1.2)], report_id="r2")
    again = branch.propose(
        question="entry",
        child=strength(),
        experiment_id="exp-entry-again",
        cited_reports=["r2"],
        score_dates=[date(2021, 8, 2)],
        fit_dates=[],
    )
    assert again.authorized


def test_child_kill_does_not_erase_a_live_parent() -> None:
    branch, seed = _open()
    parent_id = config_hash(seed.resolved_recipe)  # type: ignore[attr-defined]
    branch.judge_current(meas(3.0, interval_low_pp=-1.0))
    _read_exit(branch)
    _propose_exit(branch)
    killed = branch.judge_child(fold_meas(-2.0))
    assert killed.outcome == "kill"
    assert branch.status != "killed"
    assert branch.recipe_id == parent_id
    assert branch.selection is None
