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
from moneylab import COMPATIBLE_ENGINE_VERSIONS, ENGINE_VERSION
from moneylab.components.registry import get_component
from moneylab.settings import Settings, get_settings


class IndicatorRef(BaseModel):
    id: str
    params: dict[str, Any] = Field(default_factory=dict)

    def key(self) -> str:
        decl = get_component(self.id)
        args = ",".join(f"{k}={v}" for k, v in sorted(decl.resolve_params(self.params).items()))
        return f"{self.id}({args})"


ConditionKind = Literal["price_cross", "threshold", "indicator_compare", "indicator_cross"]
PAIRED_KINDS = ("indicator_compare", "indicator_cross")


class Condition(BaseModel):
    kind: ConditionKind
    indicator: IndicatorRef
    params: dict[str, Any] = Field(default_factory=dict)
    other: IndicatorRef | None = Field(
        default=None,
        description="the second indicator for indicator_compare and indicator_cross",
    )

    @model_validator(mode="after")
    def _other_matches_kind(self) -> Condition:
        if self.kind in PAIRED_KINDS and self.other is None:
            raise ValueError(f"{self.kind} needs a second indicator ('other')")
        if self.kind not in PAIRED_KINDS and self.other is not None:
            raise ValueError(f"{self.kind} does not take a second indicator")
        return self

    def label(self) -> str:
        decl = get_component(self.kind)
        p = decl.resolve_params(self.params)
        if self.kind == "price_cross":
            return f"close crosses {p['direction']} {self.indicator.key()}"
        if self.kind == "indicator_compare":
            assert self.other is not None
            return f"{self.indicator.key()} {p['op']} {self.other.key()}"
        if self.kind == "indicator_cross":
            assert self.other is not None
            return f"{self.indicator.key()} crosses {p['direction']} {self.other.key()}"
        return f"{self.indicator.key()} {p['op']} {p['value']:g}"


