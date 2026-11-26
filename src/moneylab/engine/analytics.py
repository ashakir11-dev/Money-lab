"""Research analytics for a finished run: trade quality, edge versus the benchmark, risk,
drawdowns and breakdowns by year, symbol, exit rule and market regime.

Everything here is computed after a run from its ledgers (and, where prices during a
trade are needed, from the same snapshot bars the run used). Nothing feeds back into
decisions, so full-period statistics cannot leak into trading, and the analysis can be
recomputed for any saved run without simulating it again.

Conventions (kept deliberately simple so the numbers can be checked by hand):

* A trade is a closed round trip, matched first-in first-out per symbol. Fills marked
  ``sweep`` (idle cash parked in the benchmark) are not trades.
* A trade's return is its P&L after both sides' costs divided by what the entry cost
  (shares x price + buy cost).
* The benchmark's return over a trade is open-to-open, from the entry fill session to the
  exit fill session, the same prices a trade fills at. Edge = trade return - that return.
* Holding length counts sessions from the entry fill to the exit fill (an entry on
  Monday's open sold on Wednesday's open held 2 sessions).
* MAE/MFE are the lowest low and highest high from the entry session up to the session
  before the exit fill, plus the exit price, relative to the entry price.
* Annualised figures use bars per year for the run's timeframe (252 daily sessions).
* The regime is "up" when the benchmark's prior close is above its 200-day average
  (200 sessions x bars per session), otherwise "down"; "warmup" until the average exists.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from moneylab.data.bars import bars_per_session, bars_per_year
from moneylab.engine.simulator import SWEEP_NOTE

LOW_SAMPLE_TRADES = 30
REGIME_DAYS = 200

TRADE_COLUMNS = [
    "symbol",
    "entry_session",
    "exit_session",
    "entry_decision_id",
    "exit_decision_id",
    "qty",
    "entry_price",
    "exit_price",
    "costs",
    "pnl",
    "ret",
    "hold_sessions",
    "exit_kind",
    "exit_reason",
    "bench_ret",
    "edge",
    "mae",
    "mfe",
    "regime",
]


# Helpers -------------------------------------------------------------------------------
def _f(x: Any) -> float | None:
    """A finite float rounded for storage, or None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, 6) if math.isfinite(v) else None


def _int_or_none(v: Any) -> int | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if math.isfinite(f) else None


def _t_stat(x: np.ndarray) -> float | None:
    if x.size < 2:
        return None
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return None
    return float(x.mean() / (sd / math.sqrt(x.size)))


def exit_kind(reason: str | None) -> str:
    """A short category for an exit decision's reason text."""
    r = str(reason or "")
    for k in ("stop_loss", "take_profit", "trailing_stop"):
        if r.startswith(k):
            return k
    if r.startswith("held "):
        return "max_hold"
    if r == "exit signal":
        return "signal"
    return r.split(":")[0] or "other"


def strategy_fills(fills: pd.DataFrame) -> pd.DataFrame:
    """Fills that belong to the strategy (sweep fills into the benchmark removed)."""
    if not len(fills) or "note" not in fills:
        return fills
    return fills[fills["note"].fillna("") != SWEEP_NOTE]


