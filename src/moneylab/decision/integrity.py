"""Phase-1 research contract: a card is valid before it runs, and a decision names why.

The historical family loop is unchanged. ``judge_seed`` still kills a negative
``active_excess_pp`` with no interval. New research uses ``decide_research``. A stored
legacy judgment is not rewritten.

Costs: new research uses 5 bps per side and the ``research_bps`` model, which is the
path that charges them. A zero-cost replay must say ``legacy_zero_cost``. The settings
default stays 0, and an omitted cost model still charges nothing.

Candidate evidence, outcomes, and the proposer contract live in ``decision.evidence``
and ``decision.proposals``. The Support Recovery V2 card is registered here and is
not run. That run stays in ``DEFERRED``.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal, TypedDict

import numpy as np

from moneylab.components.registry import get_component
from moneylab.decision.measure import changed_axes
from moneylab.decision.proposer import proposer_mode
from moneylab.decision.rules import Judgment
from moneylab.engine.config import (
    Condition,
    EntryFill,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    SizingRule,
    StrategyConfig,
    config_hash,
    resolve,
)
from moneylab.settings import Settings

RESEARCH_COST_BPS = 5.0
STRESS_COST_BPS = {"stress_10": 10.0, "stress_20": 20.0}
COST_LABELS = ("legacy_zero_cost", "research", "stress_10", "stress_20")
DECLARED_AXES = ("entry", "exit", "sizing", "universe", "execution")
KNOWN_FAMILIES = (
    "support_bounce",
    "support_recovery",
    "breakout",
    "trend",
    "rotation",
    "momentum",
)
BOUNCE_FAMILIES = frozenset({"support_bounce", "support_recovery"})
DEFERRED = ("V2 support-recovery run",)
SUPPORT_RECOVERY_V2_STATUS = "registered_not_run"
REPORTED_V0_ACTIVE_EXCESS_PP = -0.00346

DecisionState = Literal["INVALID", "PARK", "DROP", "KILL", "KEEP", "FREEZE"]
_TERMINAL = frozenset({"INVALID", "KILL", "FREEZE"})
_FINE_TO_DECLARED = {
    "entry": "entry",
    "filter": "entry",
    "exit": "exit",
    "sizing": "sizing",
    "capacity": "sizing",
    "capital": "sizing",
    "universe": "universe",
    "fill": "execution",
    "costs": "execution",
    "ranking": "execution",
    "cooldown": "execution",
    "frozen_levels": "execution",
    "cash": "cash",
    "benchmark": "benchmark",
}
_LEVEL = {"lookback": 50, "min_move": 2.0, "zone": 1.0, "atr_length": 14}
_PROSE = {
    "numeric_cross": re.compile(r"\bcross(?:es|ing|ed)?\b", re.IGNORECASE),
    "cooldown": re.compile(r"\bcooldown\b", re.IGNORECASE),
    "inclusive": re.compile(r"\binclusive\b|\bat or above\b|\bat or below\b", re.IGNORECASE),
    "frozen_stop": re.compile(r"\bfrozen\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class EvidenceFloor:
    """Floors bound before a run. A zero crossing of the point estimate is not a decision."""

    delta_pp: float
    min_events: int
    min_folds: int
    confidence: float = 0.95
    block_length: int = 1
    horizon_bars: int = 1
    timeframe: str = "1d"


@dataclass(frozen=True)
class Partition:
    name: str
    start: date
    end: date
    role: str
    contaminated: bool = False
    contamination_reason: str = ""


@dataclass(frozen=True)
class ApprovedApproximation:
    """A versioned stand-in for a capability the recipe does not actually express."""

    capability: str
    version: str
    statement: str


@dataclass(frozen=True)
class ResearchCard:
    family: str
    behavior: str
    observable_event: str
    eligibility: str
    treatment: StrategyConfig
    control: StrategyConfig
    target_estimand: str
    horizon: str
    timeframe: str
    cost_label: str
    falsifier: str
    falsifier_series: str
    falsifier_op: str
    falsifier_level: float
    uncertainty_method: str
    minimum_evidence: EvidenceFloor
    trial_budget: int
    partitions: tuple[Partition, ...]
    claims: tuple[str, ...] = ()
    approximations: tuple[ApprovedApproximation, ...] = ()
    compound_seed: bool = False
    approve_cash_policy: bool = False
    declared_axis: str | None = None


@dataclass(frozen=True)
class ContractResult:
    ok: bool
    reasons: tuple[str, ...]
    approximations: tuple[ApprovedApproximation, ...]
    cost_bps_per_side: float | None
    cost_label: str
    capabilities: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AxisAuthorization:
    authorized: bool
    reasons: tuple[str, ...]
    axes: tuple[str, ...]
    declared_axis: str
    spends_trial: bool


@dataclass(frozen=True)
class ResearchDecision:
    state: DecisionState
    reasons: tuple[str, ...]
    scope: str
    point_pp: float | None = None
    interval_low_pp: float | None = None
    interval_high_pp: float | None = None
    assumptions: tuple[str, ...] = ()
    effective_sample: float | None = None
    method: str = ""


@dataclass(frozen=True)
class UncertaintyInterval:
    low_pp: float
    high_pp: float
    point_pp: float
    method: str
    block_length: int
    draws: int
    confidence: float
    effective_sample: float
    seed: int
    assumptions: tuple[str, ...]


def registry_capabilities() -> dict[str, Any]:
    """What a research card can ask for without silently dropping a condition."""
    threshold = get_component("threshold")
    ops = tuple(threshold.param("op").choices)
    return {
        "threshold_version": threshold.version,
        "threshold_ops": ops,
        "numeric_threshold_cross": "crosses_above" in ops and "crosses_below" in ops,
        "inclusive_threshold": "at_or_above" in ops and "at_or_below" in ops,
        "indicator_cross": "two indicators only; a numeric level uses threshold crosses_*",
        "cooldown_bars": "charged only when portfolio.cooldown_bars is set",
        "frozen_entry_levels": (
            "portfolio.freeze_entry_levels stores the entry decision's finite values; "
            "exit rules still read live indicators, so a frozen stop needs an approximation"
        ),
        "cost_model": (
            "omitted or legacy_unapplied charges nothing; research_bps charges cost_bps_per_side"
        ),
        "default_cost_bps_per_side": 0.0,
        "research_cost_bps_per_side": RESEARCH_COST_BPS,
        "deferred": DEFERRED,
    }


def _resolve(config: StrategyConfig) -> dict[str, Any]:
    """Resolve from the card alone. The machine's ``.env`` is not read."""
    return resolve(config, Settings(_env_file=None))


