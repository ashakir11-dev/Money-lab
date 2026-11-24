"""Application service: the single entry point the dashboard, CLI and tests use."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from moneylab.data.actions import POLICY as ACTION_POLICY
from moneylab.data.actions import adjust, read_actions
from moneylab.data.providers import NewSessions, Provider, TiingoProvider, new_sessions
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
        if actions_path is None:
            return self.import_frame(bars, source=f"file:{path.name}", label=label)
        actions = read_actions(actions_path)
        return save_snapshot(
            adjust(bars, actions),
            self.settings.snapshot_dir,
            source=f"file:{path.name}+actions:{actions_path.name}",
            adjustment=ACTION_POLICY,
            label=label,
            provenance={"provider": "file", "file": path.name, "actions_file": actions_path.name},
            raw=bars,
            actions=actions,
        )

    def provider(self, name: str = "tiingo") -> Provider:
        """A live provider built from settings. Tiingo is the one implemented today."""
        if name == "tiingo":
            return TiingoProvider(self.settings.tiingo_api_key, cache_dir=self.settings.cache_dir)
        raise ValueError(f"unknown provider {name!r}; available: tiingo")

    def import_provider(
        self,
        symbols: Iterable[str],
        start: date,
        end: date,
        label: str = "",
        *,
        provider: Provider | None = None,
        refresh: bool = False,
    ) -> Snapshot:
        """Fetch raw history and actions from a provider, adjust them through the same
        path as the file adapter, and store an immutable snapshot with its provenance."""
        prov = provider or self.provider()
        hist = prov.fetch_history(symbols, start, end, refresh=refresh)
        return save_snapshot(
            hist.adjusted(),
            self.settings.snapshot_dir,
            source=prov.name,
            adjustment=ACTION_POLICY,
            label=label,
            provenance=hist.provenance,
            raw=hist.raw,
            actions=hist.actions,
        )

    def fetch_new_sessions(
        self,
        snapshot_id: str,
        symbols: Iterable[str] | None = None,
        *,
        end: date | None = None,
        provider: Provider | None = None,
    ) -> NewSessions:
        """Sessions the provider has that are newer than the snapshot, in the import
        format (see ``moneylab.data.providers.base.NewSessions``). Stores nothing."""
        snap = self.snapshot(snapshot_id)
        syms = sorted({s.upper() for s in (symbols or snap.meta["symbols"])})
        unknown = sorted(set(syms) - set(snap.meta["symbols"]))
        if unknown:
            raise ValueError(f"{unknown} not in snapshot {snapshot_id}; import them first")
        known = snap.bars[snap.bars["symbol"].isin(syms)]
        # One request per symbol from the earliest of their last sessions.
        after = known.groupby("symbol")["date"].max().min().date()
        prov = provider or self.provider()
        hist = prov.fetch_sessions(syms, after, end)
        return new_sessions(known, hist.raw, hist.actions)

    # Watchlists -------------------------------------------------------------------
    def save_watchlist(self, name: str, symbols: Iterable[str], label: str = "") -> dict[str, Any]:
        """Save a named, labelled watchlist. Changing it adds a version; old ones stay."""
        return self.store.save_watchlist(name, list(symbols), label)

    def watchlist(self, name: str, version: int | None = None) -> dict[str, Any]:
        return self.store.watchlist(name, version)

    def watchlists(self) -> list[dict[str, Any]]:
        return self.store.list_watchlists()

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
    def run(
        self,
        config: StrategyConfig,
        snapshot_id: str,
        parent_id: str | None = None,
        *,
        watchlist: str | None = None,
    ) -> str:
        """Resolve a user config with today's defaults, run it and save it.

        With ``watchlist`` the universe is that watchlist's current members, and the run
        records the watchlist (name, version, label, members) it used.
        """
        wl = None
        if watchlist is not None:
            wl = self.watchlist(watchlist)
            config = config.model_copy(update={"universe": list(wl["members"])})
        resolved = resolve(config, self.settings)
        if wl is not None:
            resolved["watchlist"] = {
                k: wl[k] for k in ("name", "version", "label", "members", "created_at")
            }
        return self.execute(resolved, snapshot_id, parent_id)

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
