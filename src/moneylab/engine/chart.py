"""Chart slice for one fill.

Indicators are computed on the run's full bar grid, then cut to ``chart_window``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from moneylab.components.registry import get_component, indicator_key
from moneylab.data.bars import session_label
from moneylab.data.snapshot import MarketData
from moneylab.engine.config import required_indicators

BEFORE = 80
AFTER = 40


class ChartRejected(ValueError):
    """The fill exists, but the chart cannot be built. The API returns 422."""


def chart_window(
    n: int, signal_i: int, fill_i: int, *, before: int = BEFORE, after: int = AFTER
) -> tuple[int, int]:
    """Inclusive indexes. 80 bars before the signal and 40 after the fill.

    Both indexes stay inside the slice. The ends clamp to the grid.
    """
    if n <= 0:
        raise ValueError("run has no bars")
    start = max(0, signal_i - before)
    end = min(n - 1, fill_i + after)
    start = min(start, signal_i, fill_i)
    end = max(end, signal_i, fill_i)
    return max(0, start), min(n - 1, end)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 6)


def _candle(row: Any) -> dict[str, float] | None:
    open_ = _finite(row["open"])
    high = _finite(row["high"])
    low = _finite(row["low"])
    close = _finite(row["close"])
    if open_ is None or high is None or low is None or close is None:
        return None
    return {"open": open_, "high": high, "low": low, "close": close}


def _indicator_order(resolved: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    ordered: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for ref in required_indicators(resolved):
        key = indicator_key(ref)
        if key in seen:
            continue
        seen.add(key)
        ordered.append((key, ref))
    return ordered


def _compute_symbol(
    data: MarketData,
    ordered: list[tuple[str, dict[str, Any]]],
    symbol: str,
    universe: list[str],
) -> dict[str, np.ndarray]:
    if symbol not in data.frames:
        raise ChartRejected(f"no data for {symbol} in snapshot")
    members = [name for name in universe if name in data.frames]
    out: dict[str, np.ndarray] = {}
    try:
        for key, ref in ordered:
            decl = get_component(str(ref["id"]))
            if decl.compute_universe is not None:
                frames = {name: data.frames[name] for name in members}
                series = decl.compute_universe(frames, ref["params"])
                out[key] = series.to_numpy(dtype=float)
                continue
            if decl.compute is None:
                raise ChartRejected(f"{ref['id']} has no compute function")
            series = decl.compute(data.frames[symbol], ref["params"])
            out[key] = series.to_numpy(dtype=float)
    except ChartRejected:
        raise
    except Exception as exc:
        raise ChartRejected(str(exc)) from exc
    return out


def assemble_chart(
    resolved: dict[str, Any],
    data: MarketData,
    decision: dict[str, Any],
    fill: dict[str, Any],
) -> dict[str, Any]:
    """Candles and indicator series for one fill, already sliced."""
    symbol = str(fill["symbol"])
    labels = [session_label(ts) for ts in data.sessions]
    signal_session = str(decision["session"])
    fill_session = str(fill["session"])
    if signal_session not in labels:
        raise ChartRejected(f"signal session {signal_session} is not on the run's bar grid")
    if fill_session not in labels:
        raise ChartRejected(f"fill session {fill_session} is not on the run's bar grid")
    signal_i = labels.index(signal_session)
    fill_i = labels.index(fill_session)
    start, end = chart_window(len(labels), signal_i, fill_i)
    window = slice(start, end + 1)
    ordered = _indicator_order(resolved)
    computed = _compute_symbol(data, ordered, symbol, list(resolved["universe"]))
    frame = data.frames[symbol]
    candles: list[dict[str, float] | None] = [
        _candle(frame.loc[ts]) if ts in frame.index else None for ts in data.sessions[window]
    ]
    series: list[dict[str, Any]] = []
    for key, ref in ordered:
        series.append(
            {
                "key": key,
                "price_scale": bool(get_component(str(ref["id"])).price_scale),
                "values": [_finite(value) for value in computed[key][window]],
            }
        )
    conditions = decision.get("conditions")
    values = decision.get("values")
    return {
        "symbol": symbol,
        "timeframe": str(resolved.get("timeframe") or "1d"),
        "sessions": labels[window],
        "candles": candles,
        "series": series,
        "fill": {
            "session": fill_session,
            "side": str(fill["side"]),
            "price": float(fill["price"]),
        },
        "signal": {
            "session": signal_session,
            "action": str(decision.get("action") or ""),
            "reason": str(decision.get("reason") or ""),
            "conditions": conditions if isinstance(conditions, list) else [],
            "values": values if isinstance(values, dict) else {},
        },
    }