# Round trips ---------------------------------------------------------------------------
def round_trips(
    fills: pd.DataFrame,
    equity: pd.DataFrame,
    decisions: pd.DataFrame | None = None,
    frames: dict[str, pd.DataFrame] | None = None,
    benchmark: str | None = None,
    timeframe: str = "1d",
) -> pd.DataFrame:
    """Closed round trips, first-in first-out per symbol (see module notes)."""
    fl = strategy_fills(fills)
    if not len(fl):
        return pd.DataFrame(columns=TRADE_COLUMNS)
    sessions = [str(s) for s in equity["session"]] if len(equity) else []
    pos = {s: i for i, s in enumerate(sessions)}
    reasons: dict[int, str] = {}
    if decisions is not None and len(decisions) and "decision_id" in decisions:
        for did, reason in zip(decisions["decision_id"], decisions["reason"], strict=False):
            d = _int_or_none(did)
            if d is not None:
                reasons[d] = str(reason)
    bench_open = _arr(frames, benchmark, "open", len(sessions))
    regimes = _regimes(frames, benchmark, len(sessions), timeframe)

    books: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for f in fl.to_dict("records"):
        sym = str(f["symbol"])
        book = books.setdefault(sym, [])
        qty = float(f["qty"])
        cost = float(f.get("cost") or 0.0)
        if f["side"] == "buy":
            book.append(
                {
                    "session": str(f["session"]),
                    "qty": qty,
                    "price": float(f["price"]),
                    "cost": cost,
                    "decision_id": _int_or_none(f.get("decision_id")),
                }
            )
            continue
        left = qty
        sell_cost_per = cost / qty if qty else 0.0
        while left > 1e-9 and book:
            lot = book[0]
            take = min(lot["qty"], left)
            buy_cost = lot["cost"] / lot["qty"] * take if lot["qty"] else 0.0
            sell_cost = sell_cost_per * take
            basis = take * lot["price"] + buy_cost
            pnl = take * (float(f["price"]) - lot["price"]) - buy_cost - sell_cost
            ei, xi = pos.get(lot["session"]), pos.get(str(f["session"]))
            exit_did = _int_or_none(f.get("decision_id"))
            reason = reasons.get(exit_did) if exit_did is not None else None
            ret = pnl / basis if basis else math.nan
            b_ret = math.nan
            if ei is not None and xi is not None and bench_open is not None:
                b0, b1 = bench_open[ei], bench_open[xi]
                if b0 and not math.isnan(b0) and not math.isnan(b1):
                    b_ret = b1 / b0 - 1
            mae = mfe = math.nan
            if frames is not None and sym in frames and ei is not None and xi is not None:
                fr = frames[sym]
                lows = fr["low"].to_numpy(dtype=float)[ei:xi]
                highs = fr["high"].to_numpy(dtype=float)[ei:xi]
                px = float(f["price"])
                lo = np.nanmin(np.append(lows, px))
                hi = np.nanmax(np.append(highs, px))
                mae, mfe = lo / lot["price"] - 1, hi / lot["price"] - 1
            rows.append(
                {
                    "symbol": sym,
                    "entry_session": lot["session"],
                    "exit_session": str(f["session"]),
                    "entry_decision_id": lot["decision_id"],
                    "exit_decision_id": exit_did,
                    "qty": take,
                    "entry_price": lot["price"],
                    "exit_price": float(f["price"]),
                    "costs": buy_cost + sell_cost,
                    "pnl": pnl,
                    "ret": ret,
                    "hold_sessions": (xi - ei) if ei is not None and xi is not None else None,
                    "exit_kind": exit_kind(reason),
                    "exit_reason": reason,
                    "bench_ret": b_ret,
                    "edge": ret - b_ret if not math.isnan(b_ret) else math.nan,
                    "mae": mae,
                    "mfe": mfe,
                    "regime": regimes[ei] if regimes is not None and ei is not None else None,
                }
            )
            lot["qty"] -= take
            lot["cost"] -= buy_cost
            left -= take
            if lot["qty"] <= 1e-9:
                book.pop(0)
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def _arr(
    frames: dict[str, pd.DataFrame] | None, sym: str | None, col: str, n: int
) -> np.ndarray | None:
    if frames is None or sym is None or sym not in frames:
        return None
    return frames[sym][col].to_numpy(dtype=float)[:n]


def _regimes(
    frames: dict[str, pd.DataFrame] | None, bench: str | None, n: int, timeframe: str
) -> list[str] | None:
    """Per session: 'up' / 'down' from the benchmark's PRIOR close vs its average."""
    closes = _arr(frames, bench, "close", n)
    if closes is None:
        return None
    window = REGIME_DAYS * bars_per_session(timeframe)
    s = pd.Series(closes).ffill()
    avg = s.rolling(window, min_periods=window).mean()
    prev_c, prev_a = s.shift(1).to_numpy(), avg.shift(1).to_numpy()
    out = []
    for c, a in zip(prev_c, prev_a, strict=True):
        if math.isnan(a) or math.isnan(c):
            out.append("warmup")
        else:
            out.append("up" if c > a else "down")
    return out