def bind_cost(config: StrategyConfig, label: str) -> StrategyConfig:
    """Copy a config onto a labeled cost path. This does not change ``Settings``."""
    if label == "legacy_zero_cost":
        bps: float = 0.0
        model = "legacy_unapplied"
    elif label == "research":
        bps = RESEARCH_COST_BPS
        model = "research_bps"
    elif label in STRESS_COST_BPS:
        bps = STRESS_COST_BPS[label]
        model = "research_bps"
    else:
        raise ValueError(f"unknown cost label {label!r}")
    portfolio = config.portfolio.model_copy(update={"cost_bps_per_side": bps, "cost_model": model})
    return config.model_copy(update={"portfolio": portfolio})


def proposer_status() -> dict[str, str]:
    """Fallback or model. The key itself is never included."""
    mode = proposer_mode()
    return {
        "mode": mode,
        "labeled": "fallback" if mode == "fallback" else "model",
    }


def recovery_swing_partitions() -> tuple[Partition, ...]:
    """The windows from the 27 Nov 2026 run. 2025 was scored and is contaminated."""
    return (
        Partition("discovery", date(2020, 1, 1), date(2023, 12, 31), "development"),
        Partition("validation", date(2024, 1, 1), date(2024, 12, 31), "development"),
        Partition(
            "holdout",
            date(2025, 1, 1),
            date(2025, 12, 31),
            "holdout",
            True,
            "viewed_in_recovery_swing_run_2026-11-27",
        ),
    )


def _threshold(
    indicator: str, op: str, value: float, params: dict[str, Any] | None = None
) -> Condition:
    return Condition(
        kind="threshold",
        indicator=IndicatorRef(id=indicator, params=params or {}),
        params={"op": op, "value": value},
    )


def _recovery_book(*, legacy: bool) -> dict[str, Any]:
    label = "legacy_zero_cost" if legacy else "research"
    portfolio = bind_cost(
        StrategyConfig(
            name="book",
            universe=["NVDA"],
            benchmark="SPY",
            entry=[_threshold("roc", "above", 0, {"length": 1})],
            portfolio=PortfolioSettings(
                starting_cash=100_000,
                max_positions=10,
                target_allocation=0.10,
                idle_cash="cash",
            ),
            sizing=SizingRule(id="fixed_dollar", params={"amount": 10_000}),
        ),
        label,
    ).portfolio
    return {
        "universe": ["NVDA"],
        "benchmark": "SPY",
        "timeframe": "1h",
        "portfolio": portfolio,
        "sizing": SizingRule(id="fixed_dollar", params={"amount": 10_000}),
    }


