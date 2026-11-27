"""Closed decision rules for one research branch.

The seven closures below are the spec gaps the dry test could not code. Numeric
thresholds stay on the campaign. These rules only say which series, which contrast,
and which end event the thresholds apply to.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Question = Literal["entry", "filter", "exit", "ranking"]

SERIES_ACTIVE = "active_excess_pp"
SERIES_TERMINAL = "terminal_net_excess_pp"
FOLD_FLATTEN = "flatten_and_restart"
QUESTION_ORDER: tuple[Question, ...] = ("filter", "exit", "ranking", "entry")

# Spec §4 context features. Legal recipe inputs only when the component registry
# can evaluate them. This tuple does not add those series to the catalog.
CONTEXT_FEATURES = frozenset(
    {
        "stock_minus_sector",
        "sector_minus_spy",
        "stock_minus_spy",
        "vix_level",
        "vix_change_1",
        "vix_change_5",
        "vix_percentile_252",
    }
)

CLOSURES: dict[str, str] = {
    "one_metric": (
        "The objective, the falsifier, and the paired hurdle all name one series, "
        "in percentage points, higher better, with an explicit comparison such as "
        "greater-than or greater-or-equal. When unused cash stays in cash, that series "
        "is active_excess_pp: the average daily excess over the benchmark on sessions "
        "that were actually invested, so a cash balance cannot kill the claim. When "
        "unused cash is invested in the benchmark, the series is terminal_net_excess_pp, "
        "the terminal net return gap versus the benchmark. A breakout falsifier written "
        "as a net gap versus SPY at or below zero, while cash stays cash, is rejected."
    ),
    "blame_picker": (
        "Among contrasts that clear the uncertainty bar and that the catalog can "
        "execute, the next change is the one with the largest effect. An exact tie "
        "uses the fixed order filter, then exit, then ranking, then entry. A contrast "
        "the catalog cannot express is reported and skipped. The entry question is "
        "skipped once the branch has used its one entry replacement."
    ),
    "selection_end": (
        "Selection ends when no follow-up is recorded, when no authorized experiment "
        "remains, or when the family budget is exhausted. If a qualifier exists, freeze "
        "the winner by the higher objective, then lower turnover, then recipe id. If "
        "none qualifies, park. The record names which of those three events ended it. "
        "A kill from the falsifier or a drawdown breach is decided before this step."
    ),
    "embargo": (
        "Scoring dates for a new experiment exclude decision dates whose forward "
        "outcomes were already in a cited report, and they exclude dates used to fit "
        "the proposal. Fit stays on earlier data. If that leaves no scoring dates, the "
        "experiment is not authorized and does not spend a trial."
    ),
    "control_contract": (
        "A seed control must be a second recipe the catalog can run, with the same "
        "hold, size, slots, costs, cash policy, and dates, differing only in the entry. "
        "A matched cohort is legal only with a match key, a tolerance, an as-of field "
        "of decision, and the key's values on that date. Missing values reject the "
        "cohort. Context features run only when their closes are supplied. A thesis "
        "that needs historical index weights is rejected unless an as-of weight table "
        "is supplied. A catalog rejection does not spend a family trial."
    ),
    "closed_definitions": (
        "Concentration is the largest session's share of the sum of absolute daily "
        "excess returns. Above the owner's maximum the result is inconclusive, even "
        "with many trades. Hard rejection means the drawdown is above the owner's "
        "maximum on a sample that already has enough coverage, and it kills. The "
        "small-batch gate allows an eleventh one-axis variant only when one of the "
        "first ten qualified and budget remains. Fold boundaries flatten and restart "
        "in cash. Carrying positions is rejected. The case sample is the best row, the "
        "worst row, and a seeded draw of the rest, or every row when the side is small."
    ),
    "two_counters": (
        "A failed entry is an entry-axis child with enough coverage that misses the "
        "paired hurdle or trips the falsifier. An inconclusive child does not count "
        "and does not use up the one replacement. One such failure closes the entry "
        "branch. A drawdown kill uses up the replacement without incrementing the "
        "counter. Related campaigns share a disclosed final window when they have the "
        "same universe id and the dates overlap. A new recipe cannot score a window "
        "that was already opened."
    ),
}

ENGINE_LIMITATIONS: tuple[str, ...] = (
    "Matched-cohort controls run only from a supplied match-key series on the decision "
    "date. Absent values are a hard rejection, and no peer return is filled in.",
    "Stock minus sector, sector minus SPY, stock minus SPY, and VIX level, change, and "
    "percentile run only when those closes are supplied. Absent series are a hard rejection.",
    "Point-in-time index weights run only from a supplied as-of table. A missing table "
    "rejects the thesis. Membership is not invented.",
    "Uncertainty intervals are supplied by the report. This slice does not block-bootstrap them.",
    "Walk-forward fitting runs inside the simulator. Each fold fits one declared knob on "
    "the prior window and trades the next window from a flat start.",
    "Paper scoring compares the account's fills with the frozen model's fills on the same "
    "bars. A mismatch does not edit the entry, the filter, or the exit.",
)


def catalog_has(feature_id: str) -> bool:
    """True when the component registry can evaluate this id."""
    import moneylab.components  # noqa: F401  (registers built-ins)
    from moneylab.components.registry import get_component

    try:
        get_component(feature_id)
    except KeyError:
        return False
    return True


def feature_executable(feature_id: str, context: object = None) -> tuple[bool, str]:
    """A context feature runs only when its series was supplied. Otherwise it is rejected."""
    if feature_id in CONTEXT_FEATURES:
        if not catalog_has(feature_id):
            return False, "context_feature_not_in_catalog"
        covers = getattr(context, "covers", None)
        if context is None or not callable(covers) or not covers(feature_id):
            return False, "context_data_absent"
        return True, ""
    if not catalog_has(feature_id):
        return False, "feature_not_in_catalog"
    return True, ""


def relation_holds(value: float, op: str, threshold: float) -> bool:
    if op == "gt":
        return value > threshold
    if op == "gte":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "lte":
        return value <= threshold
    if op == "eq":
        return value == threshold
    raise ValueError(f"unknown comparison {op!r}")


def concentration_share(daily_excess: list[float] | tuple[float, ...]) -> float:
    """Largest session's share of the sum of absolute daily excess returns."""
    magnitudes = [abs(float(x)) for x in daily_excess if x == x]
    total = sum(magnitudes)
    if total <= 0:
        return 0.0
    return max(magnitudes) / total


