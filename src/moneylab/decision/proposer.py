"""The proposer writes the thesis and names one change. It does not judge the result.

Kill, freeze, and paper stay on the thresholds. When ``OPENAI_API_KEY`` is set, the
proposer calls OpenAI chat completions. Otherwise it returns the deterministic fallback.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from moneylab.decision.rules import catalog_has
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    RankBy,
    StrategyConfig,
)

FAMILIES = ("bounce", "breakout", "trend", "rotation")
VERDICTS = frozenset({"kill", "freeze", "paper"})
_MODEL_KEYS = ("MONEYLAB_MODEL_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_OPENAI_SYSTEM = (
    "You may only propose a seed or thesis and one next change inside the existing rules. "
    "Families are bounce, breakout, trend, and rotation. "
    "Buckets are exit, filter, and entry. "
    "Signals must already be in the catalog. "
    "Return one JSON object. "
    "Kill, freeze, and paper stay thresholds. Do not return those as a verdict or a bucket."
)


class ProposerOverreach(ValueError):
    """A proposer tried to kill, freeze, or paper. Those calls are thresholds."""


class OpenAIProposerError(RuntimeError):
    """The chat completion failed. The message does not include the API key."""


class ThesisIncomplete(ValueError):
    """The budget did not name a universe, so no tickers were invented."""


@dataclass(frozen=True)
class StyleBudget:
    remaining: dict[str, int]
    universe: tuple[str, ...]
    open_family: str | None = None
    benchmark: str = "SPY"


@dataclass(frozen=True)
class Reading:
    """Numbers the proposer is allowed to read. It does not turn them into a verdict."""

    idle_cash: str
    fair_gap_pp: float | None = None
    edge_t: float | None = None
    trade_count: int = 0
    year_profit_shares: tuple[float, ...] = ()
    giveback_pp: float | None = None
    losing_slice: str | None = None
    weak_everywhere: bool = False


@dataclass(frozen=True)
class BucketProposal:
    bucket: str | None
    reason: str
    change: str


@dataclass
class ThesisDraft:
    family: str
    claim: str
    universe: tuple[str, ...]
    signals: tuple[str, ...]
    knobs: dict[str, object]
    falsifier: str
    falsifier_series: str
    falsifier_op: str
    falsifier_level: float
    recipe: StrategyConfig
    control: StrategyConfig


class Proposer(Protocol):
    def propose_family(self, budget: StyleBudget) -> str: ...

    def write_thesis(self, family: str, budget: StyleBudget) -> ThesisDraft: ...

    def name_bucket(self, reading: Reading) -> BucketProposal: ...

    def propose_change(self, parent: StrategyConfig, bucket: str) -> StrategyConfig: ...


def model_api_key() -> str | None:
    for name in _MODEL_KEYS:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def proposer_mode() -> str:
    """``model`` when a model key is set. Calling out still requires ``OPENAI_API_KEY``."""
    return "model" if model_api_key() else "fallback"


def open_proposer(
    complete: Callable[[str], str] | None = None,
    *,
    client: httpx.Client | None = None,
) -> Proposer:
    """Use OpenAI when ``OPENAI_API_KEY`` is set. Otherwise use the deterministic fallback.

    ``complete`` injects a completion and does not call the network. ``client`` is the
    HTTP client for that call, so tests can pass a mock transport.
    """
    if complete is not None:
        return ModelProposer(complete)
    if _openai_api_key() is None:
        return FallbackProposer()
    return ModelProposer(lambda prompt: _openai_chat_complete(prompt, client=client))


def _openai_api_key() -> str | None:
    value = os.environ.get("OPENAI_API_KEY")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _openai_model() -> str:
    stripped = os.environ.get("MONEYLAB_OPENAI_MODEL", "").strip()
    return stripped or _DEFAULT_OPENAI_MODEL


def _redact(text: str, key: str) -> str:
    if not key:
        return text
    return text.replace(key, "[redacted]")


def _openai_chat_complete(prompt: str, *, client: httpx.Client | None = None) -> str:
    key = _openai_api_key()
    if key is None:
        raise OpenAIProposerError("openai_key_absent")
    body: dict[str, object] = {
        "model": _openai_model(),
        "messages": [
            {"role": "system", "content": _OPENAI_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}"}

    def post(http: httpx.Client) -> httpx.Response:
        return http.post(_OPENAI_CHAT_URL, headers=headers, json=body)

    try:
        if client is None:
            with httpx.Client(timeout=30.0, trust_env=False, follow_redirects=False) as http:
                response = post(http)
        else:
            response = post(client)
    except httpx.HTTPError:
        raise OpenAIProposerError("openai_transport") from None
    return _completion_text(response, key)


def _completion_text(response: httpx.Response, key: str) -> str:
    if response.status_code != 200:
        detail = _error_detail(response, key)
        failure = f"openai_http_{response.status_code}"
        if detail:
            failure = f"{failure} {detail}"
        raise OpenAIProposerError(failure)
    try:
        payload: object = response.json()
    except json.JSONDecodeError:
        raise OpenAIProposerError("openai_payload") from None
    if not isinstance(payload, dict):
        raise OpenAIProposerError("openai_payload")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise OpenAIProposerError("openai_payload")
    choice = choices[0].get("message")
    if not isinstance(choice, dict):
        raise OpenAIProposerError("openai_payload")
    content = choice.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OpenAIProposerError("openai_payload")
    return content


def _error_detail(response: httpx.Response, key: str) -> str:
    try:
        payload: object = response.json()
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    message = error.get("message")
    if not isinstance(message, str):
        return ""
    return _redact(message, key)


def reject_verdict(payload: Mapping[str, object]) -> None:
    verdict = str(payload.get("verdict", "")).strip().lower()
    bucket = str(payload.get("bucket", "")).strip().lower()
    if verdict in VERDICTS or bucket in VERDICTS:
        raise ProposerOverreach(verdict or bucket)


def accept_family(family: str, budget: StyleBudget) -> tuple[bool, str]:
    if family not in FAMILIES:
        return False, "family_outside_budget"
    if budget.remaining.get(family, 0) <= 0:
        return False, "family_budget_spent"
    return True, "accepted"


def allow_new_component(families_requesting: int) -> bool:
    """A new signal needs two families. One family's wish is refused."""
    return families_requesting >= 2


