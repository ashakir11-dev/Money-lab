"""Price-level exit rules: stop loss, take profit and trailing stop.

Each rule compares the session's close with a level derived from the open position:

* ``stop_loss``: fires when close <= entry_price x (1 - pct).
* ``take_profit``: fires when close >= entry_price x (1 + pct).
* ``trailing_stop``: fires when close <= peak_close x (1 - pct).

``entry_price`` is the fill price (the open the position was bought at). ``peak_close``
is the highest close from the fill session up to and including the session being
checked; the engine maintains it, so a new high on the same session raises the level
before the comparison.

Timing is the same as for every decision: a rule is checked at the close and the whole
position is sold at the next session's open. Nothing guarantees that open is at the
level: if the price gaps down overnight, the sale happens at that lower open. A session
without a bar for the symbol (close is NaN) never fires; the rule is checked again on
the next bar.

The level is rounded to 10 decimal places before the comparison so binary floating
point noise (10 x (1 - 0.08) = 9.200000000000001) cannot decide whether a close exactly
at the level fires: it always does. The values recorded on the decision (close,
entry_price, level, peak_close) are rounded to 6 decimal places.
"""

from __future__ import annotations

import math
from typing import Any

from moneylab.components.registry import ExitContext, ParamSpec, register_exit


def _pct(default: float) -> tuple[ParamSpec, ...]:
    return (
        ParamSpec(
            "pct",
            "float",
            default,
            "Percent (as a fraction)",
            min=0.001,
            max=0.9,
            help="A fraction of the reference price: 0.08 means 8%.",
        ),
    )


def _level(ref: float, factor: float) -> float:
    return round(ref * factor, 10)


def _num(x: float) -> str:
    """A price or percentage for a reason string: at most 6 decimals, no trailing zeros."""
    return f"{round(x, 6):.6f}".rstrip("0").rstrip(".")


def _extra(ctx: ExitContext, level: float, **more: float) -> dict[str, Any]:
    out = {"close": ctx.close, "entry_price": ctx.entry_price, "level": level, **more}
    return {k: round(float(v), 6) for k, v in out.items()}


@register_exit(
    id="stop_loss",
    version="1.0.0",
    label="Stop loss",
    params=_pct(0.08),
    description=(
        "Fires when the close is at or below entry price x (1 - pct); the entry price is "
        "the fill price. Checked at the close and sold at the next open, like every "
        "decision, so a gap down sells at that lower open, not at the level."
    ),
)
def stop_loss(ctx: ExitContext, params: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if math.isnan(ctx.close):
        return None
    pct = float(params["pct"])
    level = _level(ctx.entry_price, 1 - pct)
    if ctx.close <= level:
        return (
            f"stop_loss: close {_num(ctx.close)} <= {_num(level)} "
            f"({_num(pct * 100)}% below entry {_num(ctx.entry_price)})",
            _extra(ctx, level),
        )
    return None


@register_exit(
    id="take_profit",
    version="1.0.0",
    label="Take profit",
    params=_pct(0.15),
    description=(
        "Fires when the close is at or above entry price x (1 + pct); the entry price is "
        "the fill price. Checked at the close and sold at the next open, like every "
        "decision, so the sale price can be above or below the level."
    ),
)
def take_profit(ctx: ExitContext, params: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if math.isnan(ctx.close):
        return None
    pct = float(params["pct"])
    level = _level(ctx.entry_price, 1 + pct)
    if ctx.close >= level:
        return (
            f"take_profit: close {_num(ctx.close)} >= {_num(level)} "
            f"({_num(pct * 100)}% above entry {_num(ctx.entry_price)})",
            _extra(ctx, level),
        )
    return None


@register_exit(
    id="trailing_stop",
    version="1.0.0",
    label="Trailing stop",
    params=_pct(0.10),
    description=(
        "Fires when the close is at or below peak close x (1 - pct), where the peak close "
        "is the highest close from the fill session up to and including this session. "
        "Checked at the close and sold at the next open, like every decision, so a gap "
        "down sells at that lower open, not at the level."
    ),
)
def trailing_stop(ctx: ExitContext, params: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if math.isnan(ctx.close):
        return None
    pct = float(params["pct"])
    level = _level(ctx.peak_close, 1 - pct)
    if ctx.close <= level:
        return (
            f"trailing_stop: close {_num(ctx.close)} <= {_num(level)} "
            f"({_num(pct * 100)}% below peak close {_num(ctx.peak_close)})",
            _extra(ctx, level, peak_close=ctx.peak_close),
        )
    return None
