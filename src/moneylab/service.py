"""Application service: the single entry point the dashboard, CLI and tests use."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moneylab.data.snapshot import (
    MarketData,
    Snapshot,
    load_snapshot,
    read_import_file,
    save_snapshot,
)
from moneylab.engine.config import StrategyConfig, config_hash, resolve
from moneylab.engine.reporting import config_diff, first_difference, metrics
from moneylab.engine.simulator import simulate
from moneylab.settings import Settings, get_settings
from moneylab.store import RunStore


@dataclass
class Lab:
    settings: Settings
    store: RunStore

    @classmethod
    def open(cls, home: Path | None = None) -> Lab:
        s = get_settings()
        if home is not None:
            s = s.model_copy(update={"home": home})
        return cls(s, RunStore(s.home))

    # Data -------------------------------------------------------------------------
    def import_file(self, path: Path, label: str = "") -> Snapshot:
        return save_snapshot(
            read_import_file(path),
            self.settings.snapshot_dir,
            source=f"file:{path.name}",
            label=label,
        )

    def snapshot(self, sid: str) -> Snapshot:
        return load_snapshot(sid, self.settings.snapshot_dir)

    # Runs -------------------------------------------------------------------------
    def run(self, config: StrategyConfig, snapshot_id: str, parent_id: str | None = None) -> str:
        resolved = resolve(config, self.settings)
        snap = self.snapshot(snapshot_id)
        data = MarketData.build(
            snap.bars,
            resolved["universe"] + [resolved["benchmark"]],
            resolved["start"],
            resolved["end"],
        )
        result = simulate(resolved, data)
        m = metrics(result.equity, result.fills)
        if "benchmark_equity" in result.equity:
            m["benchmark"] = metrics(result.equity, result.fills, column="benchmark_equity")
        resolved["data"] = {
            "snapshot_id": snap.id,
            "source": snap.meta["source"],
            "adjustment": snap.meta["adjustment"],
            "first_session": data.sessions[0].date().isoformat(),
            "last_session": data.sessions[-1].date().isoformat(),
        }
        return self.store.save(
            resolved=resolved,
            snapshot_id=snap.id,
            config_hash=config_hash(resolved),
            result=result,
            metrics=m,
            parent_id=parent_id,
        )

    def compare(self, run_a: str, run_b: str) -> dict[str, Any]:
        a, b = self.store.get(run_a), self.store.get(run_b)
        la, lb = self.store.ledgers(run_a), self.store.ledgers(run_b)
        return {
            "a": a,
            "b": b,
            "config_diff": config_diff(a["resolved"], b["resolved"]),
            "first_difference": first_difference(
                la.decisions, lb.decisions, la.fills, lb.fills, la.equity, lb.equity
            ),
        }
