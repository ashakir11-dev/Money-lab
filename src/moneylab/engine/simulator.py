"""Deterministic daily simulator shared by historical and (future) paper modes.

Timeline for each exchange session ``t``:

1. Pending orders fill on ``t`` (exits first, then entries in ascending symbol order).
   Default entries fill at the open. Range and dip entries use that session's high/low.
   An order for a symbol without a bar on ``t`` waits.
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

from moneylab.components.registry import (
    ExitContext,
    SizingContext,
    get_component,
    indicator_key,
)
from moneylab.data.bars import session_label
from moneylab.data.snapshot import MarketData
from moneylab.engine.config import required_indicators, resolved_exits, resolved_sizing

# Decision actions
ENTER = "enter"
EXIT = "exit"
SKIP = "skip"
NO_SIGNAL = "no_signal"  # diagnostic: some entry conditions passed, not all
INELIGIBLE = "ineligible"  # diagnostic: warm-up or missing bar
# Actions that are trading decisions (compared between runs). The others explain them.
DECISION_ACTIONS = (ENTER, EXIT, SKIP)
# Note on fills that park or release idle cash in the benchmark (not strategy trades).
SWEEP_NOTE = "sweep"


def _ind_key(ind: dict[str, Any]) -> str:
    return indicator_key(ind)


def _cond_label(c: dict[str, Any]) -> str:
    p = c["params"]
    if c["kind"] == "price_cross":
        return f"close crosses {p['direction']} {_ind_key(c['indicator'])}"
    if c["kind"] == "indicator_compare":
        return f"{_ind_key(c['indicator'])} {p['op']} {_ind_key(c['other'])}"
    if c["kind"] == "indicator_cross":
        return f"{_ind_key(c['indicator'])} crosses {p['direction']} {_ind_key(c['other'])}"
    return f"{_ind_key(c['indicator'])} {p['op']} {p['value']:g}"


def _compute_indicators(
    data: MarketData, indicators: list[dict[str, Any]], universe: list[str] | None = None
) -> dict[str, dict[str, np.ndarray]]:
    """symbol -> indicator key -> values aligned to sessions. Computed once per run.

    Cross-sectional indicators (breadth) are computed once over the universe symbols
    and the same array is shared by every symbol.
    """
    needed = {_ind_key(ind): ind for ind in indicators}
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
    other: str | None = None  # second indicator's key for indicator_compare / _cross


def _compile(conds: list[dict[str, Any]]) -> list[_Cond]:
    return [
        _Cond(
            c["kind"],
            _ind_key(c["indicator"]),
            _cond_label(c),
            c["params"],
            _ind_key(c["other"]) if c.get("other") else None,
        )
        for c in conds
    ]


def _evaluate(
    conds: list[dict[str, Any]],
    ind: dict[str, np.ndarray],
    close: np.ndarray,
    i: int,
) -> tuple[bool | None, list[dict[str, Any]], dict[str, float | None]]:
    """Evaluate entry conditions at session i. Passes only when every condition passes."""
    return _evaluate_compiled(_compile(conds), ind, close, i)


def _evaluate_compiled(
    conds: list[_Cond],
    ind: dict[str, np.ndarray],
    close: np.ndarray,
    i: int,
    *,
    combine: str = "all",
) -> tuple[bool | None, list[dict[str, Any]], dict[str, float | None]]:
    """Evaluate conditions at session i.

    ``combine="all"`` (entries) passes only when every condition passes.
    ``combine="any"`` (exits) passes when one condition passes, so a stop and a
    target never have to happen together. Returns None when nothing passed and
    every condition is still warming up.
    """
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
        elif c.other is not None:
            other = ind[c.other]
            w = other[i]
            values[c.other] = _f(w)
            r["other"] = c.other
            r["other_observed"] = _f(w)
            if c.kind == "indicator_cross":
                pv = arr[i - 1] if i > 0 else np.nan
                pw = other[i - 1] if i > 0 else np.nan
                values[f"prev {key}"] = _f(pv)
                values[f"prev {c.other}"] = _f(pw)
                if math.isnan(v) or math.isnan(w) or math.isnan(pv) or math.isnan(pw):
                    r["passed"] = None
                elif p["direction"] == "above":
                    r["passed"] = bool(pv <= pw and v > w)
                else:
                    r["passed"] = bool(pv >= pw and v < w)
            elif math.isnan(v) or math.isnan(w):
                r["passed"] = None
            elif p["op"] == "above":
                r["passed"] = bool(v > w)
            else:
                r["passed"] = bool(v < w)
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
    flags = [r["passed"] for r in results]
    if combine == "any":
        if any(flag is True for flag in flags):
            return True, results, values
        if flags and all(flag is None for flag in flags):
            return None, results, values
        return False, results, values
    if not evaluable:
        return None, results, values
    return all(flags), results, values


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
            elif c.other is not None:
                w = np.vstack([view.indicators[s][c.other] for s in symbols])
                none = np.isnan(v) | np.isnan(w)
                if c.kind == "indicator_cross":
                    pad = np.full((len(symbols), 1), np.nan)
                    pv = np.concatenate([pad, v[:, :-1]], axis=1)
                    pw = np.concatenate([pad, w[:, :-1]], axis=1)
                    none = none | np.isnan(pv) | np.isnan(pw)
                    if p["direction"] == "above":
                        passed = (pv <= pw) & (v > w)
                    else:
                        passed = (pv >= pw) & (v < w)
                else:
                    passed = v > w if p["op"] == "above" else v < w
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
    # Highest close from the fill session on (NaN until the first close is seen).
    peak_close: float = math.nan


@dataclass
class _Order:
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    decision_idx: int
    decision_id: int
    decision_close: float = math.nan
    fill_kind: str = "next_open"
    limit_px: float = math.nan
    range_lo: float = math.nan
    range_hi: float = math.nan


@dataclass(frozen=True)
class _ExitRule:
    """One resolved exit rule, ready to check a position at a session's close."""

    kind: str
    params: dict[str, Any]
    compiled: list[_Cond]
    keys: list[str]

    @classmethod
    def build(cls, ex: dict[str, Any]) -> _ExitRule:
        return cls(
            ex["kind"],
            ex["params"],
            _compile(ex["conditions"]),
            [_ind_key(r) for r in ex.get("requires", [])],
        )

    def check(
        self, view: MarketView, sym: str, i: int, pos: _Position, held: int, close: float
    ) -> tuple[str, dict[str, Any]] | None:
        if self.kind == "signal":
            cap = int(self.params.get("max_sessions") or 0)
            if cap and held >= cap:
                return f"held {held} sessions >= {cap}", {}
            if math.isnan(close):
                return None
            ok, res, vals = _evaluate_compiled(
                self.compiled, view.indicators[sym], view.closes[sym], i, combine="any"
            )
            return ("exit signal", {"conditions": res, "values": vals}) if ok else None
        fn = get_component(self.kind).exit_fn
        assert fn is not None
        ctx = ExitContext(
            symbol=sym,
            qty=pos.qty,
            entry_price=pos.entry_price,
            held=held,
            close=close,
            peak_close=pos.peak_close,
            values={k: float(view.indicators[sym][k][i]) for k in self.keys},
        )
        return fn(ctx, self.params)


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
    highs: dict[str, np.ndarray]
    lows: dict[str, np.ndarray]
    closes: dict[str, np.ndarray]
    indicators: dict[str, dict[str, np.ndarray]]
    first_bar: dict[str, int]
    # The benchmark's own bars, used only to park idle cash in it (idle_cash "benchmark").
    bench_open: np.ndarray | None = None
    bench_close: np.ndarray | None = None

    @classmethod
    def build(cls, resolved: dict[str, Any], data: MarketData) -> MarketView:
        symbols = sorted(resolved["universe"])
        bench = data.frames.get(resolved.get("benchmark", ""))
        return cls(
            sessions=data.sessions,
            opens={s: data.frames[s]["open"].to_numpy(dtype=float) for s in symbols},
            highs={s: data.frames[s]["high"].to_numpy(dtype=float) for s in symbols},
            lows={s: data.frames[s]["low"].to_numpy(dtype=float) for s in symbols},
            closes={s: data.frames[s]["close"].to_numpy(dtype=float) for s in symbols},
            indicators=_compute_indicators(data, required_indicators(resolved), symbols),
            first_bar=dict(data.first_bar),
            bench_open=bench["open"].to_numpy(dtype=float) if bench is not None else None,
            bench_close=bench["close"].to_numpy(dtype=float) if bench is not None else None,
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
        self.exits = resolved_exits(resolved)
        self.sizing = resolved_sizing(resolved)
        self.rank = resolved.get("rank_by")
        self.entry_fill = resolved.get("entry_fill") or {"kind": "next_open"}
        self.symbols = sorted(resolved["universe"])
        # Idle cash held in the benchmark (engine/0.4.0): whole shares kept apart from
        # ``positions``. They never count toward max_positions and are not trades.
        self.idle_in_benchmark = port.get("idle_cash", "cash") == "benchmark"
        self.bench = str(resolved["benchmark"])
        self._weights = _weight_index(resolved.get("point_in_time_weights"))
        self.sweep_qty = 0
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
        self._exit_rules = [_ExitRule.build(e) for e in self.exits]
        self._size_fn = get_component(self.sizing["id"]).size_fn
        self._size_keys = [_ind_key(r) for r in self.sizing.get("requires", [])]
        self._rank_key = _ind_key(self.rank["indicator"]) if self.rank else None
        self._cand_view: MarketView | None = None
        self._cand: np.ndarray = np.zeros((0, 0), dtype=bool)

    def _candidates(self, view: MarketView, i: int) -> np.ndarray:
        """Indices into ``self.symbols`` the entry step must visit at session ``i``, in
        processing order: ascending symbol, or by the rank indicator (missing values
        last, ties in symbol order)."""
        if view is not self._cand_view:
            self._cand = _entry_candidates(self._entry_compiled, view, self.symbols)
            self._cand_view = view
        ks = np.flatnonzero(self._cand[i])
        if self._rank_key is None or ks.size < 2:
            return ks
        sign = 1.0 if self.rank and self.rank["order"] == "ascending" else -1.0
        key = self._rank_key

        def order(k: int) -> tuple[bool, float, int]:
            v = view.indicators[self.symbols[k]][key][i]
            missing = bool(math.isnan(v))
            return (missing, 0.0 if missing else sign * float(v), int(k))

        return np.array(sorted(ks.tolist(), key=order), dtype=int)

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
            "sweep_qty": self.sweep_qty,
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
        a.sweep_qty = int(state.get("sweep_qty", 0))
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

    def _entry_fill_price(
        self, view: MarketView, i: int, o: _Order, open_px: float
    ) -> tuple[float | None, str, bool]:
        """Buy fill. ``(price, note, wait)``. Wait keeps the order; ``None`` cancels."""
        kind = o.fill_kind or "next_open"
        if kind == "next_open":
            return open_px, "", False
        low = view.lows[o.symbol][i]
        high = view.highs[o.symbol][i]
        if math.isnan(low) or math.isnan(high):
            return None, "", True
        if kind == "dip":
            limit = o.limit_px
            if math.isnan(limit):
                return open_px, "", False
            if open_px <= limit:
                return open_px, "gapped through dip", False
            if low <= limit:
                return limit, "filled at dip", False
            return None, "", True
        if kind == "next_range":
            lo, hi = o.range_lo, o.range_hi
            if math.isnan(lo) or math.isnan(hi):
                return open_px, "", False
            if lo <= open_px <= hi:
                return open_px, "", False
            if high < lo or low > hi:
                return None, "outside_range", False
            if open_px > hi:
                return hi, "filled at range high", False
            return lo, "filled at range low", False
        return open_px, "", False

    def _fill_at_open(self, view: MarketView, i: int) -> None:
        day = session_label(view.sessions[i])
        cost_rate = self.cost_rate
        for side in ("sell", "buy"):
            if side == "buy":
                self._sweep_sell(view, i, day)
            for sym in sorted(s for s, o in self.pending.items() if o.side == side):
                o = self.pending[sym]
                open_px = view.opens[sym][i]
                if math.isnan(open_px):
                    continue  # no bar: never invent a price, wait for the next session
                if side == "sell":
                    del self.pending[sym]
                    pos = self.positions.pop(sym)
                    gross = pos.qty * open_px
                    cost = _cents(gross * cost_rate)
                    self.cash = _cents(self.cash + gross - cost)
                    self.fills.append(
                        _fill(
                            day,
                            sym,
                            "sell",
                            pos.qty,
                            open_px,
                            cost,
                            self.cash,
                            o,
                            i - o.decision_idx,
                        )
                    )
                    continue
                px, note, wait = self._entry_fill_price(view, i, o, open_px)
                if wait:
                    continue
                del self.pending[sym]
                if px is None:
                    self._record(
                        day,
                        sym,
                        SKIP,
                        note or "entry not filled",
                        order_qty=o.qty,
                        open=open_px,
                        order_decision_id=o.decision_id,
                    )
                    continue
                qty = o.qty
                need = qty * px * (1 + cost_rate)
                if need > self.cash:
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
        self._sweep_buy(view, i, day)

    # Idle cash in the benchmark ----------------------------------------------------
    def _bench_open(self, view: MarketView, i: int) -> float:
        """The benchmark's open at session ``i``; NaN when idle cash stays as cash or the
        benchmark has no bar (then nothing is swept that session)."""
        if not self.idle_in_benchmark or view.bench_open is None:
            return math.nan
        return float(view.bench_open[i])

    def _sweep_fill(self, day: str, side: str, qty: int, px: float, cost: float) -> dict[str, Any]:
        return {
            "session": day,
            "symbol": self.bench,
            "side": side,
            "qty": qty,
            "price": round(px, 6),
            "gross": _cents(qty * px),
            "cost": cost,
            "cash_after": self.cash,
            "decision_id": None,
            "sessions_waited": 0,
            "note": SWEEP_NOTE,
        }

    def _sweep_sell(self, view: MarketView, i: int, day: str) -> None:
        """Sell just enough benchmark shares to pay for the buys that can fill today.

        The estimate is each order's quantity times its highest possible fill price (the
        open, or the range's high when that is above it), times one plus the cost, rounded up
        to the cent, plus a cent for each order after the first (their own rounding).
        """
        px = self._bench_open(view, i)
        if math.isnan(px) or self.sweep_qty <= 0:
            return
        need = 0.0
        orders = 0
        for sym, o in self.pending.items():
            if o.side != "buy":
                continue
            open_px = view.opens[sym][i]
            if math.isnan(open_px):
                continue
            top = open_px if math.isnan(o.range_hi) else max(open_px, o.range_hi)
            need += o.qty * top * (1 + self.cost_rate)
            orders += 1
        if not orders:
            return
        # Round up to the cent, and allow a cent for each further order's own rounding.
        need = math.ceil(need * 100 - 1e-9) / 100 + 0.01 * (orders - 1)
        short = need - self.cash
        if short <= 0:
            return
        k = min(self.sweep_qty, math.ceil(short / (px * (1 - self.cost_rate))))
        while True:
            gross = k * px
            cost = _cents(gross * self.cost_rate)
            cash = _cents(self.cash + gross - cost)
            if cash >= need or k >= self.sweep_qty:
                break
            k += 1
        self.cash = cash
        self.sweep_qty -= k
        self.fills.append(self._sweep_fill(day, "sell", k, px, cost))

    def _sweep_buy(self, view: MarketView, i: int, day: str) -> None:
        """Put all the cash that is left into whole benchmark shares at the open."""
        px = self._bench_open(view, i)
        if math.isnan(px) or self.cash <= 0:
            return
        k = int(self.cash // (px * (1 + self.cost_rate)))
        while k > 0 and k * px + _cents(k * px * self.cost_rate) > self.cash:
            k -= 1
        if k <= 0:
            return
        cost = _cents(k * px * self.cost_rate)
        self.cash = _cents(self.cash - k * px - cost)
        self.sweep_qty += k
        self.fills.append(self._sweep_fill(day, "buy", k, px, cost))

    def _mark_at_close(self, view: MarketView, i: int) -> float:
        """Mark to market (last known close values a symbol without a bar)."""
        for sym in self.symbols:
            c = view.closes[sym][i]
            if not math.isnan(c):
                self.last_close[sym] = float(c)
        pos_value = sum(p.qty * self.last_close[s] for s, p in self.positions.items())
        sweep_value = 0.0
        if self.idle_in_benchmark:
            if view.bench_close is not None and not math.isnan(view.bench_close[i]):
                self.last_close[self.bench] = float(view.bench_close[i])
            sweep_value = _cents(self.sweep_qty * self.last_close.get(self.bench, 0.0))
        equity = _cents(self.cash + pos_value + sweep_value)
        row = {
            "session": session_label(view.sessions[i]),
            "cash": self.cash,
            "positions_value": _cents(pos_value),
            "equity": equity,
            "positions": len(self.positions),
        }
        if self.idle_in_benchmark:
            row["sweep_value"] = sweep_value
        self.equity_rows.append(row)
        return equity

    def _decide_exits(self, view: MarketView, i: int) -> int:
        day = session_label(view.sessions[i])
        exiting = 0
        for sym in sorted(self.positions):
            if sym in self.pending:
                exiting += 1
                continue
            pos = self.positions[sym]
            close = float(view.closes[sym][i])
            if not math.isnan(close) and not close <= pos.peak_close:
                pos.peak_close = close
            # The fill session counts as holding session one.
            held = i - pos.fill_idx + 1
            for rule in self._exit_rules:
                fired = rule.check(view, sym, i, pos, held, close)
                if fired is None:
                    continue
                reason, extra = fired
                did = self._record(day, sym, EXIT, reason, qty=pos.qty, **extra)
                self.pending[sym] = _Order(sym, "sell", pos.qty, i, did)
                exiting += 1
                break
        return exiting

    def _decide_entries(self, view: MarketView, i: int, equity: float, exiting: int) -> None:
        """Entry decisions in ascending symbol order (documented tie-break)."""
        day = session_label(view.sessions[i])
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
        sweep_proceeds = 0.0
        if self.idle_in_benchmark and self.sweep_qty:
            sweep_proceeds = self.sweep_qty * self.last_close.get(self.bench, 0.0) * (1 - cost_rate)
        symbols = self.symbols
        # Only symbols that can record something are visited (see _entry_candidates);
        # the rest are not yet listed or fail a condition outright.
        for k in self._candidates(view, i):
            sym = symbols[k]
            if i < view.first_bar[sym]:
                continue  # not yet listed in the snapshot
            if self._weights is not None and not _in_index(self._weights, sym, view.sessions[i]):
                self._record(day, sym, INELIGIBLE, "not_in_index")
                continue
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
            assert self._size_fn is not None
            size_inputs = {k: float(view.indicators[sym][k][i]) for k in self._size_keys}
            if size_inputs:
                # Record what the sizing rule saw, so its quantity can be checked.
                vals = {**vals, **{k: _f(v) for k, v in size_inputs.items()}}
            qty = int(
                self._size_fn(
                    SizingContext(
                        symbol=sym,
                        close=float(c),
                        equity=equity,
                        target_allocation=self.target,
                        values=size_inputs,
                    ),
                    self.sizing["params"],
                )
            )
            if qty <= 0:
                self._record(day, sym, SKIP, "zero_quantity", conditions=res, values=vals)
                continue
            est = qty * c * (1 + cost_rate)
            available = self.cash + expected_proceeds - committed + sweep_proceeds
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
            fill = self.entry_fill
            kind = str(fill.get("kind") or "next_open")
            close_px = float(c)
            limit = lo = hi = math.nan
            if kind == "dip":
                limit = close_px * (1.0 - float(fill.get("dip_pct") or 0.0))
            elif kind == "next_range":
                lo = close_px * (1.0 - float(fill.get("below_pct") or 0.0))
                hi = close_px * (1.0 + float(fill.get("above_pct") or 0.0))
            self.pending[sym] = _Order(
                sym,
                "buy",
                qty,
                i,
                did,
                close_px,
                fill_kind=kind,
                limit_px=limit,
                range_lo=lo,
                range_hi=hi,
            )
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
        "invested_session": session_label(sessions[j]),
        "price": round(px, 6),
        "shares": shares,
        "cost": cost,
        "cash_left": cash,
    }


def _weight_index(
    records: object,
) -> dict[str, list[tuple[pd.Timestamp, float]]] | None:
    if records is None:
        return None
    if not isinstance(records, list):
        raise ValueError("point_in_time_weights must be a list of as-of rows")
    grouped: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("point_in_time_weights row is not a record")
        symbol = str(row["symbol"]).upper()
        grouped.setdefault(symbol, []).append((pd.Timestamp(row["as_of"]), float(row["weight"])))
    for symbol in grouped:
        grouped[symbol].sort()
    return grouped


def _in_index(
    weights: dict[str, list[tuple[pd.Timestamp, float]]], symbol: str, session: pd.Timestamp
) -> bool:
    """Latest weight at or before this session. Later rows are not used."""
    latest: float | None = None
    day = pd.Timestamp(session)
    for as_of, weight in weights.get(symbol, []):
        if as_of <= day:
            latest = weight
        else:
            break
    return latest is not None and latest > 0


def simulate(resolved: dict[str, Any], data: MarketData) -> RunResult:
    """Historical run: one account stepped through every session of ``data``.

    A ``walk_forward`` block fits one knob on each prior window and trades the next
    window from a flat account. Without that block this is a single pass.
    """
    if resolved.get("walk_forward"):
        from moneylab.engine.walkforward import simulate_walk_forward

        return simulate_walk_forward(resolved, data)
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
