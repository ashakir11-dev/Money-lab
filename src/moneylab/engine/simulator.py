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


def _ind_key(ind: dict[str, Any]) -> str:
    args = ",".join(f"{k}={v}" for k, v in sorted(ind["params"].items()))
    return f"{ind['id']}({args})"


def _cond_label(c: dict[str, Any]) -> str:
    p = c["params"]
    if c["kind"] == "price_cross":
        return f"close crosses {p['direction']} {_ind_key(c['indicator'])}"
    return f"{_ind_key(c['indicator'])} {p['op']} {p['value']:g}"


def _compute_indicators(
    data: MarketData, conditions: list[dict[str, Any]], universe: list[str] | None = None
) -> dict[str, dict[str, np.ndarray]]:
    """symbol -> indicator key -> values aligned to sessions. Computed once per run.

    Cross-sectional indicators (breadth) are computed once over the universe symbols
    and the same array is shared by every symbol.
    """
    needed = {_ind_key(c["indicator"]): c["indicator"] for c in conditions}
    members = sorted(universe) if universe is not None else sorted(data.frames)
    shared: dict[str, np.ndarray] = {}
    for key, ind in needed.items():
        decl = get_component(ind["id"])
        if decl.compute_universe is not None:
            frames = {s: data.frames[s] for s in members if s in data.frames}
            shared[key] = decl.compute_universe(frames, ind["params"]).to_numpy(dtype=float)
    out: dict[str, dict[str, np.ndarray]] = {}
    for sym, frame in data.frames.items():
        out[sym] = {}
        for key, ind in needed.items():
            if key in shared:
                out[sym][key] = shared[key]
                continue
            decl = get_component(ind["id"])
            assert decl.compute is not None
            out[sym][key] = decl.compute(frame, ind["params"]).to_numpy(dtype=float)
    return out


@dataclass(frozen=True)
class _Cond:
    """A resolved condition with its labels worked out once (not per session)."""

    kind: str
    key: str
    label: str
    params: dict[str, Any]


def _compile(conds: list[dict[str, Any]]) -> list[_Cond]:
    return [_Cond(c["kind"], _ind_key(c["indicator"]), _cond_label(c), c["params"]) for c in conds]


def _evaluate(
    conds: list[dict[str, Any]],
    ind: dict[str, np.ndarray],
    close: np.ndarray,
    i: int,
) -> tuple[bool | None, list[dict[str, Any]], dict[str, float | None]]:
    """Evaluate all conditions at session i. Returns (passed | None if not evaluable)."""
    return _evaluate_compiled(_compile(conds), ind, close, i)


def _evaluate_compiled(
    conds: list[_Cond],
    ind: dict[str, np.ndarray],
    close: np.ndarray,
    i: int,
) -> tuple[bool | None, list[dict[str, Any]], dict[str, float | None]]:
    results: list[dict[str, Any]] = []
    c_i = close[i]
    values: dict[str, float | None] = {"close": _f(c_i)}
    evaluable = True
    for c in conds:
        key = c.key
        arr = ind[key]
        v = arr[i]
        fv = _f(v)
        values[key] = fv
        p = c.params
        r: dict[str, Any] = {"condition": c.label, "indicator": key, "observed": fv}
        if c.kind == "price_cross":
            pv = arr[i - 1] if i > 0 else np.nan
            pc = close[i - 1] if i > 0 else np.nan
            values[f"prev {key}"] = _f(pv)
            values["prev close"] = _f(pc)
            if math.isnan(v) or math.isnan(pv) or math.isnan(pc) or math.isnan(c_i):
                r["passed"] = None
            elif p["direction"] == "above":
                r["passed"] = bool(pc <= pv and c_i > v)
            else:
                r["passed"] = bool(pc >= pv and c_i < v)
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