@dataclass(frozen=True)
class MatchSpec:
    """Match language for a comparable-group control. The engine does not run it."""

    key: str | None = None
    tolerance: float | None = None
    as_of: str | None = None


@dataclass(frozen=True)
class OwnerCard:
    """Thresholds the campaign owner binds before any promotion."""

    series: str
    objective_op: str
    objective_threshold: float
    falsifier_op: str
    falsifier_threshold: float
    paired_op: str
    paired_threshold: float
    uncertainty_low_above: float
    max_drawdown: float
    max_concentration: float | None
    min_distinct_dates: int
    min_folds: int
    family_budget: int
    discovery_budget: int
    universe_id: str
    idle_cash: str
    fold_boundary: str = FOLD_FLATTEN
    case_seed: int = 0
    holdout_start: date | None = None
    holdout_end: date | None = None
    max_initial_variants: int = 10
    unit: str = "percentage_points"
    direction: str = "higher_better"

    def problems(self) -> list[str]:
        bad: list[str] = []
        if self.max_concentration is None:
            bad.append("unset:max_concentration")
        if self.fold_boundary != FOLD_FLATTEN:
            bad.append("fold_boundary")
        if self.idle_cash == "cash" and self.series != SERIES_ACTIVE:
            bad.append("series_not_allowed_for_cash")
        elif self.idle_cash == "benchmark" and self.series != SERIES_TERMINAL:
            bad.append("series_not_allowed_for_benchmark")
        elif self.idle_cash not in {"cash", "benchmark"}:
            bad.append("idle_cash")
        if self.unit != "percentage_points" or self.direction != "higher_better":
            bad.append("metric_unit")
        if self.family_budget < 1 or self.min_distinct_dates < 1 or self.min_folds < 1:
            bad.append("unset:budget_or_coverage")
        start, finish = self.holdout_start, self.holdout_end
        if start is None or finish is None or start > finish:
            bad.append("unset:holdout")
        return bad


@dataclass
class Measurement:
    """One scored book on the bound series. Intervals are inputs, not bootstraps."""

    values: dict[str, float]
    drawdown: float = 0.0
    distinct_dates: int = 0
    folds: int = 1
    scope: str = "development"
    interval_low_pp: float | None = None
    interval_high_pp: float | None = None
    paired_interval_low_pp: float | None = None
    paired_interval_high_pp: float | None = None
    concentration: float | None = None
    turnover: float = 0.0
    exposure: float | None = None
    trades: int = 0
    invalid: bool = False
    invalid_reason: str = ""
    recipe_id: str = ""
    daily_excess: tuple[float, ...] = ()


