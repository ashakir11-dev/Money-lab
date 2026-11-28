"""Portfolio estimands that sit beside the historical active-excess series.

``active_excess_pp`` is unchanged. It is an arithmetic mean of strategy-minus-benchmark
bar returns on bars that began invested, in percentage points. When nothing was invested
that historical series is 0.0. ``active_excess_pp_v2`` is the same mean, and it is
undefined when exposure is zero.

Exposure-matched benchmarks use start-of-bar weights. Cash earns the supplied cash
return, which is zero unless the caller passes one. They are not ``active_excess_pp``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from moneylab.data.bars import bars_per_year
from moneylab.decision.measure import measurement_from_equity
from moneylab.decision.rules import SERIES_ACTIVE
from moneylab.engine.analytics import round_trips, trade_stats
from moneylab.engine.reporting import metrics

ACTIVE_EXCESS_NOTES = (
    "active_excess_pp is the arithmetic mean, in percentage points, of "
    "strategy bar return minus benchmark-account bar return on bars whose start "
    "exposure was positive. It is not a compounded total-return gap and it is not "
    "the exposure-matched SPY excess. Bars spent in cash are left out. Version 1 "
    "returns 0.0 when no bar began invested. Version 2 leaves that case undefined."
)


def _compound(returns: np.ndarray) -> float:
    if returns.size == 0:
        return 0.0
    return float(np.prod(1.0 + returns) - 1.0)


def _cagr(start: float, end: float, n_returns: int, timeframe: str) -> float | None:
    if n_returns < 1 or start <= 0 or end <= 0:
        return None
    years = n_returns / bars_per_year(timeframe)
    if years <= 0:
        return None
    return float((end / start) ** (1.0 / years) - 1.0)


def audit_active_excess(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Versioned reading of the historical active-excess series. The v1 number is not edited."""
    legacy = measurement_from_equity(equity, fills, timeframe=timeframe)
    exposure = (equity["positions_value"] / equity["equity"]).to_numpy(dtype=float)
    invested = exposure[:-1] > 0 if exposure.size else np.array([], dtype=bool)
    v1 = float(legacy.values[SERIES_ACTIVE])
    invested_bars = int(invested.sum()) if invested.size else 0
    undefined = invested_bars == 0
    return {
        "active_excess_pp": v1,
        "active_excess_pp_v2": None if undefined else v1,
        "v2_undefined_reason": "zero_exposure" if undefined else None,
        "unit": "percentage_points_per_invested_bar",
        "aggregation": "arithmetic_mean",
        "invested_bars": invested_bars,
        "notes": ACTIVE_EXCESS_NOTES,
    }


def exposure_matched(
    weights: np.ndarray,
    asset_returns: np.ndarray,
    benchmark_returns: np.ndarray,
    cash_returns: np.ndarray | None = None,
) -> dict[str, Any]:
    """Matched SPY and matched same-stock returns from start-of-bar weights.

    ``weights`` is ``(bars, assets)`` or one column. ``asset_returns`` matches it.
    ``benchmark_returns`` and ``cash_returns`` are one value per bar. A missing cash
    series earns zero. Each matched bar return is compounded as ``product(1 + r) - 1``.
    """
    weight = np.asarray(weights, dtype=float)
    asset = np.asarray(asset_returns, dtype=float)
    bench = np.asarray(benchmark_returns, dtype=float)
    if weight.ndim == 1:
        weight = weight.reshape(-1, 1)
    if asset.ndim == 1:
        asset = asset.reshape(-1, 1)
    cash = (
        np.zeros(bench.shape[0], dtype=float)
        if cash_returns is None
        else np.asarray(cash_returns, dtype=float)
    )
    held = weight.sum(axis=1)
    same = (weight * asset).sum(axis=1) + (1.0 - held) * cash
    spy = held * bench + (1.0 - held) * cash
    return {
        "same_stock_bar_returns": same,
        "spy_bar_returns": spy,
        "same_stock_matched_return": _compound(same),
        "spy_matched_return": _compound(spy),
        "cash_return": "zero" if cash_returns is None else "supplied",
    }


def portfolio_estimands(
    equity: pd.DataFrame,
    fills: pd.DataFrame | None = None,
    *,
    timeframe: str = "1d",
    decisions: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Account results and the benchmark series already on the equity ledger.

    Gross and net differ by the recorded fill costs. A separate gross equity curve is
    not reconstructed. Trade expectancy and profit factor come from closed round trips
    when the fill ledger has the columns those trips need.
    """
    own = metrics(equity, fills if fills is not None else pd.DataFrame(), timeframe=timeframe)
    bench = (
        metrics(equity, pd.DataFrame(), column="benchmark_equity", timeframe=timeframe)
        if "benchmark_equity" in equity
        else None
    )
    exposure = (equity["positions_value"] / equity["equity"]).to_numpy(dtype=float)
    equity_arr = equity["equity"].to_numpy(dtype=float)
    n_returns = max(equity_arr.size - 1, 0)
    costs = float(own.get("costs") or 0.0)
    start = float(equity_arr[0]) if equity_arr.size else None
    trades: dict[str, Any] = {
        "expectancy": None,
        "profit_factor": None,
        "trade_count": int(own.get("trades") or 0),
    }
    fill_cols = {"symbol", "side", "qty", "price"}
    if (
        fills is not None
        and len(fills) > 0
        and fill_cols <= set(fills.columns)
        and "session" in equity
        and "session" in fills
    ):
        stats = trade_stats(round_trips(fills, equity, decisions, timeframe=timeframe))
        trades = {
            "expectancy": stats.get("expectancy"),
            "profit_factor": stats.get("profit_factor"),
            "trade_count": int(stats.get("count") or 0),
            "mean_trade_edge": stats.get("edge"),
        }
    return {
        "total_return": own["total_return"],
        "cagr": (
            _cagr(float(equity_arr[0]), float(equity_arr[-1]), n_returns, timeframe)
            if equity_arr.size
            else None
        ),
        "max_drawdown": own["max_drawdown"],
        "sharpe": own["sharpe"],
        "avg_exposure": own.get("avg_exposure"),
        "invested_time_fraction": float((exposure > 0).mean()) if exposure.size else None,
        "trades": trades["trade_count"],
        "expectancy": trades["expectancy"],
        "profit_factor": trades["profit_factor"],
        "costs": costs,
        "cost_drag_on_start": (costs / start) if start else None,
        "net_total_return": own["total_return"],
        "spy_account_return": None if bench is None else bench["total_return"],
        "active_excess": audit_active_excess(
            equity, fills if fills is not None else pd.DataFrame(), timeframe=timeframe
        ),
    }
