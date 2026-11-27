"""Strategy versions: frozen rules, with runs attached afterwards.

A version stores the rules, sizing, costs, portfolio policy, benchmark and an
explicit timeframe. Universe, dates and the snapshot belong to each run. The
score of a run is its own total return minus its own benchmark's buy-and-hold
return over that same window.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, TypeGuard

from moneylab.engine.config import resolved_exits, resolved_sizing

PORTFOLIO_KEYS = (
    "starting_cash",
    "target_allocation",
    "max_positions",
    "cost_bps_per_side",
    "idle_cash",
)


def frozen_rules(resolved: dict[str, Any]) -> dict[str, Any]:
    """The part of a resolved config that defines a strategy version."""
    portfolio = resolved.get("portfolio") or {}
    return {
        "benchmark": resolved.get("benchmark"),
        "timeframe": resolved.get("timeframe"),
        "entry": resolved.get("entry") or [],
        "entry_fill": resolved.get("entry_fill"),
        "exits": resolved_exits(resolved),
        "sizing": resolved_sizing(resolved),
        "rank_by": resolved.get("rank_by"),
        "portfolio": {key: portfolio.get(key) for key in PORTFOLIO_KEYS},
    }


def rules_hash(rules: dict[str, Any]) -> str:
    blob = json.dumps(rules, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def run_gap(metrics: dict[str, Any] | None) -> float | None:
    """This run's return minus its own benchmark return. None when either is missing."""
    metrics = metrics or {}
    bench = metrics.get("benchmark") or {}
    left = metrics.get("total_return")
    right = bench.get("total_return")
    if not _finite(left) or not _finite(right):
        return None
    return float(left) - float(right)


def _finite(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