@dataclass(frozen=True)
class Case:
    opportunity_id: str
    effect: float


@dataclass
class Contrast:
    question: Question
    effect_pp: float
    interval_low_pp: float
    interval_high_pp: float
    distinct_dates: int
    feature_id: str | None = None
    side_a: tuple[Case, ...] = ()
    side_b: tuple[Case, ...] = ()


@dataclass(frozen=True)
class SkippedContrast:
    question: str
    reason: str
    feature_id: str | None = None


@dataclass
class ReadStep:
    kind: str
    detail: dict[str, object]


@dataclass
class ReadReport:
    steps: list[ReadStep]
    selected: Contrast | None
    skipped: list[SkippedContrast]


@dataclass
class Judgment:
    outcome: str
    reasons: list[str]
    qualifier: bool = False
    failed_entry_count: int = 0
    entry_closed: bool = False
    parent_kept: bool = True
    paired_pp: float | None = None


@dataclass(frozen=True)
class Selection:
    outcome: str
    end_record: str
    recipe_id: str | None
    reasons: list[str]


@dataclass
class PaperDecision:
    outcome: str
    queue_paper: bool
    paper_runs: int
    reasons: list[str]
    payload: dict[str, object] = field(default_factory=dict)


@dataclass
class Authorization:
    authorized: bool
    reasons: list[str]
    spends_trial: bool
    trials_spent: int
    scoring_dates: list[date]
    axes: list[str]
    settings_diff: list[dict[str, object]]
    limitations: list[str] = field(default_factory=list)


def bound_series(card: OwnerCard, measurement: Measurement) -> float:
    if card.series not in measurement.values:
        raise KeyError(card.series)
    return measurement.values[card.series]


def _uncertainty_ok(low: float | None, floor: float) -> bool:
    return low is not None and low > floor


def coverage_met(card: OwnerCard, measurement: Measurement) -> bool:
    if measurement.distinct_dates < card.min_distinct_dates:
        return False
    if measurement.scope == "development":
        return measurement.folds >= card.min_folds
    return measurement.folds >= 1


def falsifier_fires(card: OwnerCard, measurement: Measurement) -> bool:
    value = bound_series(card, measurement)
    return relation_holds(value, card.falsifier_op, card.falsifier_threshold)


def hard_rejection(card: OwnerCard, measurement: Measurement) -> str | None:
    """The only hard rejection is a drawdown above the owner's maximum."""
    if measurement.drawdown > card.max_drawdown:
        return "drawdown_breach"
    return None


def concentration_inconclusive(card: OwnerCard, measurement: Measurement) -> bool:
    if card.max_concentration is None or measurement.concentration is None:
        return False
    return measurement.concentration > card.max_concentration


def is_qualifier(card: OwnerCard, measurement: Measurement) -> bool:
    if card.problems() or measurement.invalid:
        return False
    if not coverage_met(card, measurement) or concentration_inconclusive(card, measurement):
        return False
    if falsifier_fires(card, measurement) or hard_rejection(card, measurement):
        return False
    value = bound_series(card, measurement)
    if not relation_holds(value, card.objective_op, card.objective_threshold):
        return False
    return _uncertainty_ok(measurement.interval_low_pp, card.uncertainty_low_above)


def is_supported(card: OwnerCard, contrast: Contrast) -> bool:
    if contrast.distinct_dates < card.min_distinct_dates:
        return False
    if not _uncertainty_ok(contrast.interval_low_pp, card.uncertainty_low_above):
        return False
    return contrast.effect_pp > 0


def pick_blame(
    card: OwnerCard,
    contrasts: list[Contrast],
    *,
    entry_closed: bool,
) -> tuple[Contrast | None, list[SkippedContrast]]:
    eligible: list[Contrast] = []
    skipped: list[SkippedContrast] = []
    for contrast in contrasts:
        if contrast.feature_id is not None:
            ok, why = feature_executable(contrast.feature_id)
            if not ok:
                skipped.append(SkippedContrast(contrast.question, why, contrast.feature_id))
                continue
        if contrast.question == "entry" and entry_closed:
            skipped.append(SkippedContrast(contrast.question, "entry_closed", contrast.feature_id))
            continue
        if not is_supported(card, contrast):
            skipped.append(SkippedContrast(contrast.question, "not_supported", contrast.feature_id))
            continue
        eligible.append(contrast)
    if not eligible:
        return None, skipped
    selected = sorted(eligible, key=lambda c: (-c.effect_pp, QUESTION_ORDER.index(c.question)))[0]
    return selected, skipped