# Summaries -----------------------------------------------------------------------------
def trade_stats(trips: pd.DataFrame) -> dict[str, Any]:
    n = len(trips)
    out: dict[str, Any] = {"count": n, "low_sample": n < LOW_SAMPLE_TRADES}
    if not n:
        return out
    pnl = trips["pnl"].to_numpy(dtype=float)
    ret = trips["ret"].to_numpy(dtype=float)
    wins, losses = pnl > 0, pnl <= 0
    gross_win, gross_loss = float(pnl[wins].sum()), float(-pnl[losses].sum())
    hold = pd.to_numeric(trips["hold_sessions"], errors="coerce").to_numpy(dtype=float)
    avg_win = float(pnl[wins].mean()) if wins.any() else None
    avg_loss = float(pnl[losses].mean()) if losses.any() else None
    out.update(
        {
            "wins": int(wins.sum()),
            "losses": int(losses.sum()),
            "win_rate": float(wins.mean()),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_win_pct": float(ret[wins].mean()) if wins.any() else None,
            "avg_loss_pct": float(ret[losses].mean()) if losses.any() else None,
            "payoff_ratio": (avg_win / -avg_loss) if avg_win and avg_loss else None,
            "expectancy": float(pnl.mean()),
            "expectancy_pct": float(ret.mean()),
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
            "gross_profit": gross_win,
            "gross_loss": -gross_loss,
            "largest_win": float(pnl[wins].max()) if wins.any() else None,
            "largest_loss": float(pnl[losses].min()) if losses.any() else None,
            "t_stat": _t_stat(ret),
            "avg_hold": float(np.nanmean(hold)) if np.isfinite(hold).any() else None,
            "median_hold": float(np.nanmedian(hold)) if np.isfinite(hold).any() else None,
            "avg_hold_win": _nanmean(hold[wins]),
            "avg_hold_loss": _nanmean(hold[losses]),
            "max_consecutive_losses": _max_run(losses),
            "max_consecutive_wins": _max_run(wins),
            "avg_mae": _nanmean(trips["mae"].to_numpy(dtype=float)),
            "avg_mfe": _nanmean(trips["mfe"].to_numpy(dtype=float)),
            "avg_mae_winners": _nanmean(trips["mae"].to_numpy(dtype=float)[wins]),
            "avg_mfe_losers": _nanmean(trips["mfe"].to_numpy(dtype=float)[losses]),
        }
    )
    return out


def _nanmean(x: np.ndarray) -> float | None:
    x = x[np.isfinite(x)]
    return float(x.mean()) if x.size else None