def legacy_recovery_configs() -> dict[str, StrategyConfig]:
    """The reported V0, ROC control, and V1 books at zero cost, labeled as a replay.

    This does not re-simulate the Equibles book. The kill number is the reported
    discovery ``active_excess_pp``.
    """
    book = _recovery_book(legacy=True)
    hold = ExitRule(kind="holding_period", params={"sessions": 65})
    v0_entry = [
        _threshold("support_distance", "above", 0, _LEVEL),
        _threshold("roc", "above", 0, {"length": 1}),
    ]
    control_entry = [_threshold("roc", "above", 0, {"length": 1})]
    v1_entry = [
        _threshold("rsi", "above", 40, {"length": 14}),
        _threshold("support_distance", "above", 0, _LEVEL),
        _threshold("support_distance", "below", 0.75, _LEVEL),
        _threshold("support_tests", "above", 1, _LEVEL),
        _threshold("resistance_distance", "above", 2, _LEVEL),
        _threshold(
            "macd",
            "above",
            0,
            {"fast": 12, "slow": 26, "signal": 9, "output": "histogram"},
        ),
    ]
    v1_exits = [
        ExitRule(
            kind="signal",
            conditions=[
                _threshold("resistance_distance", "below", 0.25, _LEVEL),
                _threshold("support_distance", "below", -0.5, _LEVEL),
            ],
        ),
        hold,
    ]
    return {
        "v0": StrategyConfig(name="recovery v0", entry=v0_entry, exit=hold, **book),
        "control": StrategyConfig(name="roc control", entry=control_entry, exit=hold, **book),
        "v1": StrategyConfig(name="recovery v1", entry=v1_entry, exits=v1_exits, **book),
    }


def _iter_conditions(config: StrategyConfig) -> list[Condition]:
    found = list(config.entry) + list(config.entry_signals)
    for rule in config.exit_rules():
        found.extend(rule.conditions)
    return found


def _features(config: StrategyConfig) -> set[str]:
    found: set[str] = set()
    for cond in _iter_conditions(config):
        op = str(cond.params.get("op", ""))
        if op in {"crosses_above", "crosses_below"}:
            found.add("numeric_cross")
        if op in {"at_or_above", "at_or_below"}:
            found.add("inclusive")
    if config.portfolio.cooldown_bars:
        found.add("cooldown")
    if config.portfolio.freeze_entry_levels:
        found.add("frozen_levels")
    return found


def _requested(card: ResearchCard) -> set[str]:
    text = " ".join((card.observable_event, card.eligibility, card.behavior, card.horizon))
    found = set(card.claims)
    for name, pattern in _PROSE.items():
        if pattern.search(text):
            found.add(name)
    return found


def _approval(card: ResearchCard, capability: str) -> ApprovedApproximation | None:
    for item in card.approximations:
        if item.capability == capability and item.statement.strip() and item.version.strip():
            return item
    return None


def _support_event(config: StrategyConfig) -> str:
    lower = False
    upper = False
    touch = False
    for cond in _iter_conditions(config):
        if cond.indicator.id != "support_distance":
            continue
        op = str(cond.params.get("op", ""))
        try:
            value = float(cond.params.get("value", 0))
        except (TypeError, ValueError):
            continue
        if op in {"crosses_above", "crosses_below"}:
            touch = True
        if op in {"above", "at_or_above"} and value <= 0:
            lower = True
        if op in {"below", "at_or_below"} and math.isfinite(value) and value > 0:
            upper = True
    if touch or (lower and upper):
        return ""
    return "unbounded_support"


def _partition_problems(parts: tuple[Partition, ...]) -> list[str]:
    names = [part.name for part in parts]
    if set(names) != {"discovery", "validation", "holdout"} or len(names) != 3:
        return ["partitions_unregistered"]
    by_name = {part.name: part for part in parts}
    reasons: list[str] = []
    for name in ("discovery", "validation"):
        if by_name[name].role != "development":
            reasons.append(f"{name}_role")
    if by_name["holdout"].role != "holdout":
        reasons.append("holdout_role")
    for part in parts:
        if part.start > part.end:
            reasons.append(f"{part.name}_dates")
    discovery, validation, holdout = by_name["discovery"], by_name["validation"], by_name["holdout"]
    if not (discovery.end < validation.start and validation.end < holdout.start):
        reasons.append("partitions_overlap_or_unordered")
    return reasons


