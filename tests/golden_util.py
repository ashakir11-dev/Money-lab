"""Golden ledgers: fingerprints of every decision, fill and equity row for a fixed set of
strategies on deterministic data, recorded on engine/0.2.0.

The engine may change shape (new exits, sizing, ranking), but a strategy that uses none
of the new options must keep producing exactly these ledgers. Regenerate only when a
change is meant to alter existing results: ``uv run python tests/golden_util.py``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_capacity import cross, hold, rsi, signal, variants  # noqa: E402
from make_capacity_dataset import build  # noqa: E402

from moneylab.data.snapshot import MarketData, normalise  # noqa: E402
from moneylab.engine.config import (  # noqa: E402
    Condition,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    resolve,
)
from moneylab.engine.simulator import RunResult, simulate  # noqa: E402
from moneylab.settings import Settings  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "ledger_hashes.json"
N_SYMBOLS = 60


def dataset() -> pd.DataFrame:
    df = build()
    keep = {f"S{i:03d}" for i in range(1, N_SYMBOLS + 1)} | {"SPY"}
    return normalise(df[df["symbol"].isin(keep)])


def configs(universe: list[str]) -> list[StrategyConfig]:
    out = variants(universe)
    macd = Condition(
        kind="threshold",
        indicator=IndicatorRef(id="macd", params={}),
        params={"op": "above", "value": 0.0},
    )
    breadth = Condition(
        kind="threshold",
        indicator=IndicatorRef(id="breadth", params={"length": 50}),
        params={"op": "above", "value": 0.5},
    )
    extra = [
        ("G11 SMA20 cross + MACD hist > 0, hold 10", [cross(20), macd], hold(10), {}),
        (
            "G12 RSI<35 + breadth > 0.5, exit RSI>60",
            [rsi("below", 35), breadth],
            signal(rsi("above", 60)),
            {"max_positions": 4, "target_allocation": 0.3},
        ),
        (
            "G13 SMA20 cross, hold 3, 50 bps, tight cash",
            [cross(20)],
            hold(3),
            {"target_allocation": 0.6, "max_positions": 2, "cost_bps_per_side": 50},
        ),
    ]
    out += [
        StrategyConfig(
            name=n,
            universe=universe,
            benchmark="SPY",
            timeframe="1d",
            entry=e,
            exit=x,
            portfolio=PortfolioSettings(**p),
        )
        for n, e, x, p in extra
    ]
    return out


def fingerprint(r: RunResult) -> dict[str, Any]:
    def h(df: pd.DataFrame) -> str:
        rows = df.astype(object).where(df.notna(), None).to_dict("records")
        blob = json.dumps(rows, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    return {
        "decisions": h(r.decisions),
        "fills": h(r.fills),
        "equity": h(r.equity),
        "n_decisions": len(r.decisions),
        "n_fills": len(r.fills),
        "final_equity": float(r.equity["equity"].iloc[-1]),
    }


def compute() -> dict[str, dict[str, Any]]:
    df = dataset()
    universe = sorted(s for s in df["symbol"].unique() if s != "SPY")
    sessions = sorted(df["date"].unique())
    first, last = str(sessions[0])[:10], str(sessions[-1])[:10]
    data = MarketData.build(df, [*universe, "SPY"], first, last)
    out = {}
    for cfg in configs(universe):
        resolved = resolve(cfg, Settings(_env_file=None))
        out[cfg.name] = fingerprint(simulate(resolved, data))
    return out


if __name__ == "__main__":
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(compute(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN}")