def _max_run(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def edge_stats(trips: pd.DataFrame) -> dict[str, Any]:
    e = trips["edge"].to_numpy(dtype=float) if len(trips) else np.array([])
    e = e[np.isfinite(e)]
    if not e.size:
        return {"count": 0}
    return {
        "count": int(e.size),
        "mean": float(e.mean()),
        "median": float(np.median(e)),
        "t_stat": _t_stat(e),
        "beat_rate": float((e > 0).mean()),
        "low_sample": bool(e.size < LOW_SAMPLE_TRADES),
    }


def _group(trips: pd.DataFrame, key: pd.Series) -> list[dict[str, Any]]:
    rows = []
    for k, g in trips.groupby(key, sort=True):
        pnl = g["pnl"].to_numpy(dtype=float)
        edge = g["edge"].to_numpy(dtype=float)
        edge = edge[np.isfinite(edge)]
        rows.append(
            {
                "key": str(k),
                "trades": len(g),
                "win_rate": float((pnl > 0).mean()),
                "pnl": float(pnl.sum()),
                "avg_ret": float(g["ret"].mean()),
                "avg_edge": float(edge.mean()) if edge.size else None,
                "avg_hold": _nanmean(pd.to_numeric(g["hold_sessions"]).to_numpy(dtype=float)),
            }
        )
    return rows


def _series_stats(r: np.ndarray, bpy: int) -> dict[str, Any]:
    """Annualised volatility, Sharpe and Sortino of bar returns (risk-free 0)."""
    if r.size < 2:
        return {"volatility": None, "sharpe": None, "sortino": None}
    sd = float(r.std(ddof=1))
    downside = np.minimum(r, 0.0)
    dsd = float(math.sqrt((downside**2).mean()))
    return {
        "volatility": sd * math.sqrt(bpy) if sd > 0 else 0.0,
        "sharpe": float(r.mean() / sd * math.sqrt(bpy)) if sd > 0 else None,
        "sortino": float(r.mean() / dsd * math.sqrt(bpy)) if dsd > 0 else None,
    }


def _cagr(e: np.ndarray, bpy: int) -> float | None:
    if e.size < 2 or e[0] <= 0 or e[-1] <= 0:
        return None
    years = (e.size - 1) / bpy
    if years <= 0:
        return None
    return float((e[-1] / e[0]) ** (1 / years) - 1)


def drawdown_stats(e: np.ndarray, sessions: list[str]) -> dict[str, Any]:
    """Depth, length and recovery of drawdowns (lengths in sessions/bars)."""
    if not e.size:
        return {}
    peak = np.maximum.accumulate(e)
    dd = e / peak - 1
    trough = int(np.argmin(dd))
    peak_i = int(np.argmax(e[: trough + 1])) if trough else 0
    rec = next((k for k in range(trough, e.size) if e[k] >= peak[trough]), None)
    longest = cur = 0
    longest_start = cur_start = 0
    count_10 = 0
    in_10 = False
    for k in range(e.size):
        if dd[k] < 0:
            if cur == 0:
                cur_start = k - 1 if k else 0
            cur += 1
            if cur > longest:
                longest, longest_start = cur, cur_start
        else:
            cur = 0
        if dd[k] <= -0.10 and not in_10:
            count_10 += 1
            in_10 = True
        if dd[k] >= 0:
            in_10 = False
    return {
        "max_drawdown": float(dd.min()),
        "peak_session": sessions[peak_i] if sessions else None,
        "trough_session": sessions[trough] if sessions else None,
        "recovery_session": sessions[rec] if rec is not None and sessions else None,
        "max_dd_length": (rec - peak_i) if rec is not None else None,
        "longest_underwater": longest,
        "longest_underwater_start": sessions[longest_start] if sessions and longest else None,
        "current_drawdown": float(dd[-1]),
        "drawdowns_over_10pct": count_10,
        "time_underwater": float((dd < 0).mean()),
    }


def _period_returns(values: np.ndarray, labels: list[str]) -> dict[str, float]:
    """Compounded return per label (e.g. 'YYYY' or 'YYYY-MM'), first value as the base."""
    out: dict[str, float] = {}
    prev = values[0] if values.size else math.nan
    start = prev
    current: str | None = None
    for v, lab in zip(values, labels, strict=True):
        if lab != current:
            if current is not None:
                out[current] = float(prev / start - 1) if start else math.nan
            current, start = lab, prev
        prev = v
    if current is not None:
        out[current] = float(prev / start - 1) if start else math.nan
    return out


def _beta(r: np.ndarray, b: np.ndarray, bpy: int) -> dict[str, Any]:
    ok = np.isfinite(r) & np.isfinite(b)
    r, b = r[ok], b[ok]
    if r.size < 3 or float(b.var(ddof=1)) <= 0:
        return {"beta": None, "correlation": None, "alpha": None}
    cov = float(np.cov(r, b, ddof=1)[0, 1])
    beta = cov / float(b.var(ddof=1))
    sr, sb = float(r.std(ddof=1)), float(b.std(ddof=1))
    corr = cov / (sr * sb) if sr > 0 and sb > 0 else None
    alpha = float((r.mean() - beta * b.mean()) * bpy)
    return {"beta": beta, "correlation": corr, "alpha": alpha}


def analyze(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    decisions: pd.DataFrame | None = None,
    frames: dict[str, pd.DataFrame] | None = None,
    benchmark: str | None = None,
    timeframe: str = "1d",
) -> dict[str, Any]:
    """The full analysis of one run. JSON-ready apart from NaN (callers clean it)."""
    bpy = bars_per_year(timeframe)
    sessions = [str(s) for s in equity["session"]] if len(equity) else []
    e = equity["equity"].to_numpy(dtype=float) if len(equity) else np.array([])
    has_bench = "benchmark_equity" in equity and equity["benchmark_equity"].notna().all()
    be = equity["benchmark_equity"].to_numpy(dtype=float) if has_bench else None
    r = np.diff(e) / e[:-1] if e.size > 1 else np.array([])
    br = np.diff(be) / be[:-1] if be is not None and be.size > 1 else None

    trips = round_trips(fills, equity, decisions, frames, benchmark, timeframe)
    out: dict[str, Any] = {"trades": trade_stats(trips), "edge": edge_stats(trips)}

    ret = {"cagr": _cagr(e, bpy), **_series_stats(r, bpy)}
    dd = drawdown_stats(e, sessions)
    mdd = dd.get("max_drawdown")
    ret["calmar"] = (ret["cagr"] / -mdd) if ret["cagr"] is not None and mdd else None
    out["returns"] = ret
    out["drawdown"] = dd
    if be is not None and br is not None:
        bdd = drawdown_stats(be, sessions)
        bstats = {"cagr": _cagr(be, bpy), **_series_stats(br, bpy)}
        bm = bdd.get("max_drawdown")
        bstats["calmar"] = (bstats["cagr"] / -bm) if bstats["cagr"] is not None and bm else None
        bstats["max_drawdown"] = bm
        bstats["longest_underwater"] = bdd.get("longest_underwater")
        bstats["max_dd_length"] = bdd.get("max_dd_length")
        out["benchmark"] = bstats
        out["market"] = _beta(r, br, bpy)

    # Costs and turnover
    sf = strategy_fills(fills)
    costs = float(fills["cost"].sum()) if len(fills) else 0.0
    gross = sf["gross"] if "gross" in sf else sf["qty"] * sf["price"] if len(sf) else pd.Series([])
    traded = float(pd.to_numeric(gross, errors="coerce").fillna(0).sum()) if len(sf) else 0.0
    years = (e.size - 1) / bpy if e.size > 1 else 0.0
    avg_eq = float(e.mean()) if e.size else 0.0
    start = float(e[0]) if e.size else 0.0
    pnl_total = float(e[-1] - e[0]) if e.size else 0.0
    # Trade P&L before costs, to say what share of it the trades' costs took.
    trade_costs = float(trips["costs"].sum()) if len(trips) else 0.0
    before = (float(trips["pnl"].sum()) if len(trips) else 0.0) + trade_costs
    out["costs"] = {
        "total": costs,
        "return_before_costs": (pnl_total + costs) / start if start else None,
        "cost_drag": costs / start if start else None,
        "share_of_gross_profit": trade_costs / before if before > 0 else None,
        "turnover": (traded / 2) / avg_eq / years if avg_eq and years > 0 else None,
        "traded_notional": traded,
    }

    # Signal funnel
    out["funnel"] = _funnel(decisions, sf)

    # Calendar breakdowns
    years_lab = [s[:4] for s in sessions]
    months_lab = [s[:7] for s in sessions]
    by_year_ret = _period_returns(e, years_lab) if e.size else {}
    by_year_bench = _period_returns(be, years_lab) if be is not None else {}
    trips_year = trips["entry_session"].str[:4] if len(trips) else pd.Series([], dtype=str)
    year_trades = {g["key"]: g for g in _group(trips, trips_year)} if len(trips) else {}
    out["by_year"] = [
        {
            "year": y,
            "ret": by_year_ret.get(y),
            "bench_ret": by_year_bench.get(y),
            "trades": year_trades.get(y, {}).get("trades", 0),
            "win_rate": year_trades.get(y, {}).get("win_rate"),
            "pnl": year_trades.get(y, {}).get("pnl", 0.0),
            "avg_edge": year_trades.get(y, {}).get("avg_edge"),
        }
        for y in sorted(by_year_ret)
    ]
    m_ret = _period_returns(e, months_lab) if e.size else {}
    m_bench = _period_returns(be, months_lab) if be is not None else {}
    out["monthly"] = [{"month": m, "ret": v, "bench_ret": m_bench.get(m)} for m, v in m_ret.items()]
    neg = np.array([v < 0 for v in m_ret.values()])
    out["streaks"] = {
        "max_consecutive_losing_trades": out["trades"].get("max_consecutive_losses", 0),
        "max_consecutive_losing_months": _max_run(neg) if neg.size else 0,
        "losing_months": int(neg.sum()) if neg.size else 0,
        "months": int(neg.size),
    }

    if len(trips):
        out["by_symbol"] = sorted(
            _group(trips, trips["symbol"]), key=lambda g: g["pnl"], reverse=True
        )
        out["by_exit"] = _group(trips, trips["exit_kind"])
    else:
        out["by_symbol"], out["by_exit"] = [], []
    out["by_regime"] = _by_regime(trips, frames, benchmark, e, be, timeframe, len(sessions))
    out["trade_list"] = [
        {k: (_clean(v)) for k, v in row.items()} for row in trips.to_dict("records")
    ]
    return out


def _clean(v: Any) -> Any:
    if isinstance(v, float | np.floating):
        return _f(v)
    if isinstance(v, np.integer):
        return int(v)
    return v


def _funnel(decisions: pd.DataFrame | None, sf: pd.DataFrame) -> dict[str, Any]:
    if decisions is None or not len(decisions) or "action" not in decisions:
        return {}
    act = decisions["action"]
    reason = decisions["reason"].astype(str)
    enters = int((act == "enter").sum())
    # Signals that passed every condition but were not acted on at the close.
    close_skips = reason[(act == "skip") & ~reason.isin(_OPEN_SKIPS)]
    open_skips = reason[(act == "skip") & reason.isin(_OPEN_SKIPS)]
    signals = enters + len(close_skips)
    filled = int((sf["side"] == "buy").sum()) if len(sf) else 0
    return {
        "signals": signals,
        "entered": enters,
        "filled": filled,
        "taken_rate": enters / signals if signals else None,
        "fill_rate": filled / enters if enters else None,
        "skipped": {str(k): int(v) for k, v in close_skips.value_counts().items()},
        "not_filled": {str(k): int(v) for k, v in open_skips.value_counts().items()},
        "near_misses": int((act == "no_signal").sum()),
    }


_OPEN_SKIPS = ("insufficient_cash_at_open", "outside_range", "entry not filled")


def _by_regime(
    trips: pd.DataFrame,
    frames: dict[str, pd.DataFrame] | None,
    bench: str | None,
    e: np.ndarray,
    be: np.ndarray | None,
    timeframe: str,
    n: int,
) -> list[dict[str, Any]]:
    regimes = _regimes(frames, bench, n, timeframe)
    if regimes is None or e.size < 2:
        return []
    reg = np.array(regimes)
    r = np.diff(e) / e[:-1]
    br = np.diff(be) / be[:-1] if be is not None else None
    rows = []
    for name in ("up", "down", "warmup"):
        mask = reg[1:] == name  # a bar's return belongs to the regime known at its start
        if not mask.any():
            continue
        g = trips[trips["regime"] == name] if len(trips) else trips
        pnl = g["pnl"].to_numpy(dtype=float) if len(g) else np.array([])
        edge = g["edge"].to_numpy(dtype=float) if len(g) else np.array([])
        edge = edge[np.isfinite(edge)]
        rows.append(
            {
                "regime": name,
                "sessions": int(mask.sum()),
                "ret": float(np.prod(1 + r[mask]) - 1),
                "bench_ret": float(np.prod(1 + br[mask]) - 1) if br is not None else None,
                "trades": len(g),
                "win_rate": float((pnl > 0).mean()) if pnl.size else None,
                "avg_ret": float(g["ret"].mean()) if len(g) else None,
                "avg_edge": float(edge.mean()) if edge.size else None,
            }
        )
    return rows


def headline(analysis: dict[str, Any]) -> dict[str, Any]:
    """The few analysis numbers stored flat on the run for the run list."""
    t, ed, rt = analysis.get("trades", {}), analysis.get("edge", {}), analysis.get("returns", {})
    return {
        "win_rate": t.get("win_rate"),
        "profit_factor": t.get("profit_factor"),
        "expectancy": t.get("expectancy"),
        "cagr": rt.get("cagr"),
        "edge_mean": ed.get("mean"),
        "edge_t": ed.get("t_stat"),
        "low_sample": t.get("low_sample", True),
    }
