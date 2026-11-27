"""Family-seed loop: validate, read, authorize one change, judge, freeze or paper.

Trial counts change only when a proposal is authorized. A catalog rejection is a
technical failure and spends nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from moneylab.decision.measure import (
    changed_axes,
    execution_signature,
    resolve_recipe,
    seed_jobs,
    settings_diff,
)
from moneylab.decision.rules import (
    ENGINE_LIMITATIONS,
    Authorization,
    Contrast,
    Judgment,
    MatchSpec,
    Measurement,
    OwnerCard,
    PaperDecision,
    ReadReport,
    Selection,
    embargo_dates,
    feature_executable,
    judge_pair,
    judge_seed,
    read_report,
    small_batch_allows,
    tie_break,
    windows_overlap,
)
from moneylab.engine.batch import Job
from moneylab.engine.config import StrategyConfig, config_hash

_MATCH_LIMIT = (
    "A matched cohort needs a match key, a tolerance, and an as-of field of decision. "
    "The engine compares two recipes and has no matched-cohort runner, so this control "
    "is not executed."
)
_WEIGHT_LIMIT = (
    "Point-in-time index weights are not in the engine. The universe is the static "
    "symbol list on the recipe. This thesis is rejected."
)


@dataclass
class Thesis:
    claim: str
    recipe: StrategyConfig
    control: StrategyConfig | None = None
    match: MatchSpec | None = None
    requires_historical_weights: bool = False
    falsifier_series: str | None = None


@dataclass
class SeedCheck:
    accepted: bool
    reasons: list[str]
    spends_trial: bool
    trials_reserved: int
    limitations: list[str]
    jobs: list[Job] = field(default_factory=list)
    resolved_recipe: dict[str, Any] | None = None
    resolved_control: dict[str, Any] | None = None


@dataclass(frozen=True)
class DisclosedWindow:
    universe_id: str
    start: date
    end: date
    recipe_id: str
    outcome: str


class WindowBook:
    """Disclosed final windows, shared by campaigns with the same universe id."""

    def __init__(self) -> None:
        self.windows: list[DisclosedWindow] = []

    def disclose(self, window: DisclosedWindow) -> None:
        self.windows.append(window)

    def conflicting(
        self, universe_id: str, start: date, end: date, recipe_id: str
    ) -> DisclosedWindow | None:
        return self._find(universe_id, start, end, recipe_id, same=False)

    def same_recipe(
        self, universe_id: str, start: date, end: date, recipe_id: str
    ) -> DisclosedWindow | None:
        return self._find(universe_id, start, end, recipe_id, same=True)

    def _find(
        self,
        universe_id: str,
        start: date,
        end: date,
        recipe_id: str,
        *,
        same: bool,
    ) -> DisclosedWindow | None:
        for window in self.windows:
            if (window.recipe_id == recipe_id) != same:
                continue
            overlaps = windows_overlap(
                window.universe_id, window.start, window.end, universe_id, start, end
            )
            if overlaps:
                return window
        return None


def _match_reason(match: MatchSpec) -> str:
    if not match.key or match.tolerance is None or match.tolerance < 0 or match.as_of != "decision":
        return "invalid_match"
    return "matched_cohort_not_executable"


def check_seed(card: OwnerCard, thesis: Thesis) -> SeedCheck:
    """Accept a seed only when the recipe, the control, and the series are executable."""
    problems = card.problems()
    if problems:
        return SeedCheck(False, problems, False, 0, [])
    reasons: list[str] = []
    limitations: list[str] = []
    if thesis.requires_historical_weights:
        reasons.append("point_in_time_weights_unavailable")
        limitations.append(_WEIGHT_LIMIT)
    if thesis.falsifier_series is not None and thesis.falsifier_series != card.series:
        reasons.append("falsifier_series_mismatch")
    if thesis.control is not None and thesis.match is not None:
        reasons.append("ambiguous_control")
    if thesis.control is None and thesis.match is None:
        reasons.append("missing_control")
    if thesis.match is not None:
        reason = _match_reason(thesis.match)
        reasons.append(reason)
        limitations.append(_MATCH_LIMIT)
    try:
        resolved = resolve_recipe(thesis.recipe)
    except (KeyError, ValueError) as exc:
        return SeedCheck(False, [*reasons, "recipe_invalid", str(exc)], False, 0, limitations)
    if resolved["portfolio"]["idle_cash"] != card.idle_cash:
        reasons.append("idle_cash_mismatch")
    resolved_control: dict[str, Any] | None = None
    if thesis.control is not None:
        try:
            resolved_control = resolve_recipe(thesis.control)
        except (KeyError, ValueError) as exc:
            reasons.append("recipe_invalid")
            reasons.append(str(exc))
        else:
            axes = changed_axes(resolved, resolved_control)
            same_book = execution_signature(resolved) == execution_signature(resolved_control)
            if not same_book or (axes and axes != ["entry"]):
                reasons.append("control_not_same_execution")
            elif axes != ["entry"]:
                reasons.append("control_not_different")
    if reasons:
        return SeedCheck(False, reasons, False, 0, limitations)
    assert resolved_control is not None
    return SeedCheck(
        True,
        ["accepted"],
        True,
        2,
        [],
        jobs=seed_jobs(resolved, resolved_control),
        resolved_recipe=resolved,
        resolved_control=resolved_control,
    )


def _base_entry_same(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    return changed_axes(parent, child) == [] or "entry" not in changed_axes(parent, child)


class FamilyLoop:
    """One family branch. Search does not choose the next variant. These rules do."""

    def __init__(self, card: OwnerCard, windows: WindowBook | None = None) -> None:
        self.card = card
        self.windows = windows if windows is not None else WindowBook()
        self.trials_spent = 0
        self.discovery_spent = 0
        self.technical_failures: list[list[str]] = []
        self.failed_entry_count = 0
        self.entry_closed = False
        self.one_axis_done = 0
        self.one_axis_qualifiers = 0
        self.qualifiers: list[Measurement] = []
        self.status = "open"
        self.selection: Selection | None = None
        self.recipe_resolved: dict[str, Any] | None = None
        self.recipe_id = ""
        self.parent_measurement: Measurement | None = None
        self.last_read: ReadReport | None = None
        self.reports: dict[str, set[date]] = {}
        self.reservations: dict[str, tuple[str, list[date]]] = {}
        self.pending_resolved: dict[str, Any] | None = None
        self.pending_axis = ""
        self.paper_runs = 0
        self.paper_outcome = ""
        self.frozen_id = ""

    def submit_seed(self, thesis: Thesis) -> SeedCheck:
        result = check_seed(self.card, thesis)
        if not result.accepted:
            self.technical_failures.append(result.reasons)
            return result
        self.trials_spent += result.trials_reserved
        self.recipe_resolved = result.resolved_recipe
        self.recipe_id = config_hash(result.resolved_recipe or {})
        self.status = "seeded"
        return result

    def read(
        self,
        measurement: Measurement,
        contrasts: list[Contrast],
        *,
        report_id: str,
        outcome_dates: list[date] | None = None,
        kind: str = "portfolio",
        group_effect_pp: float | None = None,
    ) -> ReadReport:
        report = read_report(
            self.card,
            measurement,
            contrasts,
            kind=kind,
            entry_closed=self.entry_closed,
            group_effect_pp=group_effect_pp,
        )
        self.last_read = report
        self.reports[report_id] = set(outcome_dates or [])
        return report

    def judge_current(self, measurement: Measurement, *, fresh_interval: bool = False) -> Judgment:
        measurement.recipe_id = self.recipe_id
        judgment = judge_seed(self.card, measurement)
        self.parent_measurement = measurement
        if judgment.outcome == "kill":
            self.status = "killed"
            self.selection = Selection(
                "kill", judgment.reasons[0], measurement.recipe_id, judgment.reasons
            )
            return judgment
        if judgment.outcome == "inconclusive" and not fresh_interval:
            self.status = "parked"
            self.selection = Selection("park", judgment.reasons[0], None, judgment.reasons)
            return judgment
        if judgment.qualifier:
            self.qualifiers = [measurement]
            self.status = "qualifier"
        else:
            self.qualifiers = []
            self.status = judgment.outcome
        self._end_if_budget()
        return judgment

    def propose(
        self,
        *,
        question: str,
        child: StrategyConfig,
        experiment_id: str,
        cited_reports: list[str],
        score_dates: list[date],
        fit_dates: list[date],
        feature_id: str | None = None,
    ) -> Authorization:
        if self.selection is not None:
            return self._deny(["selection_closed"])
        if experiment_id in self.reservations:
            try:
                resolved_retry = resolve_recipe(child)
            except (KeyError, ValueError) as exc:
                return self._deny(["recipe_invalid", str(exc)], [])
            stored_hash, stored_dates = self.reservations[experiment_id]
            if config_hash(resolved_retry) != stored_hash:
                return self._deny(["experiment_id_reused"], [])
            return Authorization(
                True,
                ["retry"],
                False,
                self.trials_spent,
                stored_dates,
                [self.pending_axis] if self.pending_axis else [],
                [],
            )
        if question == "entry" and self.entry_closed:
            return self._deny(["entry_closed"], [])
        selected = None if self.last_read is None else self.last_read.selected
        if selected is None:
            return self._deny(["no_supported_change"], [])
        if question != selected.question:
            return self._deny(["not_the_selected_change"], [])
        limitations: list[str] = []
        if feature_id is not None:
            ok, why = feature_executable(feature_id)
            if not ok:
                if why == "context_feature_not_in_catalog":
                    limitations.append(ENGINE_LIMITATIONS[1])
                return self._deny([why], limitations)
        for report_id in cited_reports:
            if report_id not in self.reports:
                return self._deny(["unknown_report"], [])
        cited = set().union(*(self.reports[report_id] for report_id in cited_reports))
        scoring, embargo_reason = embargo_dates(score_dates, cited, set(fit_dates))
        if embargo_reason:
            return self._deny([embargo_reason], [])
        remaining = self.card.family_budget - self.trials_spent
        if remaining < 1:
            return self._deny(["budget_exhausted"], [])
        if not small_batch_allows(
            self.one_axis_done,
            self.one_axis_qualifiers,
            remaining,
            self.card.max_initial_variants,
        ):
            return self._deny(["small_batch_gate"], [])
        if self.recipe_resolved is None:
            return self._deny(["no_parent"], [])
        try:
            resolved_child = resolve_recipe(child)
        except (KeyError, ValueError) as exc:
            return self._deny(["recipe_invalid", str(exc)], [])
        axes = changed_axes(self.recipe_resolved, resolved_child)
        diff = settings_diff(self.recipe_resolved, resolved_child)["settings"]
        if len(axes) == 0:
            return self._deny(["no_change"], [], diff)
        if len(axes) > 1:
            return self._deny(["multiple_axes"], [], diff)
        if axes[0] != question:
            return self._deny(["axis_mismatch"], [], diff)
        self.trials_spent += 1
        self.reservations[experiment_id] = (config_hash(resolved_child), scoring)
        self.pending_resolved = resolved_child
        self.pending_axis = axes[0]
        return Authorization(
            True,
            ["authorized"],
            True,
            self.trials_spent,
            scoring,
            axes,
            diff,
            limitations,
        )

    def judge_child(self, measurement: Measurement) -> Judgment:
        if self.parent_measurement is None or self.pending_resolved is None:
            raise RuntimeError("judge_child needs a parent measurement and an authorized child")
        measurement.recipe_id = config_hash(self.pending_resolved)
        judgment = judge_pair(
            self.card,
            self.parent_measurement,
            measurement,
            axis=self.pending_axis,
            failed_entry_count=self.failed_entry_count,
            entry_closed=self.entry_closed,
        )
        self.failed_entry_count = judgment.failed_entry_count
        self.entry_closed = judgment.entry_closed
        if judgment.outcome in {"keep", "drop", "kill"}:
            self.one_axis_done += 1
            if judgment.outcome == "keep" and judgment.qualifier:
                self.one_axis_qualifiers += 1
        if judgment.outcome == "technical":
            self.technical_failures.append(judgment.reasons)
        if judgment.outcome == "keep":
            self.recipe_resolved = self.pending_resolved
            self.recipe_id = measurement.recipe_id
            self.parent_measurement = measurement
            self.qualifiers = [measurement] if judgment.qualifier else []
            self.status = "qualifier" if judgment.qualifier else "alive"
        self.pending_resolved = None
        if judgment.outcome != "technical":
            self._end_if_budget()
        return judgment

    def end(self, record: str) -> Selection:
        if record not in {"no_follow_up", "no_authorized_experiment"}:
            raise ValueError(record)
        if self.selection is not None and self.selection.outcome in {"kill", "freeze", "park"}:
            return self.selection
        return self._close(record)

    def score_holdout(self, measurement: Measurement) -> PaperDecision:
        if self.selection is None or self.selection.outcome != "freeze" or not self.frozen_id:
            return PaperDecision("blocked", False, self.paper_runs, ["not_frozen"])
        start, end = self.card.holdout_start, self.card.holdout_end
        assert start is not None and end is not None
        payload: dict[str, object] = {
            "recipe_hash": self.frozen_id,
            "holdout_start": start.isoformat(),
            "holdout_end": end.isoformat(),
            "universe_id": self.card.universe_id,
            "idle_cash": self.card.idle_cash,
            "series": self.card.series,
            "fold_boundary": self.card.fold_boundary,
        }
        conflict = self.windows.conflicting(self.card.universe_id, start, end, self.frozen_id)
        if conflict is not None:
            return PaperDecision("blocked", False, self.paper_runs, ["holdout_disclosed"], payload)
        prior = self.windows.same_recipe(self.card.universe_id, start, end, self.frozen_id)
        if prior is not None or self.paper_runs:
            outcome = self.paper_outcome or (prior.outcome if prior is not None else "")
            return PaperDecision(outcome, False, self.paper_runs, ["paper_already_queued"], payload)
        holdout = Measurement(
            values=measurement.values,
            drawdown=measurement.drawdown,
            distinct_dates=measurement.distinct_dates,
            folds=measurement.folds,
            scope="holdout",
            interval_low_pp=measurement.interval_low_pp,
            interval_high_pp=measurement.interval_high_pp,
            concentration=measurement.concentration,
            turnover=measurement.turnover,
            exposure=measurement.exposure,
            trades=measurement.trades,
            recipe_id=self.frozen_id,
        )
        judgment = judge_seed(self.card, holdout)
        if judgment.outcome == "inconclusive":
            outcome = "park"
            queue = False
        elif judgment.outcome == "kill":
            outcome = "fail"
            queue = False
            self.status = "closed"
        elif judgment.qualifier:
            outcome = "pass"
            queue = True
            self.paper_runs = 1
            self.status = "paper_queued"
        else:
            outcome = "park"
            queue = False
        self.paper_outcome = outcome
        self.windows.disclose(
            DisclosedWindow(self.card.universe_id, start, end, self.frozen_id, outcome)
        )
        reasons = [outcome, *judgment.reasons]
        return PaperDecision(outcome, queue, self.paper_runs, reasons, payload)

    def _end_if_budget(self) -> None:
        if self.selection is not None:
            return
        if self.trials_spent >= self.card.family_budget:
            self._close("budget_exhausted")

    def _close(self, record: str) -> Selection:
        if self.qualifiers:
            winner = tie_break(self.card, self.qualifiers)
            self.frozen_id = winner.recipe_id or self.recipe_id
            self.status = "frozen"
            self.selection = Selection("freeze", record, self.frozen_id, [record])
        else:
            self.frozen_id = ""
            self.status = "parked"
            self.selection = Selection("park", record, None, [record])
        return self.selection

    def _deny(
        self,
        reasons: list[str],
        limitations: list[str] | None = None,
        diff: list[dict[str, object]] | None = None,
    ) -> Authorization:
        self.technical_failures.append(reasons)
        return Authorization(
            False,
            reasons,
            False,
            self.trials_spent,
            [],
            [],
            diff or [],
            limitations or [],
        )
