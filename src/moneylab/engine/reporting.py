"""Performance metrics and run comparison.

Metrics are computed after a run finishes, from its ledgers. Nothing here feeds back
into decisions, so full-period statistics cannot leak into trading.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from moneylab.engine.simulator import DECISION_ACTIONS

SHARPE_ASSUMPTIONS = (
    "daily close-to-close equity returns, sample standard deviation, risk-free rate 0, "
    "annualised by sqrt(252); undefined when returns never vary"
)


def metrics(equity: pd.DataFrame, fills: pd.DataFrame, column: str = "equity") -> dict[str, Any]:
    e = equity[column].to_numpy(dtype=float)
    rets = np.diff(e) / e[:-1] if e.size > 1 else np.array([])
    peak = np.maximum.accumulate(e) if e.size else e
    dd = float(((e - peak) / peak).min()) if e.size else 0.0
    sd = float(rets.std(ddof=1)) if rets.size > 1 else 0.0
    # Undefined (None) when there are fewer than two daily returns or they never vary;
    # the dashboard shows it as "undefined" rather than a misleading 0.
    sharpe = float(rets.mean() / sd * math.sqrt(252)) if sd > 0 else None
    out: dict[str, Any] = {
        "total_return": float(e[-1] / e[0] - 1) if e.size else 0.0,
        "max_drawdown": dd,
        "sharpe": sharpe,
        "sharpe_assumptions": SHARPE_ASSUMPTIONS,
    }
    if column == "equity":
        # A trade is a completed round trip (a sell that closes a position); positions
        # still open at the end are counted separately.
        buys = int((fills["side"] == "buy").sum()) if len(fills) else 0
        sells = int((fills["side"] == "sell").sum()) if len(fills) else 0
        out["trades"] = sells
        out["open_positions"] = buys - sells
        out["costs"] = float(fills["cost"].sum()) if len(fills) else 0.0
        out["avg_exposure"] = float((equity["positions_value"] / equity["equity"]).mean())
        out["final_equity"] = float(e[-1]) if e.size else 0.0
    return out


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(d, list) and d and all(isinstance(x, dict) for x in d):
        out = {}
        for i, v in enumerate(d):
            out.update(_flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: d}


def comparable_settings(resolved: dict[str, Any]) -> dict[str, Any]:
    """A resolved config in the engine/0.3.0 shape, for comparing settings.

    Runs saved before 0.3.0 have one ``exit`` and no sizing or ranking keys; they get the
    equivalent ``exits`` list, the default sizing and no ranking. The ``exit`` key (a copy
    of the first exit rule) is dropped so a changed exit is reported once.
    """
    from moneylab.engine.config import resolved_exits, resolved_sizing

    out = {k: v for k, v in resolved.items() if k != "exit"}
    out["exits"] = resolved_exits(resolved)
    out["sizing"] = resolved_sizing(resolved)
    out["rank_by"] = resolved.get("rank_by")
    # Runs saved before entry fill options filled at the next open.
    out["entry_fill"] = resolved.get("entry_fill") or {
        "kind": "next_open",
        "below_pct": None,
        "above_pct": None,
        "dip_pct": None,
    }
    return out


def config_diff(
    a: dict[str, Any], b: dict[str, Any], ignore: tuple[str, ...] = ("name",)
) -> list[dict[str, Any]]:
    fa, fb = _flatten(comparable_settings(a)), _flatten(comparable_settings(b))
    rows = []
    for key in sorted(set(fa) | set(fb)):
        if key.split(".")[0] in ignore:
            continue
        if fa.get(key) != fb.get(key):
            rows.append({"setting": key, "a": fa.get(key), "b": fb.get(key)})
    return rows


def first_difference(
    dec_a: pd.DataFrame,
    dec_b: pd.DataFrame,
    fills_a: pd.DataFrame,
    fills_b: pd.DataFrame,
    eq_a: pd.DataFrame,
    eq_b: pd.DataFrame,
    universe_a: list[str] | None = None,
    universe_b: list[str] | None = None,
) -> dict[str, Any] | None:
    """The earliest trading decision (enter, exit or skip) that differs, or None.

    Decisions are matched on (session, symbol) and ordered by session then symbol, the
    same order the engine processes them. When one run has no trading decision for that
    key, its diagnostic row (for example a near miss showing the observed RSI against its
    threshold) is shown instead. When the universes are given and the symbol is not
    in one run's universe, that run's side says so.
    """

    def keyed(d: pd.DataFrame, trading: bool) -> _Keyed:
        mask = d["action"].isin(DECISION_ACTIONS).to_numpy()
        return _Keyed(d, mask if trading else ~mask)

    ka, kb = keyed(dec_a, True), keyed(dec_b, True)
    for key in sorted(set(ka.pos) | set(kb.pos)):
        sa, sb = ka.sig(key), kb.sig(key)
        if sa == sb:
            continue
        session, symbol = key
        ra, rb = ka.row(key), kb.row(key)
        if ra is None:
            ra = keyed(dec_a, False).row(key)
        if rb is None:
            rb = keyed(dec_b, False).row(key)
        return {
            "session": session,
            "symbol": symbol,
            "a": _row_view(ra, fills_a, symbol, universe_a),
            "b": _row_view(rb, fills_b, symbol, universe_b),
            "impact": _impact(session, eq_a, eq_b),
        }
    return None


class _Keyed:
    """Rows of a decision ledger keyed by (session, symbol); the last row wins.

    Row Series are built only on demand, exactly as ``DataFrame.iterrows`` builds
    them, so large ledgers (hundreds of thousands of diagnostic rows) compare fast.
    """

    def __init__(self, d: pd.DataFrame, mask: np.ndarray) -> None:
        self.d = d
        self.values = d.values
        idx = np.flatnonzero(mask)
        sessions = d["session"].to_numpy(dtype=object)[idx]
        symbols = d["symbol"].to_numpy(dtype=object)[idx]
        self.pos: dict[tuple[str, str], int] = dict(
            zip(zip(sessions, symbols, strict=True), idx.tolist(), strict=True)
        )
        cols = list(d.columns)
        self._sig_cols = [cols.index(c) if c in cols else None for c in ("action", "reason", "qty")]

    def row(self, key: tuple[str, str]) -> pd.Series | None:
        p = self.pos.get(key)
        if p is None:
            return None
        return pd.Series(self.values[p], index=self.d.columns, name=self.d.index[p])

    def sig(self, key: tuple[str, str]) -> str:
        p = self.pos.get(key)
        if p is None:
            return "none"
        a, r, q = (None if c is None else self.values[p][c] for c in self._sig_cols)
        return f"{a}|{r}|{q}"


def _row_view(
    row: pd.Series | None,
    fills: pd.DataFrame,
    symbol: str = "",
    universe: list[str] | None = None,
) -> dict[str, Any]:
    if row is None:
        if universe is not None and symbol not in universe:
            return {"action": "none", "reason": f"{symbol} is not in this run's universe"}
        return {"action": "none", "reason": "no entry condition passed; nothing recorded"}
    view: dict[str, Any] = {str(k): _plain(v) for k, v in row.items() if not _is_missing(v)}
    if len(fills) and "decision_id" in fills:
        f = fills[fills["decision_id"] == row["decision_id"]]
        if len(f):
            view["fill"] = {str(k): _plain(v) for k, v in f.iloc[0].items()}
    return view


def _impact(session: str, eq_a: pd.DataFrame, eq_b: pd.DataFrame) -> dict[str, Any]:
    a = eq_a.set_index("session")
    b = eq_b.set_index("session")
    later = [s for s in a.index if s > session]
    nxt = later[0] if later else session
    return {
        "next_session": nxt,
        "cash_a": float(a["cash"].loc[nxt]),
        "cash_b": float(b["cash"].loc[nxt]),
        "final_equity_a": float(a["equity"].iloc[-1]),
        "final_equity_b": float(b["equity"].iloc[-1]),
        "final_equity_delta": round(float(b["equity"].iloc[-1] - a["equity"].iloc[-1]), 2),
    }


def _is_missing(v: Any) -> bool:
    if isinstance(v, list | dict):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _plain(v: Any) -> Any:
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v
