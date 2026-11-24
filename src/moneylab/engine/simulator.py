"""Deterministic daily simulator shared by historical and (future) paper modes.

Timeline for each exchange session ``t``:

1. Pending orders fill at the open of ``t`` (exits first, then entries in ascending
   symbol order). An order for a symbol without a bar on ``t`` waits.
2. Equity is marked at the close of ``t``.
3. Signals are evaluated on data up to and including the close of ``t`` and new orders
   are queued for the next eligible open. Only data at or before ``t`` is read, so
   future prices cannot reach a decision.

Every candidate decision is recorded with the signal values it saw, the conditions'
results and, when no trade follows, the skip reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np
import pandas as pd

from moneylab.components.registry import get_component
from moneylab.data.snapshot import MarketData

# Decision actions
ENTER = "enter"
EXIT = "exit"
SKIP = "skip"
NO_SIGNAL = "no_signal"  # diagnostic: some entry conditions passed, not all
INELIGIBLE = "ineligible"  # diagnostic: warm-up or missing bar
# Actions that are trading decisions (compared between runs). The others explain them.
DECISION_ACTIONS = (ENTER, EXIT, SKIP)


@dataclass
class _Position:
    qty: int
    fill_idx: int
    entry_price: float


@dataclass
class _Order:
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    decision_idx: int
    decision_id: int


@dataclass
class RunResult:
    decisions: pd.DataFrame
    fills: pd.DataFrame
    equity: pd.DataFrame
    meta: dict[str, Any] = field(default_factory=dict)


def _ind_key(ind: dict[str, Any]) -> str:
    args = ",".join(f"{k}={v}" for k, v in sorted(ind["params"].items()))
    return f"{ind['id']}({args})"


def _cond_label(c: dict[str, Any]) -> str:
    p = c["params"]
    if c["kind"] == "price_cross":
        return f"close crosses {p['direction']} {_ind_key(c['indicator'])}"
    return f"{_ind_key(c['indicator'])} {p['op']} {p['value']:g}"


def _compute_indicators(
    data: MarketData, conditions: list[dict[str, Any]]
) -> dict[str, dict[str, np.ndarray]]:
    """symbol -> indicator key -> values aligned to sessions. Computed once per run."""
    needed = {_ind_key(c["indicator"]): c["indicator"] for c in conditions}
    out: dict[str, dict[str, np.ndarray]] = {}
    for sym, frame in data.frames.items():
        out[sym] = {}
        for key, ind in needed.items():
            decl = get_component(ind["id"])
            assert decl.compute is not None
            out[sym][key] = decl.compute(frame, ind["params"]).to_numpy(dtype=float)
    return out


def _evaluate(
    conds: list[dict[str, Any]],
    ind: dict[str, np.ndarray],
    close: np.ndarray,
    i: int,
) -> tuple[bool | None, list[dict[str, Any]], dict[str, float | None]]:
    """Evaluate all conditions at session i. Returns (passed | None if not evaluable)."""
    results: list[dict[str, Any]] = []
    values: dict[str, float | None] = {"close": _f(close[i])}
    evaluable = True
    for c in conds:
        key = _ind_key(c["indicator"])
        v = ind[key][i]
        values[key] = _f(v)
        p = c["params"]
        r: dict[str, Any] = {"condition": _cond_label(c), "indicator": key, "observed": _f(v)}
        if c["kind"] == "price_cross":
            pv = ind[key][i - 1] if i > 0 else np.nan
            pc = close[i - 1] if i > 0 else np.nan
            values[f"prev {key}"] = _f(pv)
            values["prev close"] = _f(pc)
            if any(math.isnan(x) for x in (v, pv, pc, close[i])):
                r["passed"] = None
            elif p["direction"] == "above":
                r["passed"] = bool(pc <= pv and close[i] > v)
            else:
                r["passed"] = bool(pc >= pv and close[i] < v)
        else:
            r["threshold"] = p["value"]
            r["op"] = p["op"]
            if math.isnan(v):
                r["passed"] = None
            elif p["op"] == "below":
                r["passed"] = bool(v < p["value"])
            else:
                r["passed"] = bool(v > p["value"])
        if r["passed"] is None:
            evaluable = False
        results.append(r)
    if not evaluable:
        return None, results, values
    return all(r["passed"] for r in results), results, values


def _cents(x: float) -> float:
    """Round half-up to the cent (deterministic, matches hand calculation)."""
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _f(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 6)


def simulate(resolved: dict[str, Any], data: MarketData) -> RunResult:
    port = resolved["portfolio"]
    cost_rate = float(port["cost_bps_per_side"]) / 10_000.0
    target = float(port["target_allocation"])
    max_pos = int(port["max_positions"])
    entry_conds = resolved["entry"]
    exit_rule = resolved["exit"]
    exit_conds = exit_rule["conditions"]
    hold_n = int(exit_rule["params"].get("sessions", 0))
    symbols = sorted(resolved["universe"])

    ind = _compute_indicators(data, entry_conds + exit_conds)
    opens = {s: data.frames[s]["open"].to_numpy(dtype=float) for s in symbols}
    closes = {s: data.frames[s]["close"].to_numpy(dtype=float) for s in symbols}
    sessions = data.sessions

    cash = float(port["starting_cash"])
    positions: dict[str, _Position] = {}
    pending: dict[str, _Order] = {}
    last_close: dict[str, float] = {}
    decisions: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []

    def record(i: int, sym: str, action: str, reason: str, **extra: Any) -> int:
        did = len(decisions)
        decisions.append(
            {
                "decision_id": did,
                "session": sessions[i].date().isoformat(),
                "symbol": sym,
                "action": action,
                "reason": reason,
                **extra,
            }
        )
        return did

    for i in range(len(sessions)):
        day = sessions[i].date().isoformat()
        # 1. Fills at the open: exits first, then entries, each in ascending symbol order.
        for side in ("sell", "buy"):
            for sym in sorted(s for s, o in pending.items() if o.side == side):
                o = pending[sym]
                px = opens[sym][i]
                if math.isnan(px):
                    continue  # no bar: never invent a price, wait for the next session
                del pending[sym]
                if side == "sell":
                    pos = positions.pop(sym)
                    gross = pos.qty * px
                    cost = _cents(gross * cost_rate)
                    cash = _cents(cash + gross - cost)
                    fills.append(
                        _fill(day, sym, "sell", pos.qty, px, cost, cash, o, i - o.decision_idx)
                    )
                    continue
                qty = o.qty
                note = ""
                if qty * px * (1 + cost_rate) > cash:
                    qty = int(cash // (px * (1 + cost_rate)))
                    note = f"reduced from {o.qty} to {qty}: opening price {px:.2f} > cash"
                if qty <= 0:
                    record(i, sym, SKIP, "insufficient_cash_at_open", order_qty=o.qty, open=px)
                    continue
                gross = qty * px
                cost = _cents(gross * cost_rate)
                cash = _cents(cash - gross - cost)
                positions[sym] = _Position(qty, i, px)
                fills.append(
                    _fill(day, sym, "buy", qty, px, cost, cash, o, i - o.decision_idx, note)
                )

        # 2. Mark to market at the close (last known close values a symbol without a bar).
        for sym in symbols:
            if not math.isnan(closes[sym][i]):
                last_close[sym] = closes[sym][i]
        pos_value = sum(p.qty * last_close[s] for s, p in positions.items())
        equity = _cents(cash + pos_value)
        equity_rows.append(
            {
                "session": day,
                "cash": cash,
                "positions_value": _cents(pos_value),
                "equity": equity,
                "positions": len(positions),
            }
        )

        # 3a. Exit decisions for held positions.
        exiting = 0
        for sym in sorted(positions):
            if sym in pending:
                exiting += 1
                continue
            pos = positions[sym]
            held = i - pos.fill_idx
            if exit_rule["kind"] == "holding_period":
                if held >= hold_n:
                    did = record(i, sym, EXIT, f"held {held} sessions >= {hold_n}", qty=pos.qty)
                    pending[sym] = _Order(sym, "sell", pos.qty, i, did)
                    exiting += 1
            else:
                if math.isnan(closes[sym][i]):
                    continue
                ok, res, vals = _evaluate(exit_conds, ind[sym], closes[sym], i)
                if ok:
                    did = record(
                        i, sym, EXIT, "exit signal", qty=pos.qty, conditions=res, values=vals
                    )
                    pending[sym] = _Order(sym, "sell", pos.qty, i, did)
                    exiting += 1

        # 3b. Entry decisions, ascending symbol order (documented tie-break).
        committed = sum(
            o.qty * closes[o.symbol][o.decision_idx] * (1 + cost_rate)
            for o in pending.values()
            if o.side == "buy"
        )
        expected_proceeds = sum(
            positions[s].qty * last_close[s] * (1 - cost_rate)
            for s, o in pending.items()
            if o.side == "sell"
        )
        for sym in symbols:
            if i < data.first_bar[sym]:
                continue  # not yet listed in the snapshot
            c = closes[sym][i]
            if math.isnan(c):
                record(i, sym, INELIGIBLE, "missing_bar")
                continue
            ok, res, vals = _evaluate(entry_conds, ind[sym], closes[sym], i)
            if ok is None:
                if all(r["passed"] is not False for r in res):
                    record(i, sym, INELIGIBLE, "warmup", conditions=res, values=vals)
                continue
            if not ok:
                # Keep near misses (some but not all conditions passed) so comparisons can
                # show exactly which condition blocked an entry.
                if any(r["passed"] for r in res):
                    failed = [r["condition"] for r in res if not r["passed"]]
                    record(
                        i,
                        sym,
                        NO_SIGNAL,
                        "failed: " + "; ".join(failed),
                        conditions=res,
                        values=vals,
                    )
                continue
            if sym in positions or sym in pending:
                record(i, sym, SKIP, "already_held", conditions=res, values=vals)
                continue
            open_slots = (
                max_pos
                - (len(positions) - exiting)
                - sum(1 for o in pending.values() if o.side == "buy")
            )
            if open_slots <= 0:
                record(i, sym, SKIP, "max_positions", conditions=res, values=vals)
                continue
            qty = int((target * equity) // c)
            if qty <= 0:
                record(i, sym, SKIP, "zero_quantity", conditions=res, values=vals)
                continue
            est = qty * c * (1 + cost_rate)
            available = cash + expected_proceeds - committed
            if est > available:
                record(
                    i,
                    sym,
                    SKIP,
                    "insufficient_cash",
                    conditions=res,
                    values=vals,
                    qty=qty,
                    est_cost=round(est, 2),
                    available=round(available, 2),
                )
                continue
            did = record(
                i,
                sym,
                ENTER,
                "all entry conditions passed",
                conditions=res,
                values=vals,
                qty=qty,
                est_cost=round(est, 2),
            )
            pending[sym] = _Order(sym, "buy", qty, i, did)
            committed += est

    dec = pd.DataFrame(decisions)
    if dec.empty:
        dec = pd.DataFrame(columns=["decision_id", "session", "symbol", "action", "reason"])
    fl = pd.DataFrame(fills)
    if fl.empty:
        fl = pd.DataFrame(
            columns=["session", "symbol", "side", "qty", "price", "cost", "cash_after"]
        )
    eq = pd.DataFrame(equity_rows)
    bench = resolved["benchmark"]
    if bench in data.frames:
        bc = data.frames[bench]["close"].ffill().to_numpy(dtype=float)
        first = bc[~np.isnan(bc)][0]
        eq["benchmark_equity"] = np.round(float(port["starting_cash"]) * bc / first, 2)
    return RunResult(dec, fl, eq, {"open_positions": {s: p.qty for s, p in positions.items()}})


def _fill(
    day: str,
    sym: str,
    side: str,
    qty: int,
    px: float,
    cost: float,
    cash: float,
    o: _Order,
    wait: int,
    note: str = "",
) -> dict[str, Any]:
    return {
        "session": day,
        "symbol": sym,
        "side": side,
        "qty": qty,
        "price": round(px, 6),
        "gross": _cents(qty * px),
        "cost": cost,
        "cash_after": cash,
        "decision_id": o.decision_id,
        "sessions_waited": wait,
        "note": note,
    }
