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
        return "when " + (" and ".join(condition_text(c) for c in conds) or "a signal fires")
    args = ", ".join(
        f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items()
    )
    return f"on {kind}" + (f" ({args})" if args else "")


def strategy_summary(resolved: dict[str, Any]) -> str:
    """Plain-English summary of a strategy config (resolved or draft)."""
    entry = resolved.get("entry") or []
    joiner = " and " if resolved.get("entry_combine", "all") == "all" else " or "
    enter = joiner.join(condition_text(c) for c in entry) or "no entry rule"
    exits = resolved.get("exits") or [resolved.get("exit") or {}]
    leave = ", or ".join(exit_text(ex) for ex in exits)
    uni = resolved.get("universe") or []
    pf = resolved.get("portfolio") or {}
    alloc = pf.get("target_allocation")
    sizing = f", {alloc:.0%} of equity per position" if isinstance(alloc, int | float) else ""
    return (
        f"Buy when {enter}; sell {leave}. "
        f"Trades {len(uni)} symbol{'s' if len(uni) != 1 else ''} against "
        f"{resolved.get('benchmark', '?')}{sizing}."
    )
