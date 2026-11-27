"""Quantitative search builds the batch the rules allow. It does not pick a winner.

A new idea gets at most 10 variants on one axis. A wider batch waits until a small
pass has survived out of sample. Costs on the parent recipe are left as they are.
"""

from __future__ import annotations

from dataclasses import dataclass

from moneylab.engine.config import Condition, ExitRule, StrategyConfig

SMALL_CAP = 10
WIDE_CAP = 30


class SearchRefused(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__(", ".join(reasons))


class SearchDoesNotPick(RuntimeError):
    """Search has no winner. The thresholds decide."""


@dataclass(frozen=True)
class SearchPlan:
    axis: str
    variants: int
    wide: bool = False
    oos_survivor: bool = False


@dataclass(frozen=True)
class SearchAuthorization:
    authorized: bool
    reasons: list[str]
    variants: int
    winner: None = None


def authorize_search(plan: SearchPlan) -> SearchAuthorization:
    if plan.axis not in {"entry", "filter", "exit", "cash"}:
        return SearchAuthorization(False, ["unknown_axis"], 0)
    if plan.variants < 1:
        return SearchAuthorization(False, ["empty_search"], 0)
    if plan.axis == "cash" and plan.variants != 1:
        return SearchAuthorization(False, ["cash_axis_is_one_change"], 0)
    if plan.wide:
        if not plan.oos_survivor:
            return SearchAuthorization(False, ["wide_search_before_oos"], 0)
        if plan.variants > WIDE_CAP:
            return SearchAuthorization(False, ["wide_search_cap"], 0)
        return SearchAuthorization(True, ["authorized"], plan.variants)
    if plan.variants > SMALL_CAP:
        return SearchAuthorization(False, ["small_search_cap"], 0)
    return SearchAuthorization(True, ["authorized"], plan.variants)


def pick_winner(*_args: object, **_kwargs: object) -> None:
    raise SearchDoesNotPick("search does not pick the winner")


def variants_on_axis(
    parent: StrategyConfig,
    axis: str,
    values: list[object],
    *,
    wide: bool = False,
    oos_survivor: bool = False,
) -> list[StrategyConfig]:
    """One knob, in the order given. Nothing is sorted by a result."""
    plan = SearchPlan(axis, len(values), wide, oos_survivor)
    auth = authorize_search(plan)
    if not auth.authorized:
        raise SearchRefused(auth.reasons)
    return [_apply(parent, axis, value) for value in values]


def cash_rerun(parent: StrategyConfig) -> StrategyConfig:
    """The one accounting fix: unused cash tracks the benchmark. The entry stays."""
    portfolio = parent.portfolio.model_copy(update={"idle_cash": "benchmark"})
    return parent.model_copy(update={"portfolio": portfolio})


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SearchRefused(["exit_value"])
    return int(value)


def _apply(parent: StrategyConfig, axis: str, value: object) -> StrategyConfig:
    if axis == "exit":
        rule = ExitRule(kind="holding_period", params={"sessions": _whole(value)})
        updated: dict[str, object] = {"exit": rule}
        if parent.exits is not None:
            updated["exits"] = [rule]
        return parent.model_copy(update=updated)
    if axis == "filter":
        if not isinstance(value, Condition):
            raise SearchRefused(["filter_value"])
        return parent.model_copy(update={"entry_signals": [value]})
    if axis == "entry":
        if not isinstance(value, Condition):
            raise SearchRefused(["entry_value"])
        return parent.model_copy(update={"entry": [value]})
    if axis == "cash":
        if value != "benchmark":
            raise SearchRefused(["cash_axis_is_one_change"])
        return cash_rerun(parent)
    raise SearchRefused(["unknown_axis"])