def _cost_problems(card: ResearchCard) -> list[str]:
    reasons: list[str] = []
    if card.cost_label not in COST_LABELS:
        return ["cost_label"]
    books = (card.treatment.portfolio, card.control.portfolio)
    bps = {book.cost_bps_per_side for book in books}
    models = {book.cost_model for book in books}
    if len(bps) != 1 or len(models) != 1:
        reasons.append("cost_mismatch")
        return reasons
    cost = books[0].cost_bps_per_side
    model = books[0].cost_model
    if card.cost_label == "legacy_zero_cost":
        if cost != 0 or model != "legacy_unapplied":
            reasons.append("legacy_cost_unlabeled")
    elif card.cost_label == "research":
        if cost != RESEARCH_COST_BPS or model != "research_bps":
            reasons.append("research_cost")
    else:
        expected = STRESS_COST_BPS[card.cost_label]
        if cost != expected or model != "research_bps":
            reasons.append("stress_cost")
    if cost == 0 and card.cost_label != "legacy_zero_cost":
        reasons.append("zero_cost_unlabeled")
    return reasons


def validate_card(card: ResearchCard) -> ContractResult:
    """Check the card before any fetch or run. A failure spends no trial."""
    reasons: list[str] = []
    text_fields = {
        "family": card.family,
        "behavior": card.behavior,
        "observable_event": card.observable_event,
        "eligibility": card.eligibility,
        "target_estimand": card.target_estimand,
        "horizon": card.horizon,
        "falsifier": card.falsifier,
        "uncertainty_method": card.uncertainty_method,
    }
    for name, value in text_fields.items():
        if not str(value).strip():
            reasons.append(f"missing_{name}")
    if card.family not in KNOWN_FAMILIES:
        reasons.append("unknown_family")
    if card.treatment.timeframe != card.timeframe or card.control.timeframe != card.timeframe:
        reasons.append("timeframe_mismatch")
    if card.trial_budget < 1:
        reasons.append("trial_budget")
    floor = card.minimum_evidence
    if floor.min_events < 1 or floor.min_folds < 1:
        reasons.append("evidence_floor")
    if not 0 < floor.confidence < 1 or floor.block_length < 1 or floor.horizon_bars < 1:
        reasons.append("uncertainty_floor")
    if floor.timeframe != card.timeframe:
        reasons.append("uncertainty_timeframe")
    if floor.horizon_bars > 1 and floor.block_length < floor.horizon_bars:
        reasons.append("block_shorter_than_horizon")
    if card.falsifier_series not in {"active_excess_pp", "active_excess_pp_v2"}:
        reasons.append("falsifier_series")
    reasons.extend(_partition_problems(card.partitions))
    reasons.extend(_cost_problems(card))
    if card.family in BOUNCE_FAMILIES:
        bounce = _support_event(card.treatment)
        if bounce:
            reasons.append(bounce)
    parent: dict[str, Any] | None
    control: dict[str, Any] | None
    try:
        parent = _resolve(card.treatment)
        control = _resolve(card.control)
    except (KeyError, ValueError) as exc:
        reasons.append(f"recipe_invalid:{exc}")
        parent = None
        control = None
    if parent is not None and control is not None and not card.compound_seed:
        groups = {_FINE_TO_DECLARED.get(axis, axis) for axis in changed_axes(control, parent)}
        if groups - {"entry"}:
            reasons.append("control_not_isolated")
        if not groups:
            reasons.append("control_not_different")
    visible: list[ApprovedApproximation] = []
    if parent is not None:
        present = _features(card.treatment) | _features(card.control)
        for capability in sorted(_requested(card)):
            if capability in present:
                continue
            approved = _approval(card, capability)
            if approved is None:
                reasons.append(f"unsupported_{capability}")
            else:
                visible.append(approved)
    capabilities = tuple(sorted(registry_capabilities()["threshold_ops"]))
    cost = card.treatment.portfolio.cost_bps_per_side
    return ContractResult(
        ok=not reasons,
        reasons=tuple(reasons),
        approximations=tuple(visible),
        cost_bps_per_side=None if cost is None else float(cost),
        cost_label=card.cost_label,
        capabilities=tuple(("threshold_op", op) for op in capabilities),
    )


