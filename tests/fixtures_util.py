"""Helpers to build tiny hand-calculable bar sets on real XNYS sessions."""

from __future__ import annotations

from typing import Any

import exchange_calendars as xcals
import pandas as pd

from moneylab.data.snapshot import MarketData, normalise
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    resolve,
)
from moneylab.engine.simulator import RunResult, simulate
from moneylab.settings import Settings

SESSIONS = pd.DatetimeIndex(
    xcals.get_calendar("XNYS").sessions_in_range("2024-01-02", "2024-03-29")
).tz_localize(None)


def day(i: int) -> str:
    return SESSIONS[i].date().isoformat()


def bars(spec: dict[str, list[tuple[float, float] | None]]) -> pd.DataFrame:
    """symbol -> per-session (open, close); None means no bar that session."""
    rows = []
    for sym, days in spec.items():
        for i, oc in enumerate(days):
            if oc is None:
                continue
            o, c = oc
            rows.append(
                {
                    "date": SESSIONS[i],
                    "symbol": sym,
                    "open": o,
                    "high": max(o, c),
                    "low": min(o, c),
                    "close": c,
                    "volume": 1000,
                }
            )
    return normalise(pd.DataFrame(rows))


def level_strategy(
    universe: list[str], above: float, *, hold: int, **portfolio: Any
) -> StrategyConfig:
    """Enter whenever the close is above ``above`` (SMA of length 1 is the close)."""
    return StrategyConfig(
        name="fixture",
        universe=universe,
        benchmark=universe[0],
        entry=[
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="sma", params={"length": 1}),
                params={"op": "above", "value": above},
            )
        ],
        exit=ExitRule(kind="holding_period", params={"sessions": hold}),
        portfolio=PortfolioSettings(**portfolio),
    )


def run(df: pd.DataFrame, cfg: StrategyConfig) -> RunResult:
    resolved = resolve(cfg, Settings(_env_file=None))
    n = int(df["date"].nunique())
    data = MarketData.build(df, sorted(set(cfg.universe)), day(0), day(n - 1))
    return simulate(resolved, data)


def fills(r: RunResult) -> list[tuple[Any, ...]]:
    cols = ["session", "symbol", "side", "qty", "price", "cost", "cash_after"]
    return [tuple(x) for x in r.fills[cols].itertuples(index=False)]


def decisions(r: RunResult, *actions: str) -> list[tuple[str, str, str, str]]:
    d = r.decisions
    if actions:
        d = d[d["action"].isin(actions)]
    return [tuple(x) for x in d[["session", "symbol", "action", "reason"]].itertuples(index=False)]
