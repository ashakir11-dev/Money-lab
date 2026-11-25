"""Research question 1: does buying SPY's top 20 holdings when RSI14 < 30 beat holding SPY?

Imports Tiingo history (needs TIINGO_API_KEY), runs RSI14 < 30 with a 10-session hold and
four neighbouring variants, then compares every round trip with SPY over the same window.
Write-up: docs/research/rsi30-spy-top20.md. Run: uv run python scripts/research_rsi_spy20.py
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from moneylab.engine.config import Condition, ExitRule, IndicatorRef, StrategyConfig
from moneylab.service import Lab

# SPY's 20 largest holdings at the end of 2017, so the list uses no later knowledge.
UNIVERSE = ["AAPL", "MSFT", "AMZN", "META", "BRK-B", "JNJ", "JPM", "XOM", "GOOGL", "GOOG",
            "BAC", "WFC", "CVX", "INTC", "T", "PG", "HD", "UNH", "V", "PFE"]  # fmt: skip
VARIANTS = [(30, 10), (25, 10), (35, 10), (30, 5), (30, 20)]  # (RSI threshold, sessions held)


def config(threshold: float, hold: int) -> StrategyConfig:
    return StrategyConfig(
        name=f"SPY top 20: RSI14 < {threshold:g}, hold {hold}",
        universe=UNIVERSE,
        benchmark="SPY",
        start=date(2018, 1, 2),
        end=date(2024, 12, 31),
        entry=[
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="rsi", params={"length": 14}),
                params={"op": "below", "value": threshold},
            )
        ],
        exit=ExitRule(kind="holding_period", params={"sessions": hold}),
    )


def round_trips(lab: Lab, run_id: str, spy_open: pd.Series) -> pd.DataFrame:
    """Each sell matched FIFO to its buy: net return vs SPY open-to-open over the same dates."""
    held: dict[str, list] = {}
    rows = []
    for f in lab.store.ledgers(run_id).fills.itertuples():
        if f.side == "buy":
            held.setdefault(f.symbol, []).append(f)
            continue
        b = held[f.symbol].pop(0)
        ret = (f.gross - f.cost) / (b.gross + b.cost) - 1
        rows.append((b.session, f.symbol, ret, spy_open[f.session] / spy_open[b.session] - 1))
    t = pd.DataFrame(rows, columns=["entry", "symbol", "ret", "spy"])
    t["edge"] = t.ret - t.spy
    return t


def main() -> None:
    lab = Lab.open()
    snap = lab.import_provider(
        [*UNIVERSE, "SPY"], date(2017, 9, 1), date(2024, 12, 31),
        label="SPY top 20 (end-2017) + SPY, 2017-09 to 2024-12, Tiingo",
    )  # fmt: skip
    bars = snap.bars[snap.bars.symbol == "SPY"]
    spy_open = pd.Series(bars["open"].values, index=bars["date"].dt.strftime("%Y-%m-%d"))
    out, parent = {}, None
    for th, hold in VARIANTS:
        rid = lab.run(config(th, hold), snap.id, parent_id=parent)
        parent = parent or rid
        m = lab.store.get(rid)["metrics"]
        t = round_trips(lab, rid, spy_open)
        out[f"rsi<{th} hold {hold}"] = {
            "run": rid,
            "total_return": m["total_return"],
            "spy_total_return": m["benchmark"]["total_return"],
            "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"],
            "avg_exposure": m["avg_exposure"],
            "round_trips": len(t),
            "avg_trade": t.ret.mean(),
            "avg_spy_same_window": t.spy.mean(),
            "edge": t.edge.mean(),
            "edge_t": t.edge.mean() / (t.edge.std(ddof=1) / np.sqrt(len(t))),
            "beat_spy_rate": (t.edge > 0).mean(),
            "edge_by_year": t.groupby(t.entry.str[:4]).edge.mean().round(4).to_dict(),
        }
    print(f"snapshot {snap.id}")
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
