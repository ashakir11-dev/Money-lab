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

    ``tiingo`` is real end-of-day market history; a file under ``fixtures/data`` is
    the hand-designed demo data; any other file is the person's own import.
    """
    src = source or ""
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
def _indicator(ref: dict[str, Any]) -> str:
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
    if isinstance(pct, int | float):
        if kind == "stop_loss":
            return f"on a {pct * 100:.4g}% stop loss"
        if kind == "take_profit":
            return f"at a {pct * 100:.4g}% profit target"
        if kind == "trailing_stop":
            return f"on a {pct * 100:.4g}% trailing stop"
    args = ", ".join(
        f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items()
    )
    return f"on {kind}" + (f" ({args})" if args else "")


def _sizing_text(resolved: dict[str, Any]) -> str:
    alloc = (resolved.get("portfolio") or {}).get("target_allocation")
    sz = resolved.get("sizing") or {}
    p = sz.get("params") or {}
    sid = sz.get("id") or "percent_of_equity"
    if sid == "fixed_dollar" and isinstance(p.get("amount"), int | float):
        return f", ${p['amount']:,.0f} per position"
    if sid == "volatility" and isinstance(p.get("risk_fraction"), int | float):
        cap = f", capped at {alloc:.0%} of equity" if isinstance(alloc, int | float) else ""
        return (
            f", sized to risk {p['risk_fraction']:.2%} of equity over "
            f"{p.get('atr_multiple', 2):g}x ATR({p.get('atr_length', 14)}){cap}"
        )
    if sid != "percent_of_equity":
        return f", sized by {sid}"
    return f", {alloc:.0%} of equity per position" if isinstance(alloc, int | float) else ""


def strategy_summary(resolved: dict[str, Any]) -> str:
    """Plain-English summary of a strategy config (resolved or draft)."""
    entry = resolved.get("entry") or []
    joiner = " and " if resolved.get("entry_combine", "all") == "all" else " or "
    enter = joiner.join(condition_text(c) for c in entry) or "no entry rule"
    exits = resolved.get("exits") or [resolved.get("exit") or {}]
    leave = ", or ".join(exit_text(ex) for ex in exits)
    uni = resolved.get("universe") or []
    sizing = _sizing_text(resolved)
    rank = resolved.get("rank_by")
    ranked = ""
    if rank and rank.get("indicator"):
        first = "lowest" if rank.get("order", "ascending") == "ascending" else "highest"
        ranked = f" Candidates are taken {first} {_indicator(rank['indicator'])} first."
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
        f"{resolved.get('benchmark', '?')}{sizing}.{ranked}"
    )