def sample_cases(
    cases: tuple[Case, ...] | list[Case],
    seed: int,
    *,
    take_all_at: int = 5,
    extras: int = 3,
) -> list[str]:
    """Best, worst, and a seeded draw. A small side returns every id."""
    rows = list(cases)
    if len(rows) <= take_all_at:
        return sorted(case.opportunity_id for case in rows)
    ordered = sorted(rows, key=lambda case: (case.effect, case.opportunity_id))
    worst, best = ordered[0], ordered[-1]
    middle = [
        case
        for case in ordered
        if case.opportunity_id not in {best.opportunity_id, worst.opportunity_id}
    ]
    picked = random.Random(seed).sample(middle, min(extras, len(middle)))
    ids = {best.opportunity_id, worst.opportunity_id, *(case.opportunity_id for case in picked)}
    return sorted(ids)


def read_report(
    card: OwnerCard,
    measurement: Measurement,
    contrasts: list[Contrast],
    *,
    kind: str = "portfolio",
    entry_closed: bool = False,
    group_effect_pp: float | None = None,
) -> ReadReport:
    """Headline, then control, then the one contrast the picker chose, then cases."""
    selected, skipped = pick_blame(card, contrasts, entry_closed=entry_closed)
    if kind == "portfolio":
        first = ReadStep(
            "portfolio_vs_benchmark",
            {
                "series": card.series,
                "series_pp": bound_series(card, measurement),
                "drawdown": measurement.drawdown,
                "exposure": measurement.exposure,
            },
        )
    else:
        first = ReadStep(
            "group_difference",
            {"series": card.series, "effect_pp": group_effect_pp},
        )
    steps = [
        first,
        ReadStep(
            "control_and_parent",
            {
                "series_pp": bound_series(card, measurement),
                "interval_low_pp": measurement.interval_low_pp,
                "interval_high_pp": measurement.interval_high_pp,
                "distinct_dates": measurement.distinct_dates,
                "folds": measurement.folds,
            },
        ),
        ReadStep(
            "contrasts",
            {
                "selected": None if selected is None else selected.question,
                "skipped": [item.reason for item in skipped],
            },
        ),
    ]
    if selected is not None:
        steps.append(
            ReadStep(
                "cases",
                {
                    "question": selected.question,
                    "side_a": sample_cases(selected.side_a, card.case_seed),
                    "side_b": sample_cases(selected.side_b, card.case_seed),
                    "seed": card.case_seed,
                },
            )
        )
    return ReadReport(steps, selected, skipped)


def embargo_dates(
    score_dates: list[date],
    cited_outcome_dates: set[date],
    fit_dates: set[date],
) -> tuple[list[date], str | None]:
    """Cited outcome dates are dropped. A proposal that scores a fit date is rejected."""
    if set(score_dates) & set(fit_dates):
        return [], "fit_overlaps_scoring"
    scoring = sorted(day for day in score_dates if day not in cited_outcome_dates)
    if not scoring:
        return [], "embargo_empty"
    return scoring, None


def small_batch_allows(
    variants_done: int,
    qualifiers_among_them: int,
    remaining_budget: int,
    cap: int = 10,
) -> bool:
    """The eleventh one-axis variant needs a qualifier among the first ten, and budget."""
    if remaining_budget < 1:
        return False
    if variants_done < cap:
        return True
    return qualifiers_among_them >= 1


def windows_overlap(
    universe_a: str,
    start_a: date,
    end_a: date,
    universe_b: str,
    start_b: date,
    end_b: date,
) -> bool:
    """Related campaigns share a window when the universe id matches and dates overlap."""
    if universe_a != universe_b:
        return False
    return start_a <= end_b and start_b <= end_a


def tie_break(card: OwnerCard, rows: list[Measurement]) -> Measurement:
    """Higher objective, then lower turnover, then stable recipe id."""
    return sorted(rows, key=lambda row: (-bound_series(card, row), row.turnover, row.recipe_id))[0]