class FallbackProposer:
    """Catalog seeds and one-bucket reads. The same budget always gets the same thesis."""

    def propose_family(self, budget: StyleBudget) -> str:
        opened = budget.open_family
        if opened in FAMILIES and budget.remaining.get(opened or "", 0) > 0:
            return str(opened)
        for family in FAMILIES:
            if budget.remaining.get(family, 0) > 0:
                return family
        return FAMILIES[0]

    def write_thesis(self, family: str, budget: StyleBudget) -> ThesisDraft:
        if family not in FAMILIES:
            raise ThesisIncomplete("family_outside_budget")
        if not budget.universe:
            raise ThesisIncomplete("universe_missing")
        recipe = _seed(family, budget, hold=_HOLD[family])
        control = _seed(family, budget, hold=_HOLD[family], control=True)
        claim = (
            f"In {family}, {_SIGNAL_TEXT[family]}, held {_HOLD[family]} sessions "
            f"on {', '.join(budget.universe)}, is positive on invested days while cash "
            "stays cash. The fair gap is read after unused cash tracks the benchmark."
        )
        return ThesisDraft(
            family=family,
            claim=claim,
            universe=budget.universe,
            signals=_SIGNALS[family],
            knobs={
                "hold_sessions": _HOLD[family],
                "idle_cash": "cash",
                "max_positions": 5,
                "target_allocation": 0.2,
            },
            falsifier="Out of sample, active excess versus the benchmark is not positive.",
            falsifier_series="active_excess_pp",
            falsifier_op="lte",
            falsifier_level=0.0,
            recipe=recipe,
            control=control,
        )

    def name_bucket(self, reading: Reading) -> BucketProposal:
        if reading.idle_cash == "cash":
            return BucketProposal(None, "cash_drag", "idle_cash_benchmark")
        if reading.giveback_pp is not None and reading.giveback_pp > 0:
            return BucketProposal("exit", "gave_back", "hold")
        if reading.losing_slice and not reading.weak_everywhere:
            return BucketProposal("filter", "losing_slice", "sma_200_filter")
        if reading.weak_everywhere:
            return BucketProposal("entry", "weak_everywhere", "highest_high_50")
        return BucketProposal(None, "no_bucket", "none")

    def propose_change(self, parent: StrategyConfig, bucket: str) -> StrategyConfig:
        if bucket == "exit":
            hold = int(parent.exit.params.get("sessions", 10))
            return _set_hold(parent, 5 if hold != 5 else 20)
        if bucket == "filter":
            signal = Condition(
                kind="price_cross",
                indicator=IndicatorRef(id="sma", params={"length": 200}),
                params={"direction": "above"},
            )
            return parent.model_copy(update={"entry_signals": [signal]})
        if bucket == "entry":
            entry = Condition(
                kind="price_cross",
                indicator=IndicatorRef(id="highest_high", params={"length": 50}),
                params={"direction": "above"},
            )
            return parent.model_copy(update={"entry": [entry]})
        raise ValueError(f"no single change for bucket {bucket!r}")


