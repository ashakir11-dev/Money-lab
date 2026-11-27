"""Adapt campaign rules to numbers the engine already computes.

Terminal excess and drawdown come from ``reporting.metrics``. The active-excess
series uses the equity ledger's daily returns and exposure. Settings diffs use
``reporting.config_diff``. Nothing here invents sector, VIX, or index-weight joins.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from moneylab.decision.rules import SERIES_ACTIVE, SERIES_TERMINAL, Measurement, concentration_share
from moneylab.engine.batch import Job
from moneylab.engine.config import StrategyConfig, resolve
from moneylab.engine.reporting import config_diff, first_difference, metrics


def measurement_from_equity(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    timeframe: str = "1d",
    distinct_dates: int = 0,
    folds: int = 1,
    scope: str = "development",
) -> Measurement:
    """Build the two bound series from a finished equity ledger.

    ``active_excess_pp`` averages strategy-minus-benchmark daily returns on sessions
    that began invested. Sessions spent in cash are left out, so a rising benchmark
    while the book is in cash does not become the falsifier.
    """
    own = metrics(equity, fills, timeframe=timeframe)
    bench = metrics(equity, fills, column="benchmark_equity", timeframe=timeframe)
    terminal = (own["total_return"] - bench["total_return"]) * 100.0
    strategy = equity["equity"].to_numpy(dtype=float)
    benchmark = equity["benchmark_equity"].to_numpy(dtype=float)
    exposure = (equity["positions_value"] / equity["equity"]).to_numpy(dtype=float)
    strat_ret = np.diff(strategy) / strategy[:-1]
    bench_ret = np.diff(benchmark) / benchmark[:-1]
    excess = strat_ret - bench_ret
    invested = exposure[:-1] > 0
    active = float(excess[invested].mean() * 100.0) if invested.any() else 0.0
    daily = tuple(float(x) for x in excess)
    return Measurement(
        values={SERIES_TERMINAL: float(terminal), SERIES_ACTIVE: active},
        drawdown=abs(float(own["max_drawdown"])),
        distinct_dates=distinct_dates,
        folds=folds,
        scope=scope,
        concentration=concentration_share(daily),
        exposure=float(own["avg_exposure"]),
        daily_excess=daily,
    )


def _base_entry(resolved: dict[str, Any]) -> list[Any]:
    entry = list(resolved["entry"])
    signals = list(resolved.get("entry_signals") or [])
    if signals and entry[-len(signals) :] == signals:
        return entry[: -len(signals)]
    return entry


def execution_signature(resolved: dict[str, Any]) -> dict[str, Any]:
    """Everything a control must share with the seed, except the entry predicate."""
    return {
        "universe": resolved["universe"],
        "benchmark": resolved["benchmark"],
        "timeframe": resolved.get("timeframe"),
        "start": resolved.get("start"),
        "end": resolved.get("end"),
        "entry_fill": resolved.get("entry_fill"),
        "exits": resolved.get("exits"),
        "sizing": resolved.get("sizing"),
        "rank_by": resolved.get("rank_by"),
        "portfolio": resolved.get("portfolio"),
    }


def changed_axes(parent: dict[str, Any], child: dict[str, Any]) -> list[str]:
    """Semantic axes touched by a child recipe. Date windows are not an axis."""
    axes: list[str] = []
    if _base_entry(parent) != _base_entry(child):
        axes.append("entry")
    if (parent.get("entry_signals") or []) != (child.get("entry_signals") or []):
        axes.append("filter")
    if parent.get("exits") != child.get("exits"):
        axes.append("exit")
    if parent.get("rank_by") != child.get("rank_by"):
        axes.append("ranking")
    portfolio_axes = (
        ("max_positions", "capacity"),
        ("target_allocation", "sizing"),
        ("idle_cash", "cash"),
        ("cost_bps_per_side", "costs"),
        ("starting_cash", "capital"),
    )
    parent_book = parent.get("portfolio") or {}
    child_book = child.get("portfolio") or {}
    for field_name, axis in portfolio_axes:
        if parent_book.get(field_name) != child_book.get(field_name):
            axes.append(axis)
    if parent.get("sizing") != child.get("sizing") and "sizing" not in axes:
        axes.append("sizing")
    if parent["universe"] != child["universe"]:
        axes.append("universe")
    if parent["benchmark"] != child["benchmark"]:
        axes.append("benchmark")
    if parent.get("entry_fill") != child.get("entry_fill"):
        axes.append("fill")
    return axes


def settings_diff(
    parent: dict[str, Any],
    child: dict[str, Any],
    ledgers: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Settings diff, plus the first diverging decision when ledgers are passed."""
    out: dict[str, Any] = {"settings": config_diff(parent, child)}
    if ledgers is None:
        return out
    out["first_divergence"] = first_difference(
        ledgers["decisions_a"],
        ledgers["decisions_b"],
        ledgers["fills_a"],
        ledgers["fills_b"],
        ledgers["equity_a"],
        ledgers["equity_b"],
    )
    return out


def seed_jobs(recipe: dict[str, Any], control: dict[str, Any], data_key: int = 0) -> list[Job]:
    """The funded pair a batch would run. Both jobs share one snapshot key."""
    return [
        Job(resolved=recipe, data_key=data_key),
        Job(resolved=control, data_key=data_key),
    ]


def fold_windows(resolved: dict[str, Any], folds: list[tuple[date, date]]) -> list[dict[str, Any]]:
    """One flat-start config per fold. The engine already honors start and end."""
    copies: list[dict[str, Any]] = []
    for start, end in folds:
        copy_resolved = copy.deepcopy(resolved)
        copy_resolved["start"] = start.isoformat()
        copy_resolved["end"] = end.isoformat()
        execution = dict(copy_resolved.get("execution") or {})
        execution["fold_boundary"] = "flatten_and_restart"
        copy_resolved["execution"] = execution
        copies.append(copy_resolved)
    return copies


def resolve_recipe(config: StrategyConfig) -> dict[str, Any]:
    return resolve(config)
