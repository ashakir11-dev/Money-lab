"""Component declarations and the registry.

A component declares a stable identifier, version, inputs, typed parameters with
defaults and constraints, outputs, and the contexts it may be used in. The dashboard
builds its controls from these declarations, and config validation uses them too, so
a new indicator only needs to be implemented and registered here.

Exit rules and position sizing rules are components too: each is one function
registered with ``register_exit`` or ``register_sizing``, and the engine calls it with
a small read-only context. Neither may look past the decision session.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

ParamType = Literal["int", "float", "choice"]
Use = Literal["indicator", "entry", "exit", "filter", "exit_rule", "sizing"]

# An indicator receives the full open/high/low/close/volume frame for one symbol (indexed by
# session) and its parameters, and returns one value per session. Values must only use
# data up to and including that session.
IndicatorFn = Callable[[pd.DataFrame, dict[str, Any]], pd.Series]

# A cross-sectional indicator (for example market breadth) receives every universe
# symbol's frame at once and returns one value per session, shared by all symbols.
# The same causality rule applies: a value may only use data up to its own session.
UniverseFn = Callable[[dict[str, pd.DataFrame], dict[str, Any]], pd.Series]

# Optional cross-parameter check: receives the resolved (defaulted, individually
# validated) parameters and raises ValueError when a combination is invalid, for
# example MACD's fast < slow. ParamSpec bounds only cover one parameter at a time.
ParamCheck = Callable[[dict[str, Any]], None]

# Indicators a rule needs computed for it, as resolved indicator refs
# ({"id": ..., "params": {...}}), worked out from the rule's own parameters.
Requires = Callable[[dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class ExitContext:
    """What an exit rule sees for one open position at the close of session ``i``.

    ``close`` is NaN when the symbol has no bar that session. ``peak_close`` is the
    highest close from the fill session up to and including ``i``. ``values`` holds the
    rule's required indicators at ``i``, keyed by their indicator key.
    """

    symbol: str
    qty: int
    entry_price: float
    held: int  # sessions held; the fill session counts as one
    close: float
    peak_close: float
    values: dict[str, float]


@dataclass(frozen=True)
class SizingContext:
    """What a sizing rule sees when an entry signal passes at the close of session ``i``."""

    symbol: str
    close: float
    equity: float  # account equity at this close
    target_allocation: float
    values: dict[str, float]


# An exit rule returns None (hold) or (reason, extra fields recorded on the decision).
ExitFn = Callable[[ExitContext, dict[str, Any]], tuple[str, dict[str, Any]] | None]
# A sizing rule returns the whole number of shares to order (0 records zero_quantity).
SizeFn = Callable[[SizingContext, dict[str, Any]], int]


def indicator_key(ref: dict[str, Any]) -> str:
    """The stable key of a resolved indicator ref, e.g. ``atr(length=14)``.

    The engine stores indicator values under this key (in decision ``values`` and in
    ``ExitContext.values`` / ``SizingContext.values``); rules look their inputs up with it.
    """
    args = ",".join(f"{k}={v}" for k, v in sorted(ref["params"].items()))
    return f"{ref['id']}({args})"


def _no_requirements(params: dict[str, Any]) -> list[dict[str, Any]]:
    return []


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: ParamType
    default: Any
    label: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""

    def validate(self, value: Any) -> Any:
        if self.type == "int":
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{self.name} must be an integer")
            if float(value) != int(value):
                raise ValueError(f"{self.name} must be a whole number")
            value = int(value)
        elif self.type == "float":
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{self.name} must be a number")
            value = float(value)
        elif self.type == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.name} must be one of {list(self.choices)}")
            return value
        if self.min is not None and value < self.min:
            raise ValueError(f"{self.name} must be >= {self.min}")
        if self.max is not None and value > self.max:
            raise ValueError(f"{self.name} must be <= {self.max}")
        return value


@dataclass(frozen=True)
class ComponentDeclaration:
    id: str
    version: str
    label: str
    uses: tuple[Use, ...]
    params: tuple[ParamSpec, ...]
    inputs: tuple[str, ...] = ("close",)
    outputs: tuple[str, ...] = ("value",)
    description: str = ""
    compute: IndicatorFn | None = field(default=None, compare=False, repr=False)
    check: ParamCheck | None = field(default=None, compare=False, repr=False)
    # Set for indicators computed across the whole universe instead of per symbol.
    compute_universe: UniverseFn | None = field(default=None, compare=False, repr=False)
    # True when values are in price units, so "close crosses <indicator>" is meaningful.
    price_scale: bool = False
    exit_fn: ExitFn | None = field(default=None, compare=False, repr=False)
    size_fn: SizeFn | None = field(default=None, compare=False, repr=False)
    requires: Requires = field(default=_no_requirements, compare=False, repr=False)

    def param(self, name: str) -> ParamSpec:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(f"{self.id} has no parameter {name!r}")

    def resolve_params(self, given: dict[str, Any], *, cross_check: bool = True) -> dict[str, Any]:
        """Fill defaults, validate each parameter, then run the optional cross-check.

        Unknown parameters are rejected. Editors pass ``cross_check=False`` to fill a
        form whose values are not yet consistent; execution always checks.
        """
        unknown = set(given) - {p.name for p in self.params}
        if unknown:
            raise ValueError(f"{self.id}: unknown parameter(s) {sorted(unknown)}")
        resolved = {p.name: p.validate(given.get(p.name, p.default)) for p in self.params}
        if cross_check and self.check is not None:
            self.check(resolved)
        return resolved

    def warmup(self, params: dict[str, Any]) -> int:
        """Sessions of history needed before the first valid value (0-based index)."""
        return int(params.get("length", 1))


_REGISTRY: dict[str, ComponentDeclaration] = {}


def register(decl: ComponentDeclaration) -> ComponentDeclaration:
    if decl.id in _REGISTRY and _REGISTRY[decl.id] != decl:
        raise ValueError(f"component {decl.id!r} is already registered")
    _REGISTRY[decl.id] = decl
    return decl


def register_indicator(
    *,
    id: str,
    version: str,
    label: str,
    params: tuple[ParamSpec, ...],
    description: str = "",
    inputs: tuple[str, ...] = ("close",),
    check: ParamCheck | None = None,
    price_scale: bool = False,
) -> Callable[[IndicatorFn], IndicatorFn]:
    """Decorator: implement an indicator once and make it usable in every strategy."""

    def wrap(fn: IndicatorFn) -> IndicatorFn:
        register(
            ComponentDeclaration(
                id=id,
                version=version,
                label=label,
                uses=("indicator",),
                params=params,
                inputs=inputs,
                description=description,
                compute=fn,
                check=check,
                price_scale=price_scale,
            )
        )
        return fn

    return wrap


def register_universe_indicator(
    *,
    id: str,
    version: str,
    label: str,
    params: tuple[ParamSpec, ...],
    description: str = "",
    inputs: tuple[str, ...] = ("close",),
) -> Callable[[UniverseFn], UniverseFn]:
    """Decorator for a cross-sectional indicator: one value per session for the universe."""

    def wrap(fn: UniverseFn) -> UniverseFn:
        register(
            ComponentDeclaration(
                id=id,
                version=version,
                label=label,
                uses=("indicator",),
                params=params,
                inputs=inputs,
                description=description,
                compute_universe=fn,
            )
        )
        return fn

    return wrap


def register_exit(
    *,
    id: str,
    version: str,
    label: str,
    params: tuple[ParamSpec, ...],
    description: str = "",
    check: ParamCheck | None = None,
    requires: Requires = _no_requirements,
) -> Callable[[ExitFn], ExitFn]:
    """Decorator: an exit rule usable in any strategy's list of exits."""

    def wrap(fn: ExitFn) -> ExitFn:
        register(
            ComponentDeclaration(
                id=id,
                version=version,
                label=label,
                uses=("exit_rule",),
                params=params,
                inputs=("position", "close"),
                outputs=("signal",),
                description=description,
                check=check,
                exit_fn=fn,
                requires=requires,
            )
        )
        return fn

    return wrap


def register_sizing(
    *,
    id: str,
    version: str,
    label: str,
    params: tuple[ParamSpec, ...],
    description: str = "",
    check: ParamCheck | None = None,
    requires: Requires = _no_requirements,
) -> Callable[[SizeFn], SizeFn]:
    """Decorator: a position sizing rule (how many shares a new entry orders)."""

    def wrap(fn: SizeFn) -> SizeFn:
        register(
            ComponentDeclaration(
                id=id,
                version=version,
                label=label,
                uses=("sizing",),
                params=params,
                inputs=("equity", "close"),
                outputs=("quantity",),
                description=description,
                check=check,
                size_fn=fn,
                requires=requires,
            )
        )
        return fn

    return wrap


def get_component(component_id: str) -> ComponentDeclaration:
    try:
        return _REGISTRY[component_id]
    except KeyError:
        raise KeyError(f"unknown component {component_id!r}") from None


def list_components(use: Use | None = None) -> list[ComponentDeclaration]:
    items = sorted(_REGISTRY.values(), key=lambda d: d.id)
    return [d for d in items if use is None or use in d.uses]