def _entry_after(
    axis: str,
    outcome: str,
    reasons: list[str],
    *,
    coverage: bool,
    failed_entry_count: int,
    entry_closed: bool,
) -> tuple[int, bool]:
    """One conclusive entry replacement. Only a hurdle miss or falsifier increments."""
    if axis != "entry":
        return failed_entry_count, entry_closed
    if outcome in {"inconclusive", "technical"}:
        return failed_entry_count, entry_closed
    if outcome not in {"drop", "kill", "keep"}:
        return failed_entry_count, entry_closed
    closed = True
    count = failed_entry_count
    if coverage and (outcome == "drop" or (outcome == "kill" and "falsifier" in reasons)):
        count += 1
    return count, closed


def judge_seed(card: OwnerCard, measurement: Measurement) -> Judgment:
    """Rules 1-3 and the qualifier flag for a book that has no parent yet."""
    if card.problems():
        return Judgment("blocked", card.problems(), parent_kept=False)
    if measurement.invalid:
        return Judgment("technical", ["technical", measurement.invalid_reason], parent_kept=False)
    if not coverage_met(card, measurement):
        return Judgment("inconclusive", ["inconclusive_coverage"], parent_kept=False)
    if concentration_inconclusive(card, measurement):
        return Judgment(
            "inconclusive",
            ["inconclusive_concentration"],
            parent_kept=False,
        )
    if falsifier_fires(card, measurement):
        return Judgment("kill", ["falsifier"], parent_kept=False)
    breach = hard_rejection(card, measurement)
    if breach:
        return Judgment("kill", [breach], parent_kept=False)
    if is_qualifier(card, measurement):
        return Judgment("qualifier", ["qualifier"], qualifier=True, parent_kept=True)
    return Judgment("alive", ["alive"], parent_kept=True)


def judge_pair(
    card: OwnerCard,
    parent: Measurement,
    child: Measurement,
    *,
    axis: str,
    failed_entry_count: int,
    entry_closed: bool,
) -> Judgment:
    """Keep, drop, kill, or park one child. A rejected child leaves the parent in place."""
    if card.problems():
        return Judgment(
            "blocked",
            card.problems(),
            failed_entry_count=failed_entry_count,
            entry_closed=entry_closed,
        )
    if child.invalid:
        return Judgment(
            "technical",
            ["technical", child.invalid_reason],
            failed_entry_count=failed_entry_count,
            entry_closed=entry_closed,
        )
    coverage = coverage_met(card, child)
    if not coverage:
        return Judgment(
            "inconclusive",
            ["inconclusive_coverage"],
            failed_entry_count=failed_entry_count,
            entry_closed=entry_closed,
        )
    if concentration_inconclusive(card, child):
        return Judgment(
            "inconclusive",
            ["inconclusive_concentration"],
            failed_entry_count=failed_entry_count,
            entry_closed=entry_closed,
        )
    if falsifier_fires(card, child):
        count, closed = _entry_after(
            axis,
            "kill",
            ["falsifier"],
            coverage=True,
            failed_entry_count=failed_entry_count,
            entry_closed=entry_closed,
        )
        return Judgment("kill", ["falsifier"], failed_entry_count=count, entry_closed=closed)
    breach = hard_rejection(card, child)
    if breach:
        count, closed = _entry_after(
            axis,
            "kill",
            [breach],
            coverage=True,
            failed_entry_count=failed_entry_count,
            entry_closed=entry_closed,
        )
        return Judgment("kill", [breach], failed_entry_count=count, entry_closed=closed)

    paired = bound_series(card, child) - bound_series(card, parent)
    reasons: list[str] = []
    if not relation_holds(paired, card.paired_op, card.paired_threshold):
        reasons.append("hurdle_miss")
    if not _uncertainty_ok(child.paired_interval_low_pp, card.uncertainty_low_above):
        reasons.append("uncertainty")
    if reasons:
        count, closed = _entry_after(
            axis,
            "drop",
            reasons,
            coverage=True,
            failed_entry_count=failed_entry_count,
            entry_closed=entry_closed,
        )
        return Judgment(
            "drop",
            reasons,
            failed_entry_count=count,
            entry_closed=closed,
            paired_pp=paired,
        )
    count, closed = _entry_after(
        axis,
        "keep",
        [],
        coverage=True,
        failed_entry_count=failed_entry_count,
        entry_closed=entry_closed,
    )
    qualifier = is_qualifier(card, child)
    keep_reasons = ["keep"]
    if qualifier:
        keep_reasons.append("qualifier")
    return Judgment(
        "keep",
        keep_reasons,
        qualifier=qualifier,
        failed_entry_count=count,
        entry_closed=closed,
        parent_kept=False,
        paired_pp=paired,
    )
