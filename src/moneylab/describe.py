"""Plain-English descriptions shared by the API and the dashboards: where data came
from (real, demo or an imported file) and a one-sentence strategy summary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "data"


# Data source labels -----------------------------------------------------------------
@dataclass(frozen=True)
class DataSource:
    kind: str  # "real" | "demo" | "file"
    badge: str  # short badge text
    detail: str  # one line for captions


def _fixture_names() -> set[str]:
    return {p.name for p in FIXTURE_DIR.glob("*.csv")} if FIXTURE_DIR.exists() else set()


def data_source(source: str | None, label: str = "") -> DataSource:
    """Classify a snapshot/run source string.

    ``equibles`` and ``tiingo`` are real market history; a file under ``fixtures/data`` is
    the hand-designed demo data; any other file is the person's own import.
    """
    src = source or ""
    if src == "equibles":
        return DataSource("real", "Real data · Equibles", "Real prices from Equibles")
    if src == "tiingo":
        return DataSource("real", "Real data · Tiingo", "Real end-of-day prices from Tiingo")
    if src.startswith("file:"):
        name = src[len("file:") :].split("+", 1)[0]
        if name in _fixture_names():
            return DataSource(
                "demo", "Demo data", f"Synthetic demo fixture ({name}), not real prices"
            )
        return DataSource("file", "Imported file", f"Imported from {name}")
    return DataSource("file", src or "Unknown source", label or src)


# Strategy summary ---------------------------------------------------------------------
def _plain(ref: dict[str, Any]) -> str | None:
    """Plain names for indicators whose generic NAME(args) form reads badly."""
    p = ref.get("params") or {}
    rid = ref["id"]
    swing = f"{p.get('left', 5)}/{p.get('right', 5)} bars"
    if rid == "adx":
        name = {"adx": "ADX", "plus_di": "+DI", "minus_di": "-DI"}[p.get("output", "adx")]
        return f"{name}({p.get('length', 14)})"
    if rid == "linreg_trend":
        what = {"slope_pct_year": "trend slope %/yr", "tstat": "trend t-stat", "r2": "trend R²"}
        return f"{what[p.get('output', 'slope_pct_year')]} over {p.get('length', 50)} bars"
    if rid == "market_structure":
        return f"market structure (swings {swing})"
    if rid in ("swing_high", "swing_low"):
        return f"last swing {rid.split('_')[1]} ({swing})"
    if rid in ("support", "resistance", "support_distance", "resistance_distance"):
        side = rid.split("_")[0]
        turns = f"{p.get('min_move', 2):g} ATR turns"
        if rid.endswith("_distance"):
            return f"distance to {side} in ATRs ({turns})"
        return f"{side} ({turns})"
    if rid == "straightness":
        name = "efficiency" if p.get("output") == "unsigned" else "straightness"
        return f"{name}({p.get('length', 20)})"
    return None


def _indicator(ref: dict[str, Any]) -> str:
    plain = _plain(ref)
    if plain is not None:
        return plain
    params = ref.get("params") or {}
    args = ", ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in params.values())
    return f"{ref['id'].upper()}({args})" if args else ref["id"].upper()


def condition_text(cond: dict[str, Any]) -> str:
    ind = _indicator(cond["indicator"])
    p = cond.get("params") or {}
    if cond["kind"] == "price_cross":
        return f"close crosses {p.get('direction', 'above')} {ind}"
    if cond["kind"] in ("indicator_compare", "indicator_cross") and cond.get("other"):
        other = _indicator(cond["other"])
        if cond["kind"] == "indicator_cross":
            return f"{ind} crosses {p.get('direction', 'above')} {other}"
        return f"{ind} {p.get('op', 'above')} {other}"
    if cond["kind"] == "threshold":
        v = p.get("value")
        return f"{ind} {p.get('op', 'below')} {v:g}" if isinstance(v, int | float) else ind
    extra = ", ".join(f"{k}={v}" for k, v in p.items())
    return f"{cond['kind']} {ind}" + (f" ({extra})" if extra else "")


def exit_text(ex: dict[str, Any]) -> str:
    """One exit rule in words, e.g. "after 10 sessions" or "when RSI(14) above 70"."""
    kind = ex.get("kind")
    params = ex.get("params") or {}
    if kind == "holding_period":
        return f"after {params.get('sessions')} sessions"
    if kind == "signal" or kind is None:
        conds = ex.get("conditions") or []
        sig = " or ".join(condition_text(c) for c in conds) or "a signal fires"
        cap = params.get("max_sessions")
        return f"when {sig}" + (f" or after {cap} sessions" if cap else "")
    pct = params.get("pct")
    if kind in _PRICE_EXITS and isinstance(pct, int | float):
        return _PRICE_EXITS[kind].format(pct=_pct(pct))
    args = ", ".join(
        f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items()
    )
    return f"on {kind}" + (f" ({args})" if args else "")


_PRICE_EXITS = {
    "stop_loss": "if it closes {pct} below the buy price (stop loss)",
    "take_profit": "if it closes {pct} above the buy price (take profit)",
    "trailing_stop": "if it closes {pct} below its highest close (trailing stop)",
}


def _pct(x: float) -> str:
    """0.08 -> "8%", 0.125 -> "12.5%"."""
    return f"{x * 100:.2f}".rstrip("0").rstrip(".") + "%"


def sizing_text(resolved: dict[str, Any]) -> str:
    """How much each entry buys, e.g. ", 10% of equity per position"."""
    pf = resolved.get("portfolio") or {}
    alloc = pf.get("target_allocation")
    per = f"{alloc:.0%} of equity" if isinstance(alloc, int | float) else "the target allocation"
    sz = resolved.get("sizing") or {}
    params = sz.get("params") or {}
    if sz.get("id") == "fixed_dollar":
        amount = params.get("amount")
        if isinstance(amount, int | float):
            return f", ${amount:,.0f} per position"
    if sz.get("id") == "volatility":
        risk = params.get("risk_fraction")
        mult = params.get("atr_multiple")
        length = params.get("atr_length")
        if isinstance(risk, int | float) and isinstance(mult, int | float):
            return (
                f", sized to risk {_pct(risk)} of equity on a {mult:g} x ATR({length}) stop, "
                f"at most {per} per position"
            )
    return f", {per} per position" if isinstance(alloc, int | float) else ""


def rank_text(resolved: dict[str, Any]) -> str:
    """Candidate ordering in words, or "" when candidates go in symbol order."""
    rank = resolved.get("rank_by")
    if not rank or not rank.get("indicator"):
        return ""
    first = "lowest" if rank.get("order", "ascending") == "ascending" else "highest"
    ind = _indicator(rank["indicator"])
    return f" When there are more candidates than room, it buys the {first} {ind} first."


def strategy_summary(resolved: dict[str, Any]) -> str:
    """Plain-English summary of a strategy config (resolved or draft)."""
    entry = resolved.get("entry") or []
    joiner = " and " if resolved.get("entry_combine", "all") == "all" else " or "
    enter = joiner.join(condition_text(c) for c in entry) or "no entry rule"
    exits = resolved.get("exits") or [resolved.get("exit") or {}]
    leave = ", or ".join(exit_text(ex) for ex in exits)
    uni = resolved.get("universe") or []
    sizing = sizing_text(resolved)
    fill = resolved.get("entry_fill") or {}
    kind = fill.get("kind") or "next_open"
    if kind == "dip":
        pct = fill.get("dip_pct")
        how = f" on a {pct:.0%} dip" if isinstance(pct, int | float) else " on a dip"
    elif kind == "next_range":
        how = " on the next bar in range"
    else:
        how = " at the next open"
    return (
        f"Buy when {enter}{how}; sell {leave}. "
        f"Trades {len(uni)} symbol{'s' if len(uni) != 1 else ''} against "
        f"{resolved.get('benchmark', '?')}{sizing}{_bars_text(resolved)}.{rank_text(resolved)}"
    )


def _bars_text(resolved: dict[str, Any]) -> str:
    """Name the bar size when it is not daily (counts such as 'after 10 sessions' are bars)."""
    tf = resolved.get("timeframe") or "1d"
    return "" if tf == "1d" else f", on {tf} bars"