def _entry_candidates(conds: list[_Cond], view: MarketView, symbols: list[str]) -> np.ndarray:
    """Boolean (sessions x symbols): True where the entry step can record something.

    Mirrors the per-symbol branches of ``Account._decide_entries`` with whole-array
    comparisons, so the per-session loop only visits symbols that are missing a bar
    (and listed), in warm-up, a near miss or a full signal. Every other symbol records
    nothing and changes no state, so skipping it cannot change any ledger. The rows
    that are recorded are still built by ``_evaluate_compiled``.
    """
    n = len(view.sessions)
    if not symbols:
        return np.zeros((n, 0), dtype=bool)
    close = np.vstack([view.closes[s] for s in symbols])
    prev_close = np.concatenate([np.full((len(symbols), 1), np.nan), close[:, :-1]], axis=1)
    any_none = np.zeros(close.shape, dtype=bool)
    any_fail = np.zeros(close.shape, dtype=bool)
    any_pass = np.zeros(close.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        for c in conds:
            v = np.vstack([view.indicators[s][c.key] for s in symbols])
            p = c.params
            if c.kind == "price_cross":
                pv = np.concatenate([np.full((len(symbols), 1), np.nan), v[:, :-1]], axis=1)
                none = np.isnan(v) | np.isnan(pv) | np.isnan(prev_close) | np.isnan(close)
                if p["direction"] == "above":
                    passed = (prev_close <= pv) & (close > v)
                else:
                    passed = (prev_close >= pv) & (close < v)
            else:
                none = np.isnan(v)
                passed = v < p["value"] if p["op"] == "below" else v > p["value"]
            passed &= ~none
            any_none |= none
            any_pass |= passed
            any_fail |= ~none & ~passed
    record = (any_none & ~any_fail) | (~any_none & any_pass)
    listed = np.arange(n)[None, :] >= np.array([view.first_bar[s] for s in symbols])[:, None]
    out: np.ndarray = (listed & (np.isnan(close) | record)).T.copy()
    return out


def _cents(x: float) -> float:
    """Round half-up to the cent (deterministic, matches hand calculation)."""
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _f(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 6)


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
    decision_close: float = math.nan


@dataclass
class RunResult:
    decisions: pd.DataFrame
    fills: pd.DataFrame
    equity: pd.DataFrame
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketView:
    """Read-only per-session inputs for one account: prices and indicator values.

    Arrays are aligned to ``sessions``. Indicators are causal (each value uses data up
    to and including its own session), so a view built from history that ends at
    session ``t`` gives the same values at ``t`` as one built from a longer history.
    """

    sessions: pd.DatetimeIndex
    opens: dict[str, np.ndarray]
    closes: dict[str, np.ndarray]
    indicators: dict[str, dict[str, np.ndarray]]
    first_bar: dict[str, int]

    @classmethod
    def build(cls, resolved: dict[str, Any], data: MarketData) -> MarketView:
        symbols = sorted(resolved["universe"])
        conds = resolved["entry"] + resolved["exit"]["conditions"]
        return cls(
            sessions=data.sessions,
            opens={s: data.frames[s]["open"].to_numpy(dtype=float) for s in symbols},
            closes={s: data.frames[s]["close"].to_numpy(dtype=float) for s in symbols},
            indicators=_compute_indicators(data, conds, symbols),
            first_bar=dict(data.first_bar),
        )


class Account:
    """One variant's mutable state: cash, positions, pending orders and its ledgers.

    ``step(view, i)`` processes exchange session ``i`` (fills at the open, mark at the
    close, then decisions). Historical runs step through every session; paper mode
    steps one new session at a time. ``state()`` / ``from_state()`` round-trip
    everything needed to resume after a restart.
    """

    def __init__(self, resolved: dict[str, Any]) -> None:
        self.resolved = resolved
        port = resolved["portfolio"]
        self.cost_rate = float(port["cost_bps_per_side"]) / 10_000.0
        self.target = float(port["target_allocation"])
        self.max_pos = int(port["max_positions"])
        self.entry_conds = resolved["entry"]
        self.exit_rule = resolved["exit"]
        self.exit_conds = self.exit_rule["conditions"]
        self.hold_n = int(self.exit_rule["params"].get("sessions", 0))
        self.symbols = sorted(resolved["universe"])
        self.cash = float(port["starting_cash"])
        self.positions: dict[str, _Position] = {}
        self.pending: dict[str, _Order] = {}
        self.last_close: dict[str, float] = {}
        self.decisions: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.equity_rows: list[dict[str, Any]] = []
        self.last_index = -1
        # Derived, not state: rebuilt from ``resolved`` and the view after a restart.
        self._entry_compiled = _compile(self.entry_conds)
        self._exit_compiled = _compile(self.exit_conds)
        self._cand_view: MarketView | None = None
        self._cand: np.ndarray = np.zeros((0, 0), dtype=bool)

    def _candidates(self, view: MarketView, i: int) -> np.ndarray:
        """Indices into ``self.symbols`` the entry step must visit at session ``i``."""
        if view is not self._cand_view:
            self._cand = _entry_candidates(self._entry_compiled, view, self.symbols)
            self._cand_view = view
        return np.flatnonzero(self._cand[i])

    # Persistence ---------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "positions": {s: vars(p) for s, p in self.positions.items()},
            "pending": {s: vars(o) for s, o in self.pending.items()},
            "last_close": dict(self.last_close),
            "decisions": self.decisions,
            "fills": self.fills,
            "equity_rows": self.equity_rows,
            "last_index": self.last_index,
        }

    @classmethod
    def from_state(cls, resolved: dict[str, Any], state: dict[str, Any]) -> Account:
        a = cls(resolved)
        a.cash = float(state["cash"])
        a.positions = {s: _Position(**p) for s, p in state["positions"].items()}
        a.pending = {s: _Order(**o) for s, o in state["pending"].items()}
        a.last_close = {s: float(v) for s, v in state["last_close"].items()}
        a.decisions = list(state["decisions"])
        a.fills = list(state["fills"])
        a.equity_rows = list(state["equity_rows"])
        a.last_index = int(state["last_index"])
        return a

    # Processing ----------------------------------------------------------------
    def _record(self, day: str, sym: str, action: str, reason: str, **extra: Any) -> int:
        did = len(self.decisions)
        self.decisions.append(
            {
                "decision_id": did,
                "session": day,
                "symbol": sym,
                "action": action,
                "reason": reason,
                **extra,
            }
        )
        return did

    def step(self, view: MarketView, i: int) -> None:
        if i != self.last_index + 1:
            raise ValueError(
                f"sessions must be processed in order: expected {self.last_index + 1}, got {i}"
            )
        self._fill_at_open(view, i)
        equity = self._mark_at_close(view, i)
        exiting = self._decide_exits(view, i)
        self._decide_entries(view, i, equity, exiting)
        self.last_index = i

    def _fill_at_open(self, view: MarketView, i: int) -> None:
        day = view.sessions[i].date().isoformat()
        cost_rate = self.cost_rate
        for side in ("sell", "buy"):
            for sym in sorted(s for s, o in self.pending.items() if o.side == side):
                o = self.pending[sym]
                px = view.opens[sym][i]
                if math.isnan(px):
                    continue  # no bar: never invent a price, wait for the next session
                del self.pending[sym]
                if side == "sell":
                    pos = self.positions.pop(sym)
                    gross = pos.qty * px
                    cost = _cents(gross * cost_rate)
                    self.cash = _cents(self.cash + gross - cost)
                    self.fills.append(
                        _fill(day, sym, "sell", pos.qty, px, cost, self.cash, o, i - o.decision_idx)
                    )
                    continue
                qty = o.qty
                note = ""
                need = qty * px * (1 + cost_rate)
                if need > self.cash:
                    # The ordered quantity is no longer affordable at the open: skip the
                    # whole order and keep the reason (never buy a smaller quantity).
                    self._record(
                        day,
                        sym,
                        SKIP,
                        "insufficient_cash_at_open",
                        order_qty=o.qty,
                        open=px,
                        est_cost=round(need, 2),
                        available=round(self.cash, 2),
                        order_decision_id=o.decision_id,
                    )
                    continue
                gross = qty * px
                cost = _cents(gross * cost_rate)
                self.cash = _cents(self.cash - gross - cost)
                self.positions[sym] = _Position(qty, i, px)
                self.fills.append(
                    _fill(day, sym, "buy", qty, px, cost, self.cash, o, i - o.decision_idx, note)
                )

    def _mark_at_close(self, view: MarketView, i: int) -> float:
        """Mark to market (last known close values a symbol without a bar)."""
        for sym in self.symbols:
            c = view.closes[sym][i]
            if not math.isnan(c):
                self.last_close[sym] = float(c)
        pos_value = sum(p.qty * self.last_close[s] for s, p in self.positions.items())
        equity = _cents(self.cash + pos_value)
        self.equity_rows.append(
            {
                "session": view.sessions[i].date().isoformat(),
                "cash": self.cash,
                "positions_value": _cents(pos_value),
                "equity": equity,
                "positions": len(self.positions),
            }
        )
        return equity

    def _decide_exits(self, view: MarketView, i: int) -> int:
        day = view.sessions[i].date().isoformat()
        exiting = 0
        for sym in sorted(self.positions):
            if sym in self.pending:
                exiting += 1
                continue
            pos = self.positions[sym]
            # The fill session counts as holding session one.
            held = i - pos.fill_idx + 1
            if self.exit_rule["kind"] == "holding_period":
                if held >= self.hold_n:
                    did = self._record(
                        day, sym, EXIT, f"held {held} sessions >= {self.hold_n}", qty=pos.qty
                    )
                    self.pending[sym] = _Order(sym, "sell", pos.qty, i, did)
                    exiting += 1
            else:
                closes = view.closes[sym]
                if math.isnan(closes[i]):
                    continue
                ok, res, vals = _evaluate_compiled(
                    self._exit_compiled, view.indicators[sym], closes, i
                )
                if ok:
                    did = self._record(
                        day, sym, EXIT, "exit signal", qty=pos.qty, conditions=res, values=vals
                    )
                    self.pending[sym] = _Order(sym, "sell", pos.qty, i, did)
                    exiting += 1
        return exiting

    def _decide_entries(self, view: MarketView, i: int, equity: float, exiting: int) -> None:
        """Entry decisions in ascending symbol order (documented tie-break)."""
        day = view.sessions[i].date().isoformat()
        cost_rate = self.cost_rate
        committed = sum(
            o.qty * o.decision_close * (1 + cost_rate)
            for o in self.pending.values()
            if o.side == "buy"
        )
        expected_proceeds = sum(
            self.positions[s].qty * self.last_close[s] * (1 - cost_rate)
            for s, o in self.pending.items()
            if o.side == "sell"
        )
        symbols = self.symbols
        # Only symbols that can record something are visited (see _entry_candidates);
        # the rest are not yet listed or fail a condition outright.
        for k in self._candidates(view, i):
            sym = symbols[k]
            if i < view.first_bar[sym]:
                continue  # not yet listed in the snapshot
            closes = view.closes[sym]
            c = closes[i]
            if math.isnan(c):
                self._record(day, sym, INELIGIBLE, "missing_bar")
                continue
            ok, res, vals = _evaluate_compiled(
                self._entry_compiled, view.indicators[sym], closes, i
            )
            if ok is None:
                if all(r["passed"] is not False for r in res):
                    self._record(day, sym, INELIGIBLE, "warmup", conditions=res, values=vals)
                continue
            if not ok:
                # Keep near misses (some but not all conditions passed) so comparisons can
                # show exactly which condition blocked an entry.
                if any(r["passed"] for r in res):
                    failed = [r["condition"] for r in res if not r["passed"]]
                    self._record(
                        day,
                        sym,
                        NO_SIGNAL,
                        "failed: " + "; ".join(failed),
                        conditions=res,
                        values=vals,
                    )
                continue
            if sym in self.positions or sym in self.pending:
                self._record(day, sym, SKIP, "already_held", conditions=res, values=vals)
                continue
            open_slots = (
                self.max_pos
                - (len(self.positions) - exiting)
                - sum(1 for o in self.pending.values() if o.side == "buy")
            )
            if open_slots <= 0:
                self._record(day, sym, SKIP, "max_positions", conditions=res, values=vals)
                continue
            qty = int((self.target * equity) // c)
            if qty <= 0:
                self._record(day, sym, SKIP, "zero_quantity", conditions=res, values=vals)
                continue
            est = qty * c * (1 + cost_rate)
            available = self.cash + expected_proceeds - committed
            if est > available:
                self._record(
                    day,
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
            did = self._record(
                day,
                sym,
                ENTER,
                "all entry conditions passed",
                conditions=res,
                values=vals,
                qty=qty,
                est_cost=round(est, 2),
            )
            self.pending[sym] = _Order(sym, "buy", qty, i, did, float(c))
            committed += est

    # Output --------------------------------------------------------------------
    def result(self, data: MarketData | None = None) -> RunResult:
        dec = pd.DataFrame(self.decisions)
        if dec.empty:
            dec = pd.DataFrame(columns=["decision_id", "session", "symbol", "action", "reason"])
        fl = pd.DataFrame(self.fills)
        if fl.empty:
            fl = pd.DataFrame(
                columns=["session", "symbol", "side", "qty", "price", "cost", "cash_after"]
            )
        eq = pd.DataFrame(self.equity_rows)
        meta: dict[str, Any] = {"open_positions": {s: p.qty for s, p in self.positions.items()}}
        bench = self.resolved["benchmark"]
        if data is not None and bench in data.frames and len(eq):
            values, invested = _benchmark(
                data.frames[bench],
                data.sessions,
                len(eq),
                float(self.resolved["portfolio"]["starting_cash"]),
                self.cost_rate,
            )
            if invested is not None:
                eq["benchmark_equity"] = values
                meta["benchmark"] = {"symbol": bench, **invested}
        return RunResult(dec, fl, eq, meta)


def _benchmark(
    frame: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    n: int,
    start: float,
    cost_rate: float,
) -> tuple[np.ndarray, dict[str, Any] | None]:
    """Buy-and-hold the benchmark under the strategy's own conventions.

    Whole shares are bought at the open of the first session with a bar, paying the
    same cost per side; the rest stays as cash. It is valued at each close (last known
    close when a bar is missing). Prices come from the same snapshot, so splits and
    dividends are treated exactly as for the strategy's holdings.
    """
    opens = frame["open"].to_numpy(dtype=float)[:n]
    closes = frame["close"].ffill().to_numpy(dtype=float)[:n]
    ok = np.flatnonzero(~np.isnan(opens) & ~np.isnan(closes))
    if ok.size == 0:
        return np.full(n, np.nan), None
    j = int(ok[0])
    px = float(opens[j])
    shares = int(start // (px * (1 + cost_rate)))
    cost = _cents(shares * px * cost_rate)
    cash = _cents(start - shares * px - cost)
    values = np.array(
        [start if k < j else _cents(cash + shares * float(closes[k])) for k in range(n)]
    )
    return values, {
        "invested_session": sessions[j].date().isoformat(),
        "price": round(px, 6),
        "shares": shares,
        "cost": cost,
        "cash_left": cash,
    }


def simulate(resolved: dict[str, Any], data: MarketData) -> RunResult:
    """Historical run: one account stepped through every session of ``data``."""
    view = MarketView.build(resolved, data)
    account = Account(resolved)
    for i in range(len(view.sessions)):
        account.step(view, i)
    return account.result(data)


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
