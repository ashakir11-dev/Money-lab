"""Research 2: do the engine/0.3.0 building blocks change the RSI < 30 result?

Same universe, period, data and costs as research 1 (scripts/research_rsi_spy20.py):
SPY's end-2017 top 20, 2018-2024, Tiingo, 5 bps per side, $100,000. Variants add stops,
take-profits, a trailing stop, volatility sizing, and a trend rule (golden cross).

Run: ``uv run python scripts/research_rsi_stops.py`` (needs TIINGO_API_KEY).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_rsi_spy20 import UNIVERSE, config, round_trips

from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    SizingRule,
    StrategyConfig,
)
from moneylab.service import Lab


def exit_(kind: str, **params: Any) -> ExitRule:
    return ExitRule(kind=kind, params=params)


def sma(n: int) -> IndicatorRef:
    return IndicatorRef(id="sma", params={"length": n})


def variants() -> list[tuple[str, StrategyConfig]]:
    base = config(30, 10)
    hold10, hold20 = exit_("holding_period", sessions=10), exit_("holding_period", sessions=20)
    stop8, take15 = exit_("stop_loss", pct=0.08), exit_("take_profit", pct=0.15)
    golden = base.model_copy(
        update={
            "name": "SMA50 crosses above SMA200; exit on the cross below",
            "entry": [
                Condition(
                    kind="indicator_cross",
                    indicator=sma(50),
                    other=sma(200),
                    params={"direction": "above"},
                )
            ],
            "exits": [
                ExitRule(
                    kind="signal",
                    conditions=[
                        Condition(
                            kind="indicator_cross",
                            indicator=sma(50),
                            other=sma(200),
                            params={"direction": "below"},
                        )
                    ],
                )
            ],
        }
    )
    return [
        ("RSI < 30, hold 10 (research 1)", base),
        ("+ stop-loss 8%", base.model_copy(update={"exits": [stop8, hold10]})),
        (
            "+ stop 8%, take-profit 15%, hold 20",
            base.model_copy(update={"exits": [stop8, take15, hold20]}),
        ),
        (
            "+ trailing stop 10%, hold 20",
            base.model_copy(update={"exits": [exit_("trailing_stop", pct=0.10), hold20]}),
        ),
        (
            "+ volatility sizing (1% risk, 2 ATR)",
            base.model_copy(update={"sizing": SizingRule(id="volatility")}),
        ),
        ("Golden cross trend (SMA50 x SMA200)", golden),
    ]


def main() -> None:
    lab = Lab.open()
    snap = lab.import_provider(
        [*UNIVERSE, "SPY"],
        date(2017, 1, 3),
        date(2024, 12, 31),
        label="SPY top 20 (end-2017) + SPY, 2017-01 to 2024-12, Tiingo",
    )
    bars = snap.bars[snap.bars.symbol == "SPY"]
    spy_open = pd.Series(bars["open"].values, index=bars["date"].dt.strftime("%Y-%m-%d"))
    out: dict[str, Any] = {}
    parent = None
    for name, cfg in variants():
        rid = lab.run(cfg.model_copy(update={"name": name}), snap.id, parent_id=parent)
        parent = parent or rid
        m = lab.store.get(rid)["metrics"]
        t = round_trips(lab, rid, spy_open)
        dec = lab.store.ledgers(rid).decisions
        exits = dec[dec.action == "exit"].reason.str.split(":").str[0].str.split(" ").str[0]
        out[name] = {
            "run": rid,
            "total_return": m["total_return"],
            "spy_total_return": m["benchmark"]["total_return"],
            "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"],
            "avg_exposure": m["avg_exposure"],
            "round_trips": len(t),
            "edge": t.edge.mean() if len(t) else None,
            "edge_t": t.edge.mean() / (t.edge.std(ddof=1) / np.sqrt(len(t)))
            if len(t) > 1
            else None,
            "beat_spy_rate": (t.edge > 0).mean() if len(t) else None,
            "exit_reasons": exits.value_counts().to_dict(),
        }
    print(f"snapshot {snap.id}")
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
