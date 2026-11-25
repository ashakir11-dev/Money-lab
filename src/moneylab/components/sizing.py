"""Position sizing rules beyond the default percent of equity.

Each rule returns whole shares from the decision close; 0 means the engine records
the entry as skipped with reason ``zero_quantity``.
"""

from __future__ import annotations

import math
from typing import Any

from moneylab.components.registry import (
    ParamSpec,
    SizingContext,
    indicator_key,
    register_sizing,
)


@register_sizing(
    id="fixed_dollar",
    version="1.0.0",
    label="Fixed dollar amount",
    params=(ParamSpec("amount", "float", 10000.0, "Amount per entry", min=1),),
    description=(
        "floor(amount / decision close): the same dollar amount for every new entry, in "
        "whole shares, whatever the account's equity."
    ),
)
def fixed_dollar(ctx: SizingContext, params: dict[str, Any]) -> int:
    return int(float(params["amount"]) // ctx.close)


def atr_key(length: int) -> str:
    """The key under which the engine passes ATR(length) in ``SizingContext.values``."""
    return indicator_key({"id": "atr", "params": {"length": int(length)}})


@register_sizing(
    id="volatility",
    version="1.0.0",
    label="Volatility (ATR risk budget)",
    params=(
        ParamSpec(
            "risk_fraction",
            "float",
            0.01,
            "Risk per entry (fraction of equity)",
            min=0.0001,
            max=0.2,
        ),
        ParamSpec("atr_length", "int", 14, "ATR lookback (sessions)", min=1, max=200),
        ParamSpec("atr_multiple", "float", 2.0, "Stop distance (x ATR)", min=0.1, max=20),
    ),
    description=(
        "qty = floor(risk_fraction x equity / (atr_multiple x ATR(atr_length))), using "
        "equity and Wilder ATR at the decision close, so a move of atr_multiple ATRs "
        "against the position costs about risk_fraction of equity. Capped so the "
        "position never exceeds the target allocation: qty = min(qty, "
        "floor(target_allocation x equity / close)). While ATR is still warming up (or "
        "is 0) the quantity is 0 and the entry is skipped as zero_quantity."
    ),
    requires=lambda p: [{"id": "atr", "params": {"length": p["atr_length"]}}],
)
def volatility(ctx: SizingContext, params: dict[str, Any]) -> int:
    a = ctx.values.get(atr_key(params["atr_length"]), math.nan)
    if math.isnan(a) or a <= 0:
        return 0
    risk = float(params["risk_fraction"]) * ctx.equity
    qty = int(risk // (float(params["atr_multiple"]) * a))
    cap = int((ctx.target_allocation * ctx.equity) // ctx.close)
    return min(qty, cap)