class ModelProposer:
    """Parse a completion. Kill, freeze, and paper in the payload are refused."""

    def __init__(self, complete: Callable[[str], str]) -> None:
        self._complete = complete
        self._fallback = FallbackProposer()

    def propose_family(self, budget: StyleBudget) -> str:
        payload = self._payload("Propose one family. Do not kill, freeze, or paper.")
        family = str(payload.get("family", ""))
        if family not in FAMILIES:
            raise ThesisIncomplete("family_outside_budget")
        return family

    def write_thesis(self, family: str, budget: StyleBudget) -> ThesisDraft:
        payload = self._payload("Write one thesis the lab can run.")
        named = str(payload.get("family") or family)
        if named != family or named not in FAMILIES:
            raise ThesisIncomplete("family_mismatch")
        signals = payload.get("signals")
        unknown = signals is not None and (
            not isinstance(signals, list) or any(not catalog_has(str(item)) for item in signals)
        )
        if unknown:
            raise ThesisIncomplete("signal_not_in_catalog")
        draft = self._fallback.write_thesis(family, budget)
        claim = payload.get("claim")
        if isinstance(claim, str) and claim.strip():
            draft.claim = claim.strip()
        falsifier = payload.get("falsifier")
        if isinstance(falsifier, str) and falsifier.strip():
            draft.falsifier = falsifier.strip()
        return draft

    def name_bucket(self, reading: Reading) -> BucketProposal:
        payload = self._payload("Name one blame bucket.")
        bucket = payload.get("bucket")
        if bucket is not None:
            bucket = str(bucket)
        if bucket not in {None, "exit", "filter", "entry"}:
            raise ProposerOverreach(str(bucket))
        return BucketProposal(
            bucket, str(payload.get("reason", "")), str(payload.get("change", ""))
        )

    def propose_change(self, parent: StrategyConfig, bucket: str) -> StrategyConfig:
        self._payload("Propose one change inside the named bucket.")
        return self._fallback.propose_change(parent, bucket)

    def _payload(self, prompt: str) -> dict[str, object]:
        raw = json.loads(self._complete(prompt))
        if not isinstance(raw, dict):
            raise ThesisIncomplete("model_payload")
        reject_verdict(raw)
        return raw


def repair_falsifier(draft: ThesisDraft, *, idle_cash: str) -> ThesisDraft:
    """A fair-gap falsifier while cash stays cash is rewritten to the series the lab can score.

    The rewrite does not kill the thesis.
    """
    if idle_cash == "cash" and draft.falsifier_series != "active_excess_pp":
        draft.falsifier_series = "active_excess_pp"
        draft.falsifier_op = "lte"
        draft.falsifier_level = 0.0
        draft.falsifier = "Out of sample, active excess versus the benchmark is not positive."
    if idle_cash == "benchmark" and draft.falsifier_series != "terminal_net_excess_pp":
        draft.falsifier_series = "terminal_net_excess_pp"
        draft.falsifier_op = "lte"
        draft.falsifier_level = 0.0
        draft.falsifier = "Out of sample, the fair gap versus the benchmark is not positive."
    return draft


_HOLD = {"bounce": 10, "breakout": 10, "trend": 20, "rotation": 20}
_SIGNALS = {
    "bounce": ("rsi",),
    "breakout": ("highest_high",),
    "trend": ("sma",),
    "rotation": ("roc",),
}
_SIGNAL_TEXT = {
    "bounce": "RSI(14) below 30",
    "breakout": "a close above the prior 20-session high",
    "trend": "a close above its 50-session average",
    "rotation": "20-session rate of change above zero, ranked by that rate",
}


def _portfolio(idle: Literal["cash", "benchmark"]) -> PortfolioSettings:
    return PortfolioSettings(
        max_positions=5,
        target_allocation=0.2,
        idle_cash=idle,
        starting_cash=100_000,
    )


def _entry(family: str, *, control: bool) -> Condition:
    if family == "bounce":
        value = 40 if control else 30
        return Condition(
            kind="threshold",
            indicator=IndicatorRef(id="rsi", params={"length": 14}),
            params={"op": "below", "value": value},
        )
    if family == "breakout":
        if control:
            return Condition(
                kind="threshold",
                indicator=IndicatorRef(id="roc", params={"length": 20}),
                params={"op": "above", "value": 0},
            )
        return Condition(
            kind="price_cross",
            indicator=IndicatorRef(id="highest_high", params={"length": 20}),
            params={"direction": "above"},
        )
    if family == "trend":
        length = 20 if control else 50
        return Condition(
            kind="price_cross",
            indicator=IndicatorRef(id="sma", params={"length": length}),
            params={"direction": "above"},
        )
    if control:
        return Condition(
            kind="price_cross",
            indicator=IndicatorRef(id="sma", params={"length": 20}),
            params={"direction": "above"},
        )
    return Condition(
        kind="threshold",
        indicator=IndicatorRef(id="roc", params={"length": 20}),
        params={"op": "above", "value": 0},
    )


def _seed(family: str, budget: StyleBudget, *, hold: int, control: bool = False) -> StrategyConfig:
    rank = None
    if family == "rotation":
        rank = RankBy(indicator=IndicatorRef(id="roc", params={"length": 20}), order="descending")
    return StrategyConfig(
        name=f"{family} {'control' if control else 'seed'}",
        universe=list(budget.universe),
        benchmark=budget.benchmark,
        timeframe="1d",
        entry=[_entry(family, control=control)],
        exit=ExitRule(kind="holding_period", params={"sessions": hold}),
        portfolio=_portfolio("cash"),
        rank_by=rank,
    )


def _set_hold(parent: StrategyConfig, sessions: int) -> StrategyConfig:
    rule = ExitRule(kind="holding_period", params={"sessions": sessions})
    updated: dict[str, object] = {"exit": rule}
    if parent.exits is not None:
        updated["exits"] = [rule]
    return parent.model_copy(update=updated)
