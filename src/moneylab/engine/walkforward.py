"""Walk-forward fitting inside the simulator.

Each fold fits one declared knob on the prior window, then trades the next window
from a fresh account. The train window is not scored. A date cut with no fit is not
this path.
"""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd

from moneylab.data.snapshot import MarketData
from moneylab.engine.simulator import RunResult, simulate

_SMALL_CAP = 10


def simulate_walk_forward(resolved: dict[str, Any], data: MarketData) -> RunResult:
    spec = resolved.get("walk_forward") or {}
    knob = str(spec.get("knob") or "")
    grid = [spec_value for spec_value in spec.get("grid") or []]
    train_n = int(spec.get("train_sessions") or 0)
    test_n = int(spec.get("test_sessions") or 0)
    if not knob or not grid or train_n < 2 or test_n < 2:
        raise ValueError("walk_forward needs a knob, a grid, and train and test lengths")
    wide = bool(spec.get("wide"))
    survivor = bool(spec.get("oos_survivor"))
    if wide and not survivor:
        raise ValueError("wide walk-forward before an out-of-sample survivor")
    if len(grid) > _SMALL_CAP and not survivor:
        raise ValueError("walk_forward grid is above 10 without an out-of-sample survivor")
    if train_n + test_n > len(data.sessions):
        raise ValueError("walk_forward window is longer than the data")

    pieces: list[RunResult] = []
    folds: list[dict[str, Any]] = []
    cursor = 0
    while cursor + train_n + test_n <= len(data.sessions):
        train = _slice(data, cursor, cursor + train_n)
        test = _slice(data, cursor + train_n, cursor + train_n + test_n)
        scores: dict[str, float] = {}
        for value in grid:
            trained = simulate(apply_knob(resolved, knob, value), train)
            scores[str(value)] = float(trained.equity["equity"].iloc[-1])
        chosen = max(grid, key=lambda value: (scores[str(value)], -float(value)))
        tested = simulate(apply_knob(resolved, knob, chosen), test)
        pieces.append(tested)
        folds.append(
            {
                "chosen": chosen,
                "train_scores": scores,
                "train_start": str(train.sessions[0].date()),
                "train_end": str(train.sessions[-1].date()),
                "test_start": str(test.sessions[0].date()),
                "test_end": str(test.sessions[-1].date()),
                "test_start_equity": float(tested.equity["equity"].iloc[0]),
            }
        )
        cursor += test_n
    equity = _chain(pieces, float(resolved["portfolio"]["starting_cash"]))
    decisions = (
        pd.concat([piece.decisions for piece in pieces], ignore_index=True)
        if pieces
        else pd.DataFrame()
    )
    fills = (
        pd.concat([piece.fills for piece in pieces], ignore_index=True)
        if pieces
        else pd.DataFrame()
    )
    return RunResult(
        decisions,
        fills,
        equity,
        {"walk_forward": folds, "cost_bps_per_side": resolved["portfolio"]["cost_bps_per_side"]},
    )


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("walk-forward grid values must be numbers")
    return int(value)


def apply_knob(resolved: dict[str, Any], knob: str, value: object) -> dict[str, Any]:
    """One fitted copy. The walk-forward block is removed so the fold does not refit."""
    out = copy.deepcopy(resolved)
    out.pop("walk_forward", None)
    if knob != "exit.holding_period.sessions":
        raise ValueError(f"unsupported walk-forward knob {knob}")
    sessions = _whole(value)
    for rule in out.get("exits") or []:
        if rule.get("kind") == "holding_period":
            rule["params"]["sessions"] = sessions
    exit_rule = out.get("exit") or {}
    if exit_rule.get("kind") == "holding_period":
        exit_rule["params"]["sessions"] = sessions
    return out


def _slice(data: MarketData, start: int, end: int) -> MarketData:
    sessions = pd.DatetimeIndex(data.sessions[start:end])
    frames = {symbol: frame.reindex(sessions) for symbol, frame in data.frames.items()}
    first: dict[str, int] = {}
    for symbol, frame in frames.items():
        present = frame["close"].notna().to_numpy().nonzero()[0]
        first[symbol] = int(present[0]) if len(present) else len(sessions)
    return MarketData(sessions, frames, first)


def _chain(pieces: list[RunResult], starting_cash: float) -> pd.DataFrame:
    """Compound test-window returns. Each fold's account started flat; train days are omitted."""
    rows: list[dict[str, Any]] = []
    equity = starting_cash
    benchmark = starting_cash
    for piece in pieces:
        frame = piece.equity
        if frame.empty:
            continue
        if not rows:
            rows.append(
                {
                    "session": frame["session"].iloc[0],
                    "cash": starting_cash,
                    "positions_value": 0.0,
                    "equity": starting_cash,
                    "benchmark_equity": starting_cash,
                }
            )
        for index in range(len(frame) - 1):
            previous = float(frame["equity"].iloc[index])
            current = float(frame["equity"].iloc[index + 1])
            step = 0.0 if previous == 0 else current / previous - 1.0
            equity *= 1.0 + step
            if "benchmark_equity" in frame.columns:
                previous_bench = float(frame["benchmark_equity"].iloc[index])
                current_bench = float(frame["benchmark_equity"].iloc[index + 1])
                bench_step = 0.0 if previous_bench == 0 else current_bench / previous_bench - 1.0
                benchmark *= 1.0 + bench_step
            exposure = 0.0
            if current:
                exposure = float(frame["positions_value"].iloc[index + 1]) / current
            rows.append(
                {
                    "session": frame["session"].iloc[index + 1],
                    "cash": equity * (1.0 - exposure),
                    "positions_value": equity * exposure,
                    "equity": equity,
                    "benchmark_equity": benchmark,
                }
            )
    return pd.DataFrame(rows)
