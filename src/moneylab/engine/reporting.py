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
    "daily close-to-close equity returns, risk-free rate 0, annualised by sqrt(252)"
)


def metrics(equity: pd.DataFrame, fills: pd.DataFrame, column: str = "equity") -> dict[str, Any]:
    e = equity[column].to_numpy(dtype=float)
    rets = np.diff(e) / e[:-1] if e.size > 1 else np.array([])
    peak = np.maximum.accumulate(e) if e.size else e
    dd = float(((e - peak) / peak).min()) if e.size else 0.0
    sd = float(rets.std(ddof=1)) if rets.size > 1 else 0.0
    sharpe = float(rets.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0
    out: dict[str, Any] = {
        "total_return": float(e[-1] / e[0] - 1) if e.size else 0.0,
        "max_drawdown": dd,
        "sharpe": sharpe,
        "sharpe_assumptions": SHARPE_ASSUMPTIONS,
    }
    if column == "equity":
        out["trades"] = int((fills["side"] == "buy").sum()) if len(fills) else 0
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


def config_diff(
    a: dict[str, Any], b: dict[str, Any], ignore: tuple[str, ...] = ("name",)
) -> list[dict[str, Any]]:
    fa, fb = _flatten(a), _flatten(b)
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
) -> dict[str, Any] | None:
    """The earliest trading decision (enter, exit or skip) that differs, or None.

    Decisions are matched on (session, symbol) and ordered by session then symbol, the
    same order the engine processes them. When one run has no trading decision for that
    key, its diagnostic row (for example a near miss showing the observed RSI against its
    threshold) is shown instead.
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
        return {
            "session": session,
            "symbol": symbol,
            "a": _row_view(ra if ra is not None else keyed(dec_a, False).row(key), fills_a),
            "b": _row_view(rb if rb is not None else keyed(dec_b, False).row(key), fills_b),
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


def _row_view(row: pd.Series | None, fills: pd.DataFrame) -> dict[str, Any]:
    if row is None:
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
