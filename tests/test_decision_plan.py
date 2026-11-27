"""The plan's missing pieces: proposer, search, context data, walk-forward, paper score."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from moneylab.components.context import ContextDataAbsent, vix_change_1, vix_percentile_252
from moneylab.components.registry import get_component
from moneylab.data.snapshot import MarketData
from moneylab.decision.context import (
    ContextBook,
    matched_cohort,
    members_on,
)
from moneylab.decision.loop import Thesis, check_seed
from moneylab.decision.measure import changed_axes
from moneylab.decision.proposer import (
    FallbackProposer,
    ModelProposer,
    ProposerOverreach,
    Reading,
    StyleBudget,
    accept_family,
    allow_new_component,
    open_proposer,
    repair_falsifier,
)
from moneylab.decision.rules import SERIES_ACTIVE, MatchSpec, OwnerCard, feature_executable
from moneylab.decision.search import (
    SearchDoesNotPick,
    SearchPlan,
    SearchRefused,
    authorize_search,
    cash_rerun,
    pick_winner,
    variants_on_axis,
)
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    resolve,
)
from moneylab.engine.simulator import simulate
from moneylab.paper.score import score_account
from moneylab.settings import Settings

UNIVERSE = ("AAA", "BBB")


def card() -> OwnerCard:
    return OwnerCard(
        series=SERIES_ACTIVE,
        objective_op="gt",
        objective_threshold=0.0,
        falsifier_op="lte",
        falsifier_threshold=0.0,
        paired_op="gt",
        paired_threshold=2.0,
        uncertainty_low_above=0.0,
        max_drawdown=0.25,
        max_concentration=0.40,
        min_distinct_dates=30,
        min_folds=3,
        family_budget=10,
        discovery_budget=30,
        universe_id="static-list",
        idle_cash="cash",
        holdout_start=date(2024, 1, 2),
        holdout_end=date(2024, 6, 28),
    )


def budget() -> StyleBudget:
    return StyleBudget(
        remaining={"bounce": 10, "breakout": 10, "trend": 10, "rotation": 10},
        universe=UNIVERSE,
        open_family="breakout",
    )


def test_default_cost_is_zero() -> None:
    assert Settings().cost_bps_per_side == 0.0


def test_fallback_writes_a_breakout_thesis_the_lab_can_run() -> None:
    proposer = open_proposer()
    assert proposer.__class__.__name__ == "FallbackProposer"
    draft = proposer.write_thesis("breakout", budget())
    assert draft.signals == ("highest_high",)
    assert draft.falsifier_series == "active_excess_pp"
    assert draft.knobs["idle_cash"] == "cash"
    result = check_seed(
        card(),
        Thesis(
            claim=draft.claim,
            recipe=draft.recipe,
            control=draft.control,
            falsifier_series=draft.falsifier_series,
        ),
    )
    assert result.accepted
    assert result.resolved_recipe is not None
    assert result.resolved_recipe["portfolio"]["cost_bps_per_side"] == 0.0
    axes = changed_axes(result.resolved_recipe, result.resolved_control)
    assert axes == ["entry"]


def test_fallback_refuses_an_unnamed_universe_and_a_family_outside_the_budget() -> None:
    proposer = FallbackProposer()
    with pytest.raises(Exception, match="universe_missing"):
        proposer.write_thesis("breakout", StyleBudget(remaining={"breakout": 1}, universe=()))
    ok, reason = accept_family("momentum", budget())
    assert not ok
    assert reason == "family_outside_budget"
    spent = StyleBudget(
        remaining={"bounce": 0, "breakout": 0, "trend": 4, "rotation": 0},
        universe=UNIVERSE,
        open_family="breakout",
    )
    assert proposer.propose_family(spent) == "trend"
    refused, why = accept_family("breakout", spent)
    assert not refused
    assert why == "family_budget_spent"
    assert allow_new_component(1) is False
    assert allow_new_component(2) is True


def test_proposer_names_one_bucket_and_does_not_kill() -> None:
    proposer = FallbackProposer()
    parent = proposer.write_thesis("breakout", budget()).recipe
    cash = proposer.name_bucket(Reading(idle_cash="cash", fair_gap_pp=-3.0))
    assert cash.bucket is None
    assert cash.change == "idle_cash_benchmark"
    fair = cash_rerun(parent)
    assert fair.portfolio.idle_cash == "benchmark"
    assert parent.entry == fair.entry
    gave_back = proposer.name_bucket(Reading(idle_cash="benchmark", giveback_pp=1.5))
    assert gave_back.bucket == "exit"
    child = proposer.propose_change(parent, "exit")
    assert changed_axes(resolve(parent), resolve(child)) == ["exit"]
    filtered = proposer.propose_change(parent, "filter")
    assert changed_axes(resolve(parent), resolve(filtered)) == ["filter"]
    replaced = proposer.propose_change(parent, "entry")
    assert changed_axes(resolve(parent), resolve(replaced)) == ["entry"]
    quiet = proposer.name_bucket(Reading(idle_cash="benchmark", fair_gap_pp=1.0, edge_t=2.0))
    assert quiet.bucket is None
    assert quiet.reason == "no_bucket"


def test_model_payload_cannot_kill_freeze_or_paper() -> None:
    def kill(_: str) -> str:
        return json.dumps({"verdict": "kill", "bucket": "exit"})

    with pytest.raises(ProposerOverreach):
        ModelProposer(kill).name_bucket(Reading(idle_cash="benchmark"))

    def freeze(_: str) -> str:
        return json.dumps({"bucket": "paper"})

    with pytest.raises(ProposerOverreach):
        ModelProposer(freeze).name_bucket(Reading(idle_cash="benchmark"))

    def legal(_: str) -> str:
        return json.dumps(
            {"bucket": "filter", "reason": "losing_slice", "change": "sma_200_filter"}
        )

    named = ModelProposer(legal).name_bucket(Reading(idle_cash="benchmark"))
    assert named.bucket == "filter"


def test_cash_falsifier_is_rewritten_without_a_verdict() -> None:
    proposer = FallbackProposer()
    draft = proposer.write_thesis("breakout", budget())
    draft.falsifier_series = "terminal_net_excess_pp"
    draft.falsifier = "Out of sample, the fair gap versus the benchmark is not positive."
    repaired = repair_falsifier(draft, idle_cash="cash")
    assert repaired.falsifier_series == "active_excess_pp"
    assert not hasattr(repaired, "verdict")


def test_search_caps_a_new_idea_and_does_not_pick_a_winner() -> None:
    parent = FallbackProposer().write_thesis("breakout", budget()).recipe
    holds = variants_on_axis(parent, "exit", [10, 5, 20])
    assert [int(item.exit.params["sessions"]) for item in holds] == [10, 5, 20]
    with pytest.raises(SearchRefused) as small:
        variants_on_axis(parent, "exit", list(range(1, 12)))
    assert small.value.reasons == ["small_search_cap"]
    early = authorize_search(SearchPlan("exit", 11, wide=True, oos_survivor=False))
    assert early.reasons == ["wide_search_before_oos"]
    assert early.winner is None
    later = authorize_search(SearchPlan("exit", 11, wide=True, oos_survivor=True))
    assert later.authorized
    assert later.winner is None
    too_wide = authorize_search(SearchPlan("exit", 31, wide=True, oos_survivor=True))
    assert too_wide.reasons == ["wide_search_cap"]
    with pytest.raises(SearchDoesNotPick):
        pick_winner([{"return": 0.2}, {"return": 0.4}])


def test_context_series_compute_when_supplied_and_reject_when_absent() -> None:
    index = pd.bdate_range("2023-01-03", periods=4)
    frame = pd.DataFrame(
        {
            "close": [100.0, 110.0, 110.0, 121.0],
            "sector_close": [50.0, 50.0, 55.0, 55.0],
            "benchmark_close": [20.0, 21.0, 21.0, 21.0],
        },
        index=index,
    )
    gap = get_component("stock_minus_sector").compute(frame, {})
    assert gap is not None
    # 110/100 - 1 = 0.10, sector 50/50 - 1 = 0, so the second session is +0.10.
    assert gap.iloc[1] == pytest.approx(0.10)
    bare = frame.drop(columns=["sector_close", "benchmark_close"])
    with pytest.raises(ContextDataAbsent):
        get_component("stock_minus_sector").compute(bare, {})
    vix = pd.Series([10.0, 12.0, 12.0], index=index[:3])
    changed = vix_change_1(pd.DataFrame({"vix_close": vix}), {})
    assert changed.iloc[1] == pytest.approx(0.2)
    short = pd.DataFrame({"vix_close": [10.0, 11.0, 12.0]})
    assert vix_percentile_252(short, {}).isna().all()
    book = ContextBook(
        sector_close={"AAA": frame["sector_close"]},
        benchmark_close=frame["benchmark_close"],
    )
    assert feature_executable("stock_minus_sector", book) == (True, "")
    assert feature_executable("vix_level", book)[1] == "context_data_absent"


def test_point_in_time_weights_ignore_the_future_and_block_early_entries() -> None:
    sessions = pd.bdate_range("2023-01-03", periods=30)
    join = sessions[15]
    weights = pd.DataFrame(
        {
            "as_of": [sessions[0], join, sessions[0] + pd.Timedelta(days=400)],
            "symbol": ["AAA", "BBB", "BBB"],
            "weight": [0.6, 0.4, 0.9],
        }
    )
    assert "BBB" not in members_on(weights, str(sessions[0].date()))
    assert members_on(weights, str(join.date()))["BBB"] == pytest.approx(0.4)
    assert members_on(weights, str(sessions[0].date()))["AAA"] == pytest.approx(0.6)
    data = _market(sessions)
    recipe = _fast_recipe()
    resolved = resolve(recipe)
    from moneylab.decision.context import weight_records

    resolved["point_in_time_weights"] = weight_records(weights)
    result = simulate(resolved, data)
    entered = result.decisions[result.decisions["action"] == "enter"]
    bbb = entered[entered["symbol"] == "BBB"]["session"]
    assert not bbb.empty
    assert all(str(session) >= str(join.date()) for session in bbb)
    blocked = result.decisions[
        (result.decisions["symbol"] == "BBB") & (result.decisions["reason"] == "not_in_index")
    ]
    assert not blocked.empty
    draft = FallbackProposer().write_thesis("breakout", budget())
    missing = check_seed(
        card(),
        Thesis(
            claim="Needs the published weights.",
            recipe=draft.recipe,
            control=draft.control,
            falsifier_series=SERIES_ACTIVE,
            requires_historical_weights=True,
        ),
    )
    assert "point_in_time_weights_unavailable" in missing.reasons


def test_matched_cohort_uses_supplied_keys_and_drops_names_without_a_path() -> None:
    dates = pd.to_datetime(["2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"])
    keys = pd.DataFrame(
        {
            "date": list(dates[:1]) * 4 + [dates[1]],
            "symbol": ["AAA", "BBB", "CCC", "DDD", "CCC"],
            "value": [1.0, 1.01, 2.0, 1.02, 1.0],
        }
    )
    bars = pd.DataFrame(
        {
            "date": list(dates) * 3,
            "symbol": ["AAA"] * 4 + ["BBB"] * 4 + ["CCC"] * 4,
            "close": [100, 100, 110, 110, 50, 50, 60, 60, 80, 80, 80, 90],
        }
    )
    # DDD is inside tolerance but has no closes, so it is omitted rather than zeroed.
    rows = matched_cohort(
        [{"symbol": "AAA", "date": "2023-01-03"}],
        keys,
        bars,
        tolerance=0.05,
        hold=2,
    )
    assert rows[0]["matches"][0]["symbol"] == "BBB"
    assert rows[0]["matches"][0]["forward_return"] == pytest.approx(60 / 50 - 1)
    assert [item["symbol"] for item in rows[0]["matches"]] == ["BBB"]
    assert rows[0]["cohort_return"] == pytest.approx(60 / 50 - 1)
    with pytest.raises(ContextDataAbsent):
        matched_cohort(
            [{"symbol": "AAA", "date": "2023-01-03"}],
            None,
            bars,
            tolerance=0.05,
            hold=2,
        )
    draft = FallbackProposer().write_thesis("breakout", budget())
    accepted = check_seed(
        card(),
        Thesis(
            claim="Matched on the supplied key.",
            recipe=draft.recipe,
            match=MatchSpec(key="value", tolerance=0.05, as_of="decision"),
            key_series=keys,
            falsifier_series=SERIES_ACTIVE,
        ),
    )
    assert accepted.accepted
    assert accepted.trials_reserved == 1


def test_walk_forward_fits_on_the_train_window_and_restarts_flat() -> None:
    sessions = pd.bdate_range("2023-01-03", periods=40)
    data = _market(sessions)
    resolved = resolve(_fast_recipe())
    resolved["walk_forward"] = {
        "knob": "exit.holding_period.sessions",
        "grid": [5, 10],
        "train_sessions": 15,
        "test_sessions": 10,
    }
    result = simulate(resolved, data)
    folds = result.meta["walk_forward"]
    assert folds
    first = folds[0]
    assert first["chosen"] == max(
        [5, 10], key=lambda value: (first["train_scores"][str(value)], -float(value))
    )
    assert first["test_start_equity"] == pytest.approx(100_000)
    assert result.meta["cost_bps_per_side"] == 0.0
    sessions_scored = {str(session)[:10] for session in result.equity["session"]}
    assert first["train_start"] not in sessions_scored
    assert first["test_start"] in sessions_scored
    altered = _market(sessions)
    test_start = 15
    for frame in altered.frames.values():
        close = frame["close"].to_numpy(dtype=float).copy()
        opened = frame["open"].to_numpy(dtype=float).copy()
        close[test_start:] = close[test_start] * 3
        opened[test_start:] = close[test_start:]
        frame["close"] = close
        frame["open"] = opened
    again = simulate(resolved, altered)
    assert again.meta["walk_forward"][0]["chosen"] == first["chosen"]
    wide = {**resolved["walk_forward"], "wide": True, "grid": list(range(1, 12))}
    with pytest.raises(ValueError, match="wide walk-forward"):
        simulate({**resolved, "walk_forward": wide}, data)


def test_paper_score_compares_fills_with_the_frozen_model() -> None:
    sessions = pd.bdate_range("2023-01-03", periods=30)
    data = _market(sessions)
    resolved = resolve(_fast_recipe())
    model = simulate(resolved, data)
    same = score_account(resolved, data, model.fills)
    assert same["match"] is True
    assert same["recipe_edited"] is False
    assert same["cost_bps_per_side"] == 0.0
    assert resolved["portfolio"]["cost_bps_per_side"] == 0.0
    bumped = model.fills.copy()
    bumped.loc[bumped.index[0], "price"] = float(bumped.loc[bumped.index[0], "price"]) + 1
    diverged = score_account(resolved, data, bumped)
    assert diverged["match"] is False
    assert diverged["price_diffs"]
    assert resolved["portfolio"]["cost_bps_per_side"] == 0.0


def _market(sessions: pd.DatetimeIndex) -> MarketData:
    frames = {}
    for offset, symbol in enumerate(("AAA", "BBB", "SPY")):
        close = 100.0 + offset + pd.Series(range(len(sessions)), index=sessions, dtype=float)
        frames[symbol] = pd.DataFrame(
            {
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1_000_000,
            },
            index=sessions,
        )
    return MarketData(sessions, frames, {symbol: 0 for symbol in frames})


def _fast_recipe() -> StrategyConfig:
    """A one-session rate of change, so a short window can actually trade."""
    return StrategyConfig(
        name="fast",
        universe=["AAA", "BBB"],
        benchmark="SPY",
        timeframe="1d",
        entry=[
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="roc", params={"length": 1}),
                params={"op": "above", "value": -0.5},
            )
        ],
        exit=ExitRule(kind="holding_period", params={"sessions": 5}),
        portfolio=PortfolioSettings(
            max_positions=5,
            target_allocation=0.2,
            idle_cash="cash",
            starting_cash=100_000,
        ),
    )