class ExitRule(BaseModel):
    kind: str = Field(
        default="holding_period",
        description="'signal' (exit when any condition passes) or a registered exit rule id",
    )
    params: dict[str, Any] = Field(default_factory=dict)
    conditions: list[Condition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_kind(self) -> ExitRule:
        if self.kind == "signal":
            if not self.conditions:
                raise ValueError("a signal exit needs at least one condition")
            return self
        try:
            decl = get_component(self.kind)
        except KeyError:
            raise ValueError(f"unknown exit rule {self.kind!r}") from None
        if "exit_rule" not in decl.uses:
            raise ValueError(f"{self.kind} is not an exit rule")
        if self.conditions:
            raise ValueError(f"{self.kind} takes no conditions (only a signal exit does)")
        return self


class SizingRule(BaseModel):
    id: str = "percent_of_equity"
    params: dict[str, Any] = Field(default_factory=dict)


class RankBy(BaseModel):
    """Order in which a session's entry candidates claim cash and position slots."""

    indicator: IndicatorRef
    order: Literal["ascending", "descending"] = Field(
        default="ascending", description="ascending: lowest value first (e.g. most oversold RSI)"
    )


class PortfolioSettings(BaseModel):
    starting_cash: float | None = Field(default=None, gt=0)
    target_allocation: float | None = Field(default=None, gt=0, le=1)
    max_positions: int | None = Field(default=None, ge=1, le=300)
    cost_bps_per_side: float | None = Field(default=None, ge=0, le=500)
    idle_cash: Literal["cash", "benchmark"] | None = Field(
        default=None,
        description="what cash not needed by a position does: stay as cash (default) or be "
        "held in whole shares of the benchmark, sold at the open when a trade needs the cash",
    )


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
    timeframe: Literal["1min", "5min", "15min", "30min", "1h", "1d"] | None = Field(
        default=None,
        description="bar size; when omitted, the run takes the snapshot's bar size",
    )
    start: date | None = None
    end: date | None = None
    entry: list[Condition]
    entry_fill: EntryFill = Field(default_factory=EntryFill)
    exit: ExitRule = Field(default_factory=ExitRule)
    exits: list[ExitRule] | None = Field(
        default=None,
        description=(
            "several exit rules; the first that fires sells the whole position. When "
            "given it replaces `exit`."
        ),
    )
    portfolio: PortfolioSettings = Field(default_factory=PortfolioSettings)
    sizing: SizingRule | None = Field(
        default=None, description="position sizing rule; default percent_of_equity"
    )
    rank_by: RankBy | None = Field(
        default=None,
        description="order entry candidates by an indicator; default ascending symbol order",
    )

    @field_validator("exits")
    @classmethod
    def _exits_ok(cls, v: list[ExitRule] | None) -> list[ExitRule] | None:
        if v is not None and not v:
            raise ValueError("exits must contain at least one exit rule")
        return v

    def exit_rules(self) -> list[ExitRule]:
        return list(self.exits) if self.exits else [self.exit]

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
    out: dict[str, Any] = {
        "kind": c.kind,
        "version": cond_decl.version,
        "params": cond_decl.resolve_params(c.params),
        "indicator": _resolve_indicator(c.indicator),
    }
    if c.kind in PAIRED_KINDS:
        assert c.other is not None
        other_decl = get_component(c.other.id)
        if "indicator" not in other_decl.uses:
            raise ValueError(f"{c.other.id} is not an indicator")
        same_units = (ind_decl.price_scale and other_decl.price_scale) or (
            ind_decl.id == other_decl.id
        )
        if not same_units:
            raise ValueError(
                f"{ind_decl.label} ({ind_decl.id}) and {other_decl.label} ({other_decl.id}) "
                "are not in the same units. Compare two price-unit indicators, or the same "
                "indicator with different settings."
            )
        out["other"] = _resolve_indicator(c.other)
    return out


def _resolve_indicator(ref: IndicatorRef) -> dict[str, Any]:
    decl = get_component(ref.id)
    if "indicator" not in decl.uses:
        raise ValueError(f"{ref.id} is not an indicator")
    return {"id": decl.id, "version": decl.version, "params": decl.resolve_params(ref.params)}


def _resolve_exit(ex: ExitRule) -> dict[str, Any]:
    if ex.kind == "signal":
        # Optional cap: also exit after max_sessions held, even without the signal.
        raw_max = ex.params.get("max_sessions")
        max_sessions = int(raw_max) if raw_max not in (None, "") else None
        if max_sessions is not None and max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        unknown = set(ex.params) - {"max_sessions"}
        if unknown:
            raise ValueError(f"signal exit: unknown parameter(s) {sorted(unknown)}")
        return {
            "kind": "signal",
            "version": "1.0.0",
            "params": {"max_sessions": max_sessions} if max_sessions else {},
            "conditions": [_resolve_condition(c, "exit") for c in ex.conditions],
        }
    decl = get_component(ex.kind)
    params = decl.resolve_params(ex.params)
    out: dict[str, Any] = {
        "kind": decl.id,
        "version": decl.version,
        "params": params,
        "conditions": [],
    }
    req = _requirements(decl.requires(params))
    if req:
        out["requires"] = req
    return out


def _requirements(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_resolve_indicator(IndicatorRef(id=r["id"], params=r.get("params", {}))) for r in refs]


def resolve(config: StrategyConfig, settings: Settings | None = None) -> dict[str, Any]:
    """Validate a config and return its fully resolved, JSON-serialisable form."""
    s = settings or get_settings()
    exits = [_resolve_exit(e) for e in config.exit_rules()]
    sz = config.sizing or SizingRule()
    sz_decl = get_component(sz.id)
    if "sizing" not in sz_decl.uses:
        raise ValueError(f"{sz.id} is not a sizing rule")
    sz_params = sz_decl.resolve_params(sz.params)
    sizing: dict[str, Any] = {"id": sz_decl.id, "version": sz_decl.version, "params": sz_params}
    sz_req = _requirements(sz_decl.requires(sz_params))
    if sz_req:
        sizing["requires"] = sz_req
    rank = None
    if config.rank_by is not None:
        rank = {
            "indicator": _resolve_indicator(config.rank_by.indicator),
            "order": config.rank_by.order,
        }
    p = config.portfolio
    resolved: dict[str, Any] = {
        "name": config.name,
        "universe": list(config.universe),
        "benchmark": config.benchmark.upper(),
        "timeframe": config.timeframe or "1d",
        "start": config.start.isoformat() if config.start else None,
        "end": config.end.isoformat() if config.end else None,
        "entry": [_resolve_condition(c, "entry") for c in config.entry],
        "entry_combine": "all",
        "entry_fill": config.entry_fill.model_dump(),
        # "exit" is the first exit rule, kept for readers of the single-exit shape;
        # "exits" is the full list the engine uses.
        "exit": exits[0],
        "exits": exits,
        "sizing": sizing,
        "rank_by": rank,
        "portfolio": {
            "starting_cash": p.starting_cash if p.starting_cash is not None else s.starting_cash,
            "target_allocation": p.target_allocation
            if p.target_allocation is not None
            else s.target_allocation,
            "max_positions": p.max_positions if p.max_positions is not None else s.max_positions,
            "cost_bps_per_side": p.cost_bps_per_side
            if p.cost_bps_per_side is not None
            else s.cost_bps_per_side,
            "idle_cash": p.idle_cash or "cash",
            "long_only": True,
            "whole_shares": True,
            "one_position_per_symbol": True,
        },
        "execution": {
            "engine_version": ENGINE_VERSION,
            "calendar": "XNYS",
            "signal_time": "after completed close",
            "fill": _fill_text(config.entry_fill),
            "sizing": "the sizing rule's whole-share quantity at the decision close",
            "order_of_processing": "exits before entries",
            "exits": (
                "the first exit rule that fires sells the whole position; "
                "conditions on a signal exit are combined with or"
            ),
            "tie_break": (
                "candidates processed in ascending symbol order, or by rank_by "
                "(missing values last, ties in symbol order)"
            ),
            "gap_affordability": (
                "if an entry costs more than available cash at the open, the whole order "
                "is skipped (insufficient_cash_at_open); a smaller quantity is never bought"
            ),
            "missing_bars": "no trade on a session without a bar; orders wait for the next bar",
        },
    }
    if p.idle_cash == "benchmark":
        resolved["execution"]["idle_cash"] = (
            f"cash not needed by a position is held in whole shares of {resolved['benchmark']}: "
            "bought at each open with the cash left, sold at the open just before a buy that "
            "needs the cash; these sweep fills are not trades and use no position slot"
        )
    return resolved


def config_from_resolved(resolved: dict[str, Any]) -> StrategyConfig:
    """Rebuild an editable config from a resolved one (used when duplicating a run)."""

    def ref(r: dict[str, Any]) -> IndicatorRef:
        return IndicatorRef(id=r["id"], params=dict(r["params"]))

    def cond(c: dict[str, Any]) -> Condition:
        return Condition(
            kind=c["kind"],
            params=dict(c["params"]),
            indicator=ref(c["indicator"]),
            other=ref(c["other"]) if c.get("other") else None,
        )

    def exit_rule(ex: dict[str, Any]) -> ExitRule:
        return ExitRule(
            kind=ex["kind"],
            params=dict(ex["params"]),
            conditions=[cond(c) for c in ex["conditions"]],
        )

    exits = resolved_exits(resolved)
    sizing = resolved.get("sizing")
    rank = resolved.get("rank_by")
    return StrategyConfig(
        name=resolved["name"],
        universe=resolved["universe"],
        benchmark=resolved["benchmark"],
        timeframe=resolved.get("timeframe", "1d"),
        start=resolved["start"],
        end=resolved["end"],
        entry=[cond(c) for c in resolved["entry"]],
        entry_fill=EntryFill(**(resolved.get("entry_fill") or {})),
        exit=exit_rule(exits[0]),
        exits=[exit_rule(e) for e in exits] if len(exits) > 1 else None,
        portfolio=PortfolioSettings(
            **{
                k: resolved["portfolio"][k]
                for k in PortfolioSettings.model_fields
                if k in resolved["portfolio"]
            }
        ),
        sizing=SizingRule(id=sizing["id"], params=dict(sizing["params"])) if sizing else None,
        rank_by=RankBy(indicator=ref(rank["indicator"]), order=rank["order"]) if rank else None,
    )


def resolved_exits(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    """The exit rules of a resolved config (runs saved before engine/0.3.0 have one)."""
    exits: list[dict[str, Any]] = resolved.get("exits") or [resolved["exit"]]
    return exits


def resolved_sizing(resolved: dict[str, Any]) -> dict[str, Any]:
    """The sizing rule of a resolved config (before engine/0.3.0: percent of equity)."""
    default = {"id": "percent_of_equity", "version": "1.0.0", "params": {}}
    sizing: dict[str, Any] = resolved.get("sizing") or default
    return sizing


def all_conditions(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    conds = list(resolved["entry"])
    for ex in resolved_exits(resolved):
        conds += ex["conditions"]
    return conds


def required_indicators(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    """Every indicator a run needs computed: conditions (both sides), exit and sizing
    requirements, and the ranking indicator."""
    inds: list[dict[str, Any]] = []
    for c in all_conditions(resolved):
        inds.append(c["indicator"])
        if c.get("other"):
            inds.append(c["other"])
    for ex in resolved_exits(resolved):
        inds += ex.get("requires", [])
    inds += resolved_sizing(resolved).get("requires", [])
    rank = resolved.get("rank_by")
    if rank:
        inds.append(rank["indicator"])
    return inds


def config_hash(resolved: dict[str, Any]) -> str:
    blob = json.dumps({k: v for k, v in resolved.items() if k != "name"}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class VersionMismatch(RuntimeError):
    pass


def check_versions(resolved: dict[str, Any]) -> None:
    """Raise if the components or engine a resolved config names have changed version."""
    problems = []
    engine = resolved["execution"]["engine_version"]
    if engine != ENGINE_VERSION and engine not in COMPATIBLE_ENGINE_VERSIONS:
        problems.append(f"engine {engine} -> {ENGINE_VERSION}")
    conds = all_conditions(resolved)
    items = [(c["kind"], c["version"]) for c in conds]
    items += [(i["id"], i["version"]) for i in required_indicators(resolved)]
    items += [(e["kind"], e["version"]) for e in resolved_exits(resolved) if e["kind"] != "signal"]
    sz = resolved_sizing(resolved)
    items.append((sz["id"], sz["version"]))
    for cid, ver in items:
        cur = get_component(cid).version
        if cur != ver:
            problems.append(f"{cid} {ver} -> {cur}")
    if problems:
        raise VersionMismatch("cannot reproduce exactly: " + "; ".join(sorted(set(problems))))


def _flatten_for_test(d: dict[str, Any]) -> dict[str, Any]:
    from moneylab.engine.reporting import _flatten

    return _flatten(d)
