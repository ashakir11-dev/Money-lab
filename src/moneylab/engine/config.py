"""Strategy configuration and resolution.

``StrategyConfig`` is what a user edits. ``resolve`` fills every default from the
component declarations and the settings in force, validates it, and records component
and engine versions. Runs store the resolved form, so reopening an old run shows what
actually ran even after defaults change.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

import moneylab.components  # noqa: F401  (registers built-ins)
from moneylab import ENGINE_VERSION
from moneylab.components.registry import get_component
from moneylab.settings import Settings, get_settings


class IndicatorRef(BaseModel):
    id: str
    params: dict[str, Any] = Field(default_factory=dict)

    def key(self) -> str:
        decl = get_component(self.id)
        args = ",".join(f"{k}={v}" for k, v in sorted(decl.resolve_params(self.params).items()))
        return f"{self.id}({args})"


class Condition(BaseModel):
    kind: Literal["price_cross", "threshold"]
    indicator: IndicatorRef
    params: dict[str, Any] = Field(default_factory=dict)

    def label(self) -> str:
        decl = get_component(self.kind)
        p = decl.resolve_params(self.params)
        if self.kind == "price_cross":
            return f"close crosses {p['direction']} {self.indicator.key()}"
        return f"{self.indicator.key()} {p['op']} {p['value']:g}"


class ExitRule(BaseModel):
    kind: Literal["holding_period", "signal"] = "holding_period"
    params: dict[str, Any] = Field(default_factory=dict)
    conditions: list[Condition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _signal_needs_conditions(self) -> ExitRule:
        if self.kind == "signal" and not self.conditions:
            raise ValueError("a signal exit needs at least one condition")
        return self


class PortfolioSettings(BaseModel):
    starting_cash: float | None = Field(default=None, gt=0)
    target_allocation: float | None = Field(default=None, gt=0, le=1)
    max_positions: int | None = Field(default=None, ge=1, le=300)
    cost_bps_per_side: float | None = Field(default=None, ge=0, le=500)


class EntryFill(BaseModel):
    """When a passing signal becomes a fill. Default is the next session open."""

    kind: Literal["next_open", "next_range", "dip"] = "next_open"
    below_pct: float | None = Field(default=None, ge=0, le=0.5)
    above_pct: float | None = Field(default=None, ge=0, le=0.5)
    dip_pct: float | None = Field(default=None, ge=0, le=0.5)

    @model_validator(mode="after")
    def _fill_defaults(self) -> EntryFill:
        if self.kind == "dip" and self.dip_pct is None:
            self.dip_pct = 0.02
        if self.kind == "next_range":
            if self.below_pct is None:
                self.below_pct = 0.02
            if self.above_pct is None:
                self.above_pct = 0.02
        return self


class StrategyConfig(BaseModel):
    name: str = "Untitled strategy"
    universe: list[str]
    benchmark: str = "SPY"
    start: date | None = None
    end: date | None = None
    entry: list[Condition]
    entry_fill: EntryFill = Field(default_factory=EntryFill)
    exit: ExitRule = Field(default_factory=ExitRule)
    portfolio: PortfolioSettings = Field(default_factory=PortfolioSettings)

    @field_validator("universe")
    @classmethod
    def _universe_ok(cls, v: list[str]) -> list[str]:
        syms = sorted({s.strip().upper() for s in v if s.strip()})
        if not syms:
            raise ValueError("universe must contain at least one symbol")
        if len(syms) > 300:
            raise ValueError("universe is limited to 300 symbols")
        return syms

    @field_validator("entry")
    @classmethod
    def _entry_ok(cls, v: list[Condition]) -> list[Condition]:
        if not v:
            raise ValueError("at least one entry condition is required")
        return v


def _fill_text(fill: EntryFill) -> str:
    if fill.kind == "dip":
        return (
            f"limit {fill.dip_pct:.0%} below the signal close; waits until the low prints through"
        )
    if fill.kind == "next_range":
        return (
            f"next session bar that trades in {-(fill.below_pct or 0):.0%}…+"
            f"{fill.above_pct or 0:.0%} of the signal close; otherwise the order is cancelled"
        )
    return "next eligible session open"


def _resolve_condition(c: Condition, use: str) -> dict[str, Any]:
    cond_decl = get_component(c.kind)
    if use not in cond_decl.uses:
        raise ValueError(f"{c.kind} cannot be used as an {use} condition")
    ind_decl = get_component(c.indicator.id)
    if "indicator" not in ind_decl.uses:
        raise ValueError(f"{c.indicator.id} is not an indicator")
    if c.kind == "price_cross" and not ind_decl.price_scale:
        raise ValueError(
            f"close cannot cross {ind_decl.label} ({ind_decl.id}): it is not in price "
            "units. Use a threshold condition instead."
        )
    return {
        "kind": c.kind,
        "version": cond_decl.version,
        "params": cond_decl.resolve_params(c.params),
        "indicator": {
            "id": ind_decl.id,
            "version": ind_decl.version,
            "params": ind_decl.resolve_params(c.indicator.params),
        },
    }


def resolve(config: StrategyConfig, settings: Settings | None = None) -> dict[str, Any]:
    """Validate a config and return its fully resolved, JSON-serialisable form."""
    s = settings or get_settings()
    exit_decl = get_component("holding_period")
    if config.exit.kind == "holding_period":
        exit_resolved: dict[str, Any] = {
            "kind": "holding_period",
            "version": exit_decl.version,
            "params": exit_decl.resolve_params(config.exit.params),
            "conditions": [],
        }
    else:
        raw_max = config.exit.params.get("max_sessions")
        max_sessions = int(raw_max) if raw_max not in (None, "") else None
        if max_sessions is not None and max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        exit_resolved = {
            "kind": "signal",
            "version": "1.0.0",
            "params": {"max_sessions": max_sessions} if max_sessions else {},
            "conditions": [_resolve_condition(c, "exit") for c in config.exit.conditions],
        }
    p = config.portfolio
    return {
        "name": config.name,
        "universe": list(config.universe),
        "benchmark": config.benchmark.upper(),
        "start": config.start.isoformat() if config.start else None,
        "end": config.end.isoformat() if config.end else None,
        "entry": [_resolve_condition(c, "entry") for c in config.entry],
        "entry_combine": "all",
        "entry_fill": config.entry_fill.model_dump(),
        "exit": exit_resolved,
        "portfolio": {
            "starting_cash": p.starting_cash if p.starting_cash is not None else s.starting_cash,
            "target_allocation": p.target_allocation
            if p.target_allocation is not None
            else s.target_allocation,
            "max_positions": p.max_positions if p.max_positions is not None else s.max_positions,
            "cost_bps_per_side": p.cost_bps_per_side
            if p.cost_bps_per_side is not None
            else s.cost_bps_per_side,
            "long_only": True,
            "whole_shares": True,
            "one_position_per_symbol": True,
        },
        "execution": {
            "engine_version": ENGINE_VERSION,
            "calendar": "XNYS",
            "signal_time": "after completed close",
            "fill": _fill_text(config.entry_fill),
            "sizing": "floor(target_allocation * equity_at_decision_close / decision_close)",
            "order_of_processing": "exits before entries",
            "tie_break": "candidates processed in ascending symbol order",
            "gap_affordability": (
                "if an entry costs more than available cash at the open, quantity is reduced "
                "to the largest affordable whole number; zero shares records a skip"
            ),
            "missing_bars": "no trade on a session without a bar; orders wait for the next bar",
        },
    }


def config_from_resolved(resolved: dict[str, Any]) -> StrategyConfig:
    """Rebuild an editable config from a resolved one (used when duplicating a run)."""

    def cond(c: dict[str, Any]) -> Condition:
        return Condition(
            kind=c["kind"],
            params=dict(c["params"]),
            indicator=IndicatorRef(id=c["indicator"]["id"], params=dict(c["indicator"]["params"])),
        )

    ex = resolved["exit"]
    return StrategyConfig(
        name=resolved["name"],
        universe=resolved["universe"],
        benchmark=resolved["benchmark"],
        start=resolved["start"],
        end=resolved["end"],
        entry=[cond(c) for c in resolved["entry"]],
        entry_fill=EntryFill(**(resolved.get("entry_fill") or {})),
        exit=ExitRule(
            kind=ex["kind"],
            params=dict(ex["params"]),
            conditions=[cond(c) for c in ex["conditions"]],
        ),
        portfolio=PortfolioSettings(
            **{k: resolved["portfolio"][k] for k in PortfolioSettings.model_fields}
        ),
    )


def config_hash(resolved: dict[str, Any]) -> str:
    blob = json.dumps({k: v for k, v in resolved.items() if k != "name"}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class VersionMismatch(RuntimeError):
    pass


def check_versions(resolved: dict[str, Any]) -> None:
    """Raise if the components or engine a resolved config names have changed version."""
    problems = []
    if resolved["execution"]["engine_version"] != ENGINE_VERSION:
        problems.append(f"engine {resolved['execution']['engine_version']} -> {ENGINE_VERSION}")
    conds = resolved["entry"] + resolved["exit"]["conditions"]
    items = [(c["kind"], c["version"]) for c in conds]
    items += [(c["indicator"]["id"], c["indicator"]["version"]) for c in conds]
    if resolved["exit"]["kind"] == "holding_period":
        items.append(("holding_period", resolved["exit"]["version"]))
    for cid, ver in items:
        cur = get_component(cid).version
        if cur != ver:
            problems.append(f"{cid} {ver} -> {cur}")
    if problems:
        raise VersionMismatch("cannot reproduce exactly: " + "; ".join(sorted(set(problems))))


def _flatten_for_test(d: dict[str, Any]) -> dict[str, Any]:
    from moneylab.engine.reporting import _flatten

    return _flatten(d)
