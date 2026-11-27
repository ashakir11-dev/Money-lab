"""Proposal modes, one-axis children, and a governance log that cannot override a kill.

Three starts are accepted: a family-budget seed, a stated market behavior, and an
exploratory bucket search. Each one still needs the card's control and falsifier.
A child is emitted only for one expressible bucket that cleared the minimum count,
and only on the entry axis. No bucket parks the branch. It does not authorize a
different change.

The proposer sees discovery rows only, unless validation has been permitted. Holdout
rows are dropped before the prompt is built. A kill, a freeze, or an invalid branch
is not reopened. A labeled fallback is what runs when no completion is injected.
``require_model`` fails closed instead of pretending a model replied. Trial spend
counts an authorized child only. A refusal is stored and does not spend a trial.
Prompt provenance keeps the version, the label, and a hash. It does not keep a key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from moneylab.decision.evidence import EvidenceBundle, discovery_view
from moneylab.decision.integrity import (
    ResearchCard,
    authorize_one_axis,
)
from moneylab.decision.proposer import VERDICTS
from moneylab.engine.config import Condition, IndicatorRef, StrategyConfig, resolve
from moneylab.settings import Settings

PROMPT_VERSION = "research-proposer/1.0.0"
MODES = ("family_budget", "market_behavior", "exploratory_search")
TRIAL_POLICY = (
    "An authorized one-axis child spends one trial. A refusal, including a kill "
    "override, a holdout citation, a missing bucket, and an unexpressible bucket, "
    "is stored and does not spend a trial."
)
ProposalMode = Literal["family_budget", "market_behavior", "exploratory_search"]
_LEVEL = {"lookback": 50, "min_move": 2.0, "zone": 1.0, "atr_length": 14}
_MACD = {"fast": 12, "slow": 26, "signal": 9, "output": "histogram"}
_RSI = {"length": 14}
# Bucket name to indicator, comparisons, and parameters. Unlisted buckets stay observations.
_EXPRESSIBLE: dict[str, tuple[str, tuple[tuple[str, float], ...], dict[str, Any]]] = {
    "support_distance:0_1": ("support_distance", (("above", 0), ("below", 1)), _LEVEL),
    "support_distance:1_2": ("support_distance", (("at_or_above", 1), ("below", 2)), _LEVEL),
    "support_distance:2_3": ("support_distance", (("at_or_above", 2), ("below", 3)), _LEVEL),
    "support_distance:3_plus": ("support_distance", (("at_or_above", 3),), _LEVEL),
    "rsi:below_30": ("rsi", (("below", 30),), _RSI),
    "rsi:30_40": ("rsi", (("at_or_above", 30), ("below", 40)), _RSI),
    "rsi:40_50": ("rsi", (("at_or_above", 40), ("below", 50)), _RSI),
    "rsi:50_60": ("rsi", (("at_or_above", 50), ("below", 60)), _RSI),
    "rsi:60_plus": ("rsi", (("at_or_above", 60),), _RSI),
    "macd:positive": ("macd", (("above", 0),), _MACD),
    "macd:negative": ("macd", (("below", 0),), _MACD),
    "support_tests:one": ("support_tests", (("at_or_above", 1), ("below", 2)), _LEVEL),
    "support_tests:two": ("support_tests", (("at_or_above", 2), ("below", 3)), _LEVEL),
    "support_tests:three_plus": ("support_tests", (("at_or_above", 3),), _LEVEL),
    "market_structure:positive": ("market_structure", (("above", 0),), {}),
    "stock_minus_spy:positive": ("stock_minus_spy", (("above", 0),), {}),
}


@dataclass
class GovernanceLog:
    """Stored attempts. ``spent`` follows ``TRIAL_POLICY`` and never includes a secret."""

    attempts: list[dict[str, Any]] = field(default_factory=list)
    spent: int = 0

    def note(self, record: dict[str, Any], *, spent_trial: bool) -> None:
        self.attempts.append(record)
        if spent_trial:
            self.spent += 1


@dataclass
class StructuredProposal:
    mode: str
    labeled: str
    evidence_refs: tuple[str, ...]
    observed_pattern: str
    alternative_explanation: str
    declared_axis: str
    predicted_effect: str
    falsifier: str
    required_sample: int
    child: StrategyConfig | None
    authorized: bool
    reasons: tuple[str, ...]
    spends_trial: bool
    provenance: dict[str, str]


def next_research_step(
    card: ResearchCard,
    bundle: EvidenceBundle | None,
    decision_state: str,
) -> str:
    """The next named step. This does not score bars or load a holdout."""
    if decision_state in {"KILL", "FREEZE", "INVALID"}:
        return "stop"
    if bundle is None or not bundle.events:
        return "observe"
    proposal = propose_from_evidence(
        card, bundle, mode="exploratory_search", decision_state=decision_state
    )
    if proposal.authorized:
        return "propose"
    if "no_supported_bucket" in proposal.reasons:
        return "park"
    return "park"


def propose_from_evidence(
    card: ResearchCard,
    bundle: EvidenceBundle,
    *,
    mode: str,
    decision_state: str,
    require_model: bool = False,
    complete: Any = None,
    validation_permitted: bool = False,
    log: GovernanceLog | None = None,
) -> StructuredProposal:
    """One structured response. Discovery evidence only. A kill is left alone."""
    holdout_ids = {
        event["event_id"] for event in bundle.events if event.get("partition") == "holdout"
    }
    viewed = discovery_view(bundle, validation_permitted=validation_permitted)
    proposal = _decide(
        card,
        viewed,
        mode=mode,
        decision_state=decision_state,
        require_model=require_model,
        complete=complete,
        holdout_ids=holdout_ids,
    )
    if log is not None:
        log.note(_record(proposal), spent_trial=proposal.spends_trial)
    return proposal


def _decide(
    card: ResearchCard,
    viewed: EvidenceBundle,
    *,
    mode: str,
    decision_state: str,
    require_model: bool,
    complete: Any,
    holdout_ids: set[str],
) -> StructuredProposal:
    refs = tuple(event["event_id"] for event in viewed.events[:5])
    if decision_state == "KILL":
        return _refused(mode, "fallback", ("kill_immutable",), refs, card)
    if decision_state in {"FREEZE", "INVALID"}:
        return _refused(mode, "fallback", ("terminal",), refs, card)
    if mode not in MODES:
        return _refused(mode, "fallback", ("unknown_mode",), refs, card)
    if not refs:
        return _refused(mode, "fallback", ("no_evidence",), (), card)
    if require_model and complete is None:
        return _refused(mode, "fallback", ("model_required",), refs, card)
    parsed: dict[str, Any] | None = None
    labeled = "fallback"
    model_name = "fallback"
    response_hash = ""
    if complete is not None:
        parsed, model_name, response_hash, problem = _read_model(
            complete, viewed, mode, holdout_ids
        )
        labeled = "model"
        if problem:
            return _refused(
                mode,
                labeled,
                (problem,),
                refs,
                card,
                model_name=model_name,
                response_hash=response_hash,
            )
    bucket = _selected_bucket(viewed, parsed)
    if bucket is None:
        reason = "unsupported_bucket" if parsed is not None else "no_supported_bucket"
        return _refused(
            mode, labeled, (reason,), refs, card, model_name=model_name, response_hash=response_hash
        )
    spec = _EXPRESSIBLE.get(bucket["name"])
    if spec is None:
        return _refused(
            mode,
            labeled,
            ("unexpressible_bucket",),
            refs,
            card,
            model_name=model_name,
            response_hash=response_hash,
        )
    if parsed is not None and parsed.get("declared_axis") not in (None, "entry"):
        return _refused(
            mode,
            labeled,
            ("axis_refused",),
            refs,
            card,
            model_name=model_name,
            response_hash=response_hash,
        )
    child = _child(card.treatment, spec)
    if child is None:
        return _refused(
            mode,
            labeled,
            ("no_change",),
            refs,
            card,
            model_name=model_name,
            response_hash=response_hash,
        )
    if child.timeframe != card.timeframe:
        return _refused(
            mode,
            labeled,
            ("timeframe_mismatch",),
            refs,
            card,
            model_name=model_name,
            response_hash=response_hash,
        )
    settings = Settings(_env_file=None)
    auth = authorize_one_axis(
        resolve(card.treatment, settings),
        resolve(child, settings),
        "entry",
        approve_cash_policy=card.approve_cash_policy,
    )
    if not auth.authorized:
        return _refused(
            mode,
            labeled,
            auth.reasons,
            refs,
            card,
            model_name=model_name,
            response_hash=response_hash,
        )
    pattern = _pattern(mode, card, bucket)
    return StructuredProposal(
        mode=mode,
        labeled=labeled,
        evidence_refs=refs,
        observed_pattern=pattern,
        alternative_explanation=(
            "The bucket can be a mix of names, a path already taken, or a chance split."
        ),
        declared_axis="entry",
        predicted_effect="The entry set changes. Exit, size, costs, and the universe stay put.",
        falsifier=card.falsifier,
        required_sample=card.minimum_evidence.min_events,
        child=child,
        authorized=True,
        reasons=("authorized",),
        spends_trial=True,
        provenance=_provenance(labeled, model_name, response_hash, refs),
    )


def _selected_bucket(
    viewed: EvidenceBundle, parsed: dict[str, Any] | None
) -> dict[str, Any] | None:
    supported = [
        _bucket(row) for row in viewed.contrast.get("buckets", []) if row["status"] == "supported"
    ]
    if parsed is None:
        for row in supported:
            if row["name"] in _EXPRESSIBLE:
                return row
        return None
    name = str(parsed.get("bucket") or "")
    for row in supported:
        if row["name"] == name:
            return row
    return None


def _bucket(row: Any) -> dict[str, Any]:
    return {
        "name": str(row["name"]),
        "complete": int(row["complete"]),
        "mean_net": row.get("mean_net"),
        "status": str(row["status"]),
    }


def _child(
    parent: StrategyConfig,
    spec: tuple[str, tuple[tuple[str, float], ...], dict[str, Any]],
) -> StrategyConfig | None:
    feature_id, comparisons, params = spec
    added = [
        Condition(
            kind="threshold",
            indicator=IndicatorRef(id=feature_id, params=dict(params)),
            params={"op": op, "value": value},
        )
        for op, value in comparisons
    ]
    existing = {
        (cond.indicator.id, cond.params.get("op"), cond.params.get("value"))
        for cond in parent.entry
    }
    fresh = [
        cond
        for cond in added
        if (cond.indicator.id, cond.params.get("op"), cond.params.get("value")) not in existing
    ]
    if not fresh:
        return None
    return parent.model_copy(update={"entry": [*parent.entry, *fresh]})


def _read_model(
    complete: Any,
    viewed: EvidenceBundle,
    mode: str,
    holdout_ids: set[str],
) -> tuple[dict[str, Any] | None, str, str, str]:
    request = {
        "mode": mode,
        "timeframe": viewed.timeframe,
        "evidence_refs": [event["event_id"] for event in viewed.events[:5]],
        "buckets": [row["name"] for row in viewed.contrast.get("buckets", [])],
        "prompt_version": PROMPT_VERSION,
    }
    try:
        raw = str(complete(json.dumps(request, sort_keys=True)))
    except Exception:
        return None, "injected", "", "model_failed"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, "injected", digest, "model_failed"
    if not isinstance(parsed, dict):
        return None, "injected", digest, "model_failed"
    verdict = str(parsed.get("verdict") or "").lower()
    if verdict in VERDICTS:
        return None, "injected", digest, "proposer_overreach"
    cited = parsed.get("evidence_refs") or []
    if any(str(ref) in holdout_ids for ref in cited):
        return None, "injected", digest, "holdout_ref"
    required = (
        "bucket",
        "observed_pattern",
        "alternative_explanation",
        "predicted_effect",
        "falsifier",
        "required_sample",
    )
    if any(not str(parsed.get(key) or "").strip() for key in required):
        return None, "injected", digest, "incomplete_proposal"
    return parsed, "injected", digest, ""


def _pattern(mode: str, card: ResearchCard, bucket: dict[str, Any]) -> str:
    mean = bucket.get("mean_net")
    body = (
        f"exploratory bucket {bucket['name']} has {bucket['complete']} complete "
        f"outcomes and mean net {mean}"
    )
    if mode == "family_budget":
        return f"Family seed {card.family} keeps its control and falsifier. {body}"
    if mode == "market_behavior":
        return f"{card.behavior} {body}"
    return f"Exploratory search. {body}"


def _refused(
    mode: str,
    labeled: str,
    reasons: tuple[str, ...],
    refs: tuple[str, ...],
    card: ResearchCard,
    *,
    model_name: str = "fallback",
    response_hash: str = "",
) -> StructuredProposal:
    return StructuredProposal(
        mode=mode,
        labeled=labeled,
        evidence_refs=refs,
        observed_pattern="",
        alternative_explanation="",
        declared_axis=card.declared_axis or "",
        predicted_effect="",
        falsifier=card.falsifier,
        required_sample=card.minimum_evidence.min_events,
        child=None,
        authorized=False,
        reasons=reasons,
        spends_trial=False,
        provenance=_provenance(labeled, model_name, response_hash, refs),
    )


def _provenance(
    labeled: str, model_name: str, response_hash: str, refs: tuple[str, ...]
) -> dict[str, str]:
    return {
        "prompt_version": PROMPT_VERSION,
        "labeled": labeled,
        "model": model_name,
        "response_sha256": response_hash,
        "evidence_refs": ",".join(refs),
        "trial_policy": TRIAL_POLICY,
    }


def _record(proposal: StructuredProposal) -> dict[str, Any]:
    return {
        "mode": proposal.mode,
        "labeled": proposal.labeled,
        "authorized": proposal.authorized,
        "reasons": list(proposal.reasons),
        "declared_axis": proposal.declared_axis,
        "evidence_refs": list(proposal.evidence_refs),
        "prompt_version": PROMPT_VERSION,
        "model": proposal.provenance["model"],
        "response_sha256": proposal.provenance["response_sha256"],
        "spent_trial": proposal.spends_trial,
    }
