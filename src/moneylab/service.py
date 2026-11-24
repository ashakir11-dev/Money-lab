"""Application service: the single entry point the dashboard, CLI and tests use."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from moneylab.data.actions import POLICY as ACTION_POLICY
from moneylab.data.actions import adjust, read_actions
from moneylab.data.snapshot import (
    DEFAULT_ADJUSTMENT,
    MarketData,
    Snapshot,
    load_snapshot,
    read_import_file,
    save_snapshot,
)
from moneylab.engine.config import StrategyConfig, check_versions, config_hash, resolve
from moneylab.engine.reporting import config_diff, first_difference, metrics
from moneylab.engine.simulator import simulate
from moneylab.settings import Settings, get_settings
from moneylab.store import RunStore


@dataclass
class Lab:
    settings: Settings
    store: RunStore
    _data_cache: dict[tuple[Any, ...], MarketData] = field(default_factory=dict, repr=False)

    @classmethod
    def open(cls, home: Path | None = None) -> Lab:
        s = get_settings()
        if home is not None:
            s = s.model_copy(update={"home": home})
        return cls(s, RunStore(s.home))

    # Data -------------------------------------------------------------------------
    def import_file(
        self, path: Path, label: str = "", actions_path: Path | None = None
    ) -> Snapshot:
        """Import a CSV as a snapshot. With ``actions_path`` the CSV holds raw prices and
        is adjusted for the listed splits and dividends first."""
        bars = read_import_file(path)
        adjustment = DEFAULT_ADJUSTMENT
        source = f"file:{path.name}"
        if actions_path is not None:
            bars = adjust(bars, read_actions(actions_path))
            adjustment = ACTION_POLICY
            source += f"+actions:{actions_path.name}"
        return self.import_frame(bars, source=source, adjustment=adjustment, label=label)

    def import_frame(
        self,
        bars: pd.DataFrame,
        *,
        source: str,
        adjustment: str = DEFAULT_ADJUSTMENT,
        label: str = "",
    ) -> Snapshot:
        return save_snapshot(
            bars, self.settings.snapshot_dir, source=source, adjustment=adjustment, label=label
        )

    def snapshot(self, sid: str) -> Snapshot:
        return load_snapshot(sid, self.settings.snapshot_dir)

    # Runs -------------------------------------------------------------------------
    def run(self, config: StrategyConfig, snapshot_id: str, parent_id: str | None = None) -> str:
        """Resolve a user config with today's defaults, run it and save it."""
        return self.execute(resolve(config, self.settings), snapshot_id, parent_id)

    def duplicate(self, run_id: str, name: str | None = None) -> str:
        """Re-run a saved run exactly as resolved: same settings, data and versions.

        Current defaults are not consulted. Refuses if a component or the engine has a
        different version than the one the original run used.
        """
        orig = self.store.get(run_id)
        resolved = {k: v for k, v in orig["resolved"].items() if k != "data"}
        check_versions(resolved)
        resolved["name"] = name or f"{resolved['name']} (duplicate)"
        return self.execute(resolved, orig["snapshot_id"], parent_id=run_id)

    def market_data(
        self, snapshot_id: str, symbols: list[str], start: str | None, end: str | None
    ) -> MarketData:
        """Aligned bars, built once per (snapshot, symbols, dates) and shared read-only."""
        key = (snapshot_id, tuple(sorted(set(symbols))), start, end)
        if key not in self._data_cache:
            snap = self.snapshot(snapshot_id)
            self._data_cache[key] = MarketData.build(snap.bars, list(key[1]), start, end)
        return self._data_cache[key]

    def execute(
        self, resolved: dict[str, Any], snapshot_id: str, parent_id: str | None = None
    ) -> str:
        snap = self.snapshot(snapshot_id)
        data = self.market_data(
            snap.id,
            resolved["universe"] + [resolved["benchmark"]],
            resolved["start"],
            resolved["end"],
        )
        result = simulate(resolved, data)
        m = metrics(result.equity, result.fills)
        if "benchmark_equity" in result.equity:
            m["benchmark"] = metrics(result.equity, result.fills, column="benchmark_equity")
        resolved = dict(resolved)
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
