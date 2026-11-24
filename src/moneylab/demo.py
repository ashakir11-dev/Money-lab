"""The RSI-threshold demo path, shared by the CLI, tests and ledger export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from moneylab.engine.config import Condition, IndicatorRef, StrategyConfig
from moneylab.service import Lab

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "data" / "rsi_divergence.csv"
UNIVERSE = ["AAA", "BBB", "CCC", "DDD"]


def rsi_strategy(threshold: float, name: str | None = None) -> StrategyConfig:
    return StrategyConfig(
        name=name or f"SMA20 cross + RSI14 < {threshold:g}",
        universe=UNIVERSE,
        benchmark="SPY",
        entry=[
            Condition(
                kind="price_cross",
                indicator=IndicatorRef(id="sma", params={"length": 20}),
                params={"direction": "above"},
            ),
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="rsi", params={"length": 14}),
                params={"op": "below", "value": threshold},
            ),
        ],
    )


def run_rsi_demo(lab: Lab | None = None) -> dict[str, Any]:
    lab = lab or Lab.open()
    snap = lab.import_file(FIXTURE, label="RSI divergence fixture")
    original = lab.run(rsi_strategy(30), snap.id)
    changed = lab.run(rsi_strategy(35), snap.id, parent_id=original)
    return {"lab": lab, "snapshot": snap.id, "original": original, "changed": changed}