def authorize_one_axis(
    parent: dict[str, Any],
    child: dict[str, Any],
    declared_axis: str,
    *,
    approve_cash_policy: bool = False,
) -> AxisAuthorization:
    """One declared axis. Cash policy and a second axis are rejected before a trial is spent."""
    if declared_axis not in DECLARED_AXES:
        return AxisAuthorization(False, ("unknown_axis",), (), declared_axis, False)
    fine = changed_axes(parent, child)
    if not fine:
        return AxisAuthorization(False, ("no_change",), (), declared_axis, False)
    reasons: list[str] = []
    cash_only = fine == ["cash"]
    cash_approved = approve_cash_policy and declared_axis == "execution" and cash_only
    if "cash" in fine and not cash_approved:
        reasons.append("cash_policy_change")
    if "benchmark" in fine:
        reasons.append("unapproved_benchmark")
    groups = {_FINE_TO_DECLARED.get(axis, axis) for axis in fine}
    groups -= {"cash", "benchmark"}
    if len(groups) > 1:
        reasons.append("multiple_axes")
    elif len(groups) == 1 and declared_axis not in groups:
        reasons.append("axis_mismatch")
    if reasons:
        return AxisAuthorization(False, tuple(reasons), tuple(fine), declared_axis, False)
    return AxisAuthorization(True, ("authorized",), tuple(fine), declared_axis, True)


def _falsifier_supported(low: float, high: float, op: str, level: float) -> bool:
    if op == "lte":
        return high <= level
    if op == "lt":
        return high < level
    if op == "gte":
        return low >= level
    if op == "gt":
        return low > level
    if op == "eq":
        return low == high == level
    raise ValueError(f"unknown comparison {op!r}")


class _Shared(TypedDict):
    scope: str
    point_pp: float | None
    interval_low_pp: float | None
    interval_high_pp: float | None
    assumptions: tuple[str, ...]
    effective_sample: float | None
    method: str


def _decision(
    state: DecisionState,
    reasons: tuple[str, ...],
    *,
    scope: str,
    point_pp: float | None,
    interval_low_pp: float | None,
    interval_high_pp: float | None,
    assumptions: tuple[str, ...],
    effective_sample: float | None,
    method: str,
) -> ResearchDecision:
    return ResearchDecision(
        state,
        reasons,
        scope,
        point_pp,
        interval_low_pp,
        interval_high_pp,
        assumptions,
        effective_sample,
        method,
    )


def decide_research(
    *,
    contract_ok: bool,
    contract_reasons: tuple[str, ...] = (),
    scope: str,
    point_pp: float | None,
    interval_low_pp: float | None,
    interval_high_pp: float | None,
    delta_pp: float,
    events: int,
    folds: int,
    floor: EvidenceFloor,
    falsifier_op: str = "lte",
    falsifier_level: float = 0.0,
    assumptions: tuple[str, ...] = (),
    effective_sample: float | None = None,
    method: str = "",
) -> ResearchDecision:
    """INVALID, PARK, DROP, KILL, or KEEP. FREEZE is a later step on a KEEP."""
    shared: _Shared = {
        "scope": scope,
        "point_pp": point_pp,
        "interval_low_pp": interval_low_pp,
        "interval_high_pp": interval_high_pp,
        "assumptions": assumptions,
        "effective_sample": effective_sample,
        "method": method,
    }
    if scope not in {"family", "child"}:
        return _decision("INVALID", ("unknown_scope",), **shared)
    if not contract_ok:
        return _decision("INVALID", contract_reasons or ("invalid_contract",), **shared)
    if any(item.startswith("assumption_violated") for item in assumptions):
        return _decision("PARK", ("assumption_violated",), **shared)
    sample_short = (
        events < floor.min_events
        or folds < floor.min_folds
        or (effective_sample is not None and effective_sample < floor.min_events)
    )
    if sample_short:
        return _decision("PARK", ("insufficient_sample",), **shared)
    if point_pp is None or interval_low_pp is None or interval_high_pp is None:
        return _decision("PARK", ("interval_absent",), **shared)
    if scope == "family" and _falsifier_supported(
        interval_low_pp, interval_high_pp, falsifier_op, falsifier_level
    ):
        return _decision("KILL", ("falsifier",), **shared)
    if interval_low_pp > delta_pp:
        return _decision("KEEP", ("supported_improvement",), **shared)
    if interval_high_pp <= delta_pp:
        if scope == "child":
            return _decision("DROP", ("hurdle_miss",), **shared)
        return _decision("PARK", ("no_supported_benefit",), **shared)
    return _decision("PARK", ("interval_covers_delta",), **shared)


