"""Application service: the single entry point the dashboard, CLI and tests use."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from moneylab.data.actions import POLICY as ACTION_POLICY
from moneylab.data.actions import adjust, read_actions
from moneylab.data.providers import NewSessions, Provider, TiingoProvider, new_sessions
from moneylab.data.providers.base import empty_actions, empty_bars
from moneylab.data.snapshot import (
    DEFAULT_ADJUSTMENT,
    MarketData,
    Snapshot,
    load_snapshot,
    normalise,
    read_import_file,
    save_snapshot,
)
from moneylab.engine.batch import Job, SnapshotInfo, run_jobs, run_one
from moneylab.engine.config import StrategyConfig, check_versions, config_hash, resolve
from moneylab.engine.reporting import config_diff, first_difference
from moneylab.engine.simulator import RunResult
from moneylab.paper.desk import PaperDesk
from moneylab.settings import Settings, get_settings
from moneylab.store import RunStore


@dataclass
class Lab:
    settings: Settings
    store: RunStore
    _data_cache: dict[tuple[Any, ...], MarketData] = field(default_factory=dict, repr=False)
    _paper: PaperDesk | None = field(default=None, repr=False)

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
        resolved, result, m = run_one(resolved, data, _snap_info(snap))
        return self.store.save(
            resolved=resolved,
            snapshot_id=snap.id,
            config_hash=config_hash(resolved),
            result=result,
            metrics=m,
            parent_id=parent_id,
        )

    def run_batch(
        self,
        configs: list[StrategyConfig],
        snapshot_id: str,
        workers: int | None = None,
        parent_ids: list[str | None] | None = None,
        timings: dict[str, float] | None = None,
    ) -> list[str]:
        """Run several saved variants, each on its own account, and save every run.

        Market data is built once per date range (over the union of the variants'
        symbols) and shared read-only with the workers. ``workers`` defaults to
        ``settings.workers`` (0 = one per CPU); 1 runs sequentially in this process.
        Finished results are never reused. Returns run ids in ``configs`` order.
        ``timings``, when given, receives the seconds spent in each phase.
        """
        clock = time.perf_counter()
        phases = timings if timings is not None else {}

        def lap(name: str) -> None:
            nonlocal clock
            now = time.perf_counter()
            phases[name] = now - clock
            clock = now

        if parent_ids is not None and len(parent_ids) != len(configs):
            raise ValueError("parent_ids must have one entry per config")
        resolved = [resolve(c, self.settings) for c in configs]
        lap("resolve")
        snap = self.snapshot(snapshot_id)
        groups: dict[tuple[str | None, str | None], set[str]] = {}
        for r in resolved:
            groups.setdefault((r["start"], r["end"]), set()).update(
                [*r["universe"], r["benchmark"]]
            )
        keys = {g: n for n, g in enumerate(groups)}
        datasets = {
            keys[g]: self.market_data(snap.id, sorted(syms), g[0], g[1])
            for g, syms in groups.items()
        }
        lap("load_data")
        jobs = [
            Job(r, keys[(r["start"], r["end"])], parent_ids[n] if parent_ids else None)
            for n, r in enumerate(resolved)
        ]
        records = run_jobs(
            jobs,
            datasets,
            _snap_info(snap),
            self.store.home / "runs",
            self.settings.workers if workers is None else workers,
        )
        lap("simulate_and_write_ledgers")
        self.store.insert_records(records)
        lap("save_records")
        return [r.run_id for r in records]

    def compare(self, run_a: str, run_b: str) -> dict[str, Any]:
        a, b = self.store.get(run_a), self.store.get(run_b)
        la, lb = self.store.ledgers(run_a), self.store.ledgers(run_b)
        return {
            "a": a,
            "b": b,
            "config_diff": config_diff(a["resolved"], b["resolved"]),
            "first_difference": first_difference(
                la.decisions,
                lb.decisions,
                la.fills,
                lb.fills,
                la.equity,
                lb.equity,
                a["resolved"]["universe"],
                b["resolved"]["universe"],
            ),
        }

    # Paper forward testing (docs/paper-trading.md) ------------------------------------
    @property
    def paper(self) -> PaperDesk:
        if self._paper is None:
            self._paper = PaperDesk(self.settings, self.store)
        return self._paper

    def start_paper(self, run_id: str, start_date: str | date | None = None, name: str = "") -> str:
        """Freeze a completed run and open a paper account for sessions after the start."""
        return self.paper.start(run_id, start_date, name)

    def paper_ingest(
        self, paper_id: str, session_frame: pd.DataFrame, received_at: str | None = None
    ) -> dict[str, Any]:
        """Record one delivered session (import-format bars) and process what is ready."""
        return self.paper.ingest(paper_id, session_frame, received_at).as_dict()

    def paper_ingest_sessions(
        self, paper_id: str, frame: pd.DataFrame, received_at: str | None = None
    ) -> list[dict[str, Any]]:
        """Ingest a multi-session frame one session at a time, in date order."""
        return [r.as_dict() for r in self.paper.ingest_sessions(paper_id, frame, received_at)]

    def paper_status(self, paper_id: str, as_of: str | date | None = None) -> dict[str, Any]:
        return self.paper.status(paper_id, as_of)

    def paper_replay(self, paper_id: str) -> dict[str, Any]:
        return self.paper.replay(paper_id)

    def paper_ledgers(self, paper_id: str) -> RunResult:
        return self.paper.ledgers(paper_id)

    def paper_accounts(self) -> list[dict[str, Any]]:
        return self.paper.accounts()

    def paper_advance(
        self,
        paper_id: str,
        *,
        end: date | None = None,
        provider: Provider | None = None,
    ) -> dict[str, Any]:
        """Fetch completed sessions after the account's frontier and ingest them.

        Uses the same Tiingo path as ``fetch_new_sessions``. Corporate actions in the
        window refuse the advance: start a new run and paper account instead (see
        docs/paper-trading.md). Duplicate deliveries of already-accepted sessions are
        recorded as duplicates and do not reprocess.
        """
        acct = self.paper.store.account(paper_id)
        st = self.paper_status(paper_id)
        after = date.fromisoformat(st["latest_processed"] or acct["start_session"])
        prov = provider or self.provider()
        hist = prov.fetch_sessions(acct["symbols"], after, end)
        actions = hist.actions if not hist.actions.empty else empty_actions()
        if not actions.empty:
            raise ValueError(
                "corporate actions in the advance window; import a fresh snapshot, "
                "save a new run, and start a new paper account "
                "(see docs/paper-trading.md)"
            )
        bars = normalise(hist.raw) if not hist.raw.empty else empty_bars()
        if bars.empty:
            return {
                "paper_id": paper_id,
                "after": after.isoformat(),
                "sessions": [],
                "ingested": [],
                "status": self.paper_status(paper_id),
            }
        received_at = datetime.now(UTC).isoformat()
        ingested = self.paper_ingest_sessions(paper_id, bars, received_at=received_at)
        sessions = sorted({r["session"] for r in ingested if r.get("session")})
        return {
            "paper_id": paper_id,
            "after": after.isoformat(),
            "sessions": sessions,
            "ingested": ingested,
            "status": self.paper_status(paper_id),
        }


def _snap_info(snap: Snapshot) -> SnapshotInfo:
    return SnapshotInfo(snap.id, snap.meta["source"], snap.meta["adjustment"])