def moving_block_interval(
    values: np.ndarray,
    *,
    block_length: int,
    seed: int,
    draws: int = 400,
    confidence: float = 0.95,
    horizon_bars: int = 1,
    timeframe: str = "1d",
) -> UncertaintyInterval:
    """Percentile interval for the mean from a circular moving-block bootstrap.

    The generator is local to ``seed``. Overlapping bars stay inside blocks. A block
    shorter than the outcome horizon is reported as an assumption violation; the
    decision layer parks that interval instead of treating the bars as independent.
    """
    sample = np.asarray(values, dtype=float)
    sample = sample[np.isfinite(sample)]
    if sample.size < 2:
        raise ValueError("uncertainty needs at least two finite observations")
    if block_length < 1 or draws < 1 or not 0 < confidence < 1:
        raise ValueError("uncertainty settings are out of range")
    n = int(sample.size)
    rng = np.random.Generator(np.random.PCG64(seed))
    n_blocks = math.ceil(n / block_length)
    means = np.empty(draws, dtype=float)
    for draw in range(draws):
        starts = rng.integers(0, n, size=n_blocks)
        chunks: list[np.ndarray] = []
        for start in starts:
            start_i = int(start)
            stop = start_i + block_length
            if stop <= n:
                chunks.append(sample[start_i:stop])
            else:
                chunks.append(np.concatenate([sample[start_i:], sample[: stop - n]]))
        means[draw] = np.concatenate(chunks)[:n].mean()
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(means, [alpha, 1.0 - alpha])
    assumptions = [
        "circular moving-block bootstrap of the mean",
        f"block length {block_length}; observations inside a block stay together",
        "resampled blocks are not treated as independent bars",
        f"{confidence:.0%} percentile interval",
        f"timeframe {timeframe}",
    ]
    if horizon_bars > 1 and block_length < horizon_bars:
        assumptions.append("assumption_violated: block shorter than the outcome horizon")
    return UncertaintyInterval(
        low_pp=float(low),
        high_pp=float(high),
        point_pp=float(sample.mean()),
        method="moving_block_bootstrap",
        block_length=block_length,
        draws=draws,
        confidence=confidence,
        effective_sample=n / block_length,
        seed=seed,
        assumptions=tuple(assumptions),
    )


def redact_for_proposer(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop holdout rows. A proposer does not receive holdout labels."""
    kept: list[dict[str, Any]] = []
    for row in rows:
        if row.get("partition") == "holdout" or row.get("role") == "holdout":
            continue
        kept.append(dict(row))
    return kept


@dataclass
class HoldoutGate:
    """Holdout reads fail closed until a frozen manifest exists, and a viewed slice is marked."""

    partitions: dict[str, Partition]
    frozen_manifest: dict[str, Any] | None = None
    frozen_hash: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_partitions(cls, parts: tuple[Partition, ...]) -> HoldoutGate:
        return cls({part.name: part for part in parts})

    def freeze(self, resolved: dict[str, Any]) -> str:
        digest = config_hash(resolved)
        if self.frozen_hash:
            if digest != self.frozen_hash:
                raise ValueError("frozen manifest is immutable")
            return self.frozen_hash
        self.frozen_manifest = copy.deepcopy(resolved)
        self.frozen_hash = digest
        return digest

    def request(
        self,
        name: str,
        purpose: str,
        *,
        loader: Callable[[], object] | None = None,
    ) -> dict[str, Any]:
        """Authorize one read. ``loader`` runs only after the gate allows it."""
        part = self.partitions[name]
        holdout = part.role == "holdout"
        if not holdout:
            reason = "development"
            allowed = True
        elif purpose == "proposer":
            reason = "proposer_holdout_blocked"
            allowed = False
        elif part.contaminated:
            reason = "contaminated"
            allowed = False
        elif not self.frozen_hash:
            reason = "not_frozen"
            allowed = False
        else:
            reason = "frozen_authorized"
            allowed = True
        self.events.append(
            {"partition": name, "purpose": purpose, "allowed": allowed, "reason": reason}
        )
        if not allowed:
            return {"allowed": False, "reason": reason, "loaded": False, "value": None}
        value = loader() if loader is not None else None
        if holdout:
            self.partitions[name] = replace(
                part, contaminated=True, contamination_reason="inspected_after_freeze"
            )
        return {"allowed": True, "reason": reason, "loaded": loader is not None, "value": value}


class ResearchBranch:
    """One new branch. A terminal state does not reopen, and a legacy kill stays stored."""

    def __init__(self, card: ResearchCard) -> None:
        self.card = card
        self.contract = validate_card(card)
        self.trials_spent = 0
        self.decision: ResearchDecision | None = None
        self.legacy: Judgment | None = None
        self.gate = HoldoutGate.from_partitions(card.partitions)
        self.terminal = not self.contract.ok

    def record_legacy(self, judgment: Judgment) -> None:
        """Keep a historical judgment. Later research decisions do not replace this object."""
        self.legacy = judgment

    def judge(
        self,
        *,
        scope: str,
        point_pp: float | None,
        interval_low_pp: float | None,
        interval_high_pp: float | None,
        events: int,
        folds: int,
        assumptions: tuple[str, ...] = (),
        effective_sample: float | None = None,
        method: str = "",
    ) -> ResearchDecision:
        decision = decide_research(
            contract_ok=self.contract.ok,
            contract_reasons=self.contract.reasons,
            scope=scope,
            point_pp=point_pp,
            interval_low_pp=interval_low_pp,
            interval_high_pp=interval_high_pp,
            delta_pp=self.card.minimum_evidence.delta_pp,
            events=events,
            folds=folds,
            floor=self.card.minimum_evidence,
            falsifier_op=self.card.falsifier_op,
            falsifier_level=self.card.falsifier_level,
            assumptions=assumptions,
            effective_sample=effective_sample,
            method=method,
        )
        self.decision = decision
        if decision.state in _TERMINAL:
            self.terminal = True
        return decision

    def propose_child(
        self,
        child: StrategyConfig,
        *,
        evidence_refs: tuple[str, ...],
        declared_axis: str | None = None,
    ) -> AxisAuthorization:
        axis = declared_axis or self.card.declared_axis or ""
        if self.terminal or (self.decision is not None and self.decision.state in _TERMINAL):
            return AxisAuthorization(False, ("terminal",), (), axis, False)
        if not evidence_refs:
            return AxisAuthorization(False, ("no_evidence",), (), axis, False)
        if not self.contract.ok:
            return AxisAuthorization(False, ("invalid_contract",), (), axis, False)
        try:
            parent = _resolve(self.card.treatment)
            resolved_child = _resolve(child)
        except (KeyError, ValueError) as exc:
            return AxisAuthorization(False, (f"recipe_invalid:{exc}",), (), axis, False)
        auth = authorize_one_axis(
            parent,
            resolved_child,
            axis,
            approve_cash_policy=self.card.approve_cash_policy,
        )
        if not auth.authorized:
            return auth
        self.trials_spent += 1
        return AxisAuthorization(True, auth.reasons, auth.axes, axis, True)

    def freeze_manifest(self) -> ResearchDecision:
        """Freeze only a KEEP that already cleared the sample floor. The manifest is copied."""
        if self.decision is None or self.decision.state != "KEEP":
            decision = ResearchDecision(
                "PARK",
                ("cannot_freeze",),
                "family",
                None if self.decision is None else self.decision.point_pp,
            )
            self.decision = decision
            return decision
        if self.decision.point_pp is None:
            return self.decision
        resolved = _resolve(self.card.treatment)
        try:
            self.gate.freeze(resolved)
        except ValueError:
            return ResearchDecision("FREEZE", ("manifest_immutable",), self.decision.scope)
        decision = ResearchDecision(
            "FREEZE",
            ("frozen",),
            self.decision.scope,
            self.decision.point_pp,
            self.decision.interval_low_pp,
            self.decision.interval_high_pp,
            self.decision.assumptions,
            self.decision.effective_sample,
            self.decision.method,
        )
        self.decision = decision
        self.terminal = True
        return decision


def example_research_card() -> ResearchCard:
    """A bounded support-bounce card on the 5 bps research path. It is not a backtest."""
    book = _recovery_book(legacy=False)
    book["timeframe"] = "1d"
    hold = ExitRule(kind="holding_period", params={"sessions": 65})
    treatment = StrategyConfig(
        name="bounded support example",
        entry=[
            _threshold("roc", "above", 0, {"length": 1}),
            _threshold("support_distance", "above", 0, _LEVEL),
            _threshold("support_distance", "below", 1, _LEVEL),
        ],
        exit=hold,
        **book,
    )
    control = StrategyConfig(
        name="roc only example",
        entry=[_threshold("roc", "above", 0, {"length": 1})],
        exit=hold,
        **book,
    )
    floor = EvidenceFloor(
        delta_pp=0.0,
        min_events=30,
        min_folds=3,
        confidence=0.95,
        block_length=65,
        horizon_bars=65,
        timeframe="1d",
    )
    partitions = (
        Partition("discovery", date(2020, 1, 1), date(2023, 12, 31), "development"),
        Partition("validation", date(2024, 1, 1), date(2024, 12, 31), "development"),
        Partition("holdout", date(2026, 1, 1), date(2026, 12, 31), "holdout"),
    )
    return ResearchCard(
        family="support_bounce",
        behavior="Positive daily momentum near established support.",
        observable_event="ROC(1) above 0 and support distance above 0 and below 1 ATR.",
        eligibility="NVDA, one position, next open, fixed $10,000.",
        treatment=treatment,
        control=control,
        target_estimand="paired event net forward return versus the ROC-only control",
        horizon="65 bars",
        timeframe="1d",
        cost_label="research",
        falsifier="Active excess versus the benchmark is not positive.",
        falsifier_series="active_excess_pp",
        falsifier_op="lte",
        falsifier_level=0.0,
        uncertainty_method="moving_block_bootstrap",
        minimum_evidence=floor,
        trial_budget=3,
        partitions=partitions,
        declared_axis="entry",
    )


def support_recovery_v2_card() -> ResearchCard:
    """The registered Support Recovery V2 card. Building it does not load or score bars.

    Control is ROC(1) > 0. Treatment adds support distance > 0 and < 1 ATR, using the
    report's level defaults. Both books are hourly NVDA, a 65-bar hold, next-open
    fills, a $10,000 position, and $100,000 of cash. 2025 is not a partition: that
    year was already viewed. The holdout slot is 2026 and is not read here.
    """
    portfolio = bind_cost(
        StrategyConfig(
            name="book",
            universe=["NVDA"],
            benchmark="SPY",
            entry=[_threshold("roc", "above", 0, {"length": 1})],
            portfolio=PortfolioSettings(
                starting_cash=100_000,
                max_positions=1,
                target_allocation=0.10,
                idle_cash="cash",
            ),
            sizing=SizingRule(id="fixed_dollar", params={"amount": 10_000}),
        ),
        "research",
    ).portfolio
    book: dict[str, Any] = {
        "universe": ["NVDA"],
        "benchmark": "SPY",
        "timeframe": "1h",
        "portfolio": portfolio,
        "sizing": SizingRule(id="fixed_dollar", params={"amount": 10_000}),
        "entry_fill": EntryFill(kind="next_open"),
    }
    hold = ExitRule(kind="holding_period", params={"sessions": 65})
    control = StrategyConfig(
        name="Support Recovery V2 control",
        entry=[_threshold("roc", "above", 0, {"length": 1})],
        exit=hold,
        **book,
    )
    treatment = StrategyConfig(
        name="Support Recovery V2",
        entry=[
            _threshold("roc", "above", 0, {"length": 1}),
            _threshold("support_distance", "above", 0, _LEVEL),
            _threshold("support_distance", "below", 1, _LEVEL),
        ],
        exit=hold,
        **book,
    )
    floor = EvidenceFloor(
        delta_pp=0.0,
        min_events=30,
        min_folds=3,
        confidence=0.95,
        block_length=65,
        horizon_bars=65,
        timeframe="1h",
    )
    partitions = (
        Partition("discovery", date(2020, 1, 1), date(2023, 12, 31), "development"),
        Partition("validation", date(2024, 1, 1), date(2024, 12, 31), "development"),
        Partition("holdout", date(2026, 1, 1), date(2026, 12, 31), "holdout"),
    )
    return ResearchCard(
        family="support_recovery",
        behavior=(
            "Positive hourly momentum near established support has higher forward "
            "net returns than positive hourly momentum alone."
        ),
        observable_event="ROC(1) above 0 and support distance above 0 and below 1 ATR.",
        eligibility="Hourly NVDA, one position, next open, fixed $10,000, $100,000 cash.",
        treatment=treatment,
        control=control,
        target_estimand=(
            "paired event-level net forward return versus matched ROC-only "
            "opportunities at a fixed 65-bar horizon"
        ),
        horizon="65 bars",
        timeframe="1h",
        cost_label="research",
        falsifier="The paired 65-bar net contrast is not above the predeclared delta.",
        falsifier_series="active_excess_pp",
        falsifier_op="lte",
        falsifier_level=0.0,
        uncertainty_method="moving_block_bootstrap",
        minimum_evidence=floor,
        trial_budget=3,
        partitions=partitions,
        declared_axis="entry",
    )
