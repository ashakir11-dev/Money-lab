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
from moneylab.data.bars import resample_daily, session_label
from moneylab.data.providers import (
    EquiblesProvider,
    NewSessions,
    Provider,
    TiingoProvider,
    new_sessions,
)
from moneylab.data.providers.base import empty_actions, empty_bars
from moneylab.data.snapshot import (
    DEFAULT_ADJUSTMENT,
    MarketData,
    Snapshot,
    load_snapshot,
    normalise,
    read_import_file,
    save_intraday_snapshot,
    save_snapshot,
)
from moneylab.engine.batch import Job, SnapshotInfo, run_jobs, run_one
from moneylab.engine.config import StrategyConfig, check_versions, config_hash, resolve
from moneylab.engine.reporting import config_diff, first_difference
from moneylab.engine.simulator import RunResult
from moneylab.lineage import next_version_name
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

    def provider(self, name: str = "equibles") -> Provider:
        """A live provider built from settings: Equibles (any bar size) or Tiingo (daily)."""
        if name == "equibles":
            return EquiblesProvider(
                self.settings.equibles_api_key, cache_dir=self.settings.cache_dir
            )
        if name == "tiingo":
            return TiingoProvider(self.settings.tiingo_api_key, cache_dir=self.settings.cache_dir)
        raise ValueError(f"unknown provider {name!r}; available: equibles, tiingo")

    def import_provider(
        self,
        symbols: Iterable[str],
        start: date,
        end: date,
        label: str = "",
        *,
        provider: Provider | None = None,
        refresh: bool = False,
        watchlist: str | None = None,
        timeframe: str | None = None,
        source: str = "equibles",
    ) -> Snapshot:
        """Fetch raw bars and corporate actions, adjust, and store a snapshot.

        Equibles: ``1d`` is resampled from the hourly bars; any other timeframe is
        stored as those bars; dividends are not requested. An empty label leaves the
        display name to be read from the source, timeframe, universe and dates.
        Tiingo: daily bars only, with splits and dividends.
        """
        syms, wl = self._fetch_symbols(symbols, watchlist)
        prov = provider or self.provider(source)
        if prov.name == "tiingo":
            return self._import_tiingo(
                prov, syms, wl, start, end, label, refresh, timeframe or "1d"
            )
        timeframe = timeframe or "1h"
        hist = prov.fetch_history(syms, start, end, refresh=refresh, timeframe=timeframe)  # type: ignore[call-arg]
        if hist.raw.empty:
            raise ValueError("the provider returned no bars for this request")
        adjusted = adjust(hist.raw, hist.actions)
        if wl is not None:
            hist.provenance["watchlist"] = {
                "name": wl["name"],
                "version": wl["version"],
                "label": wl["label"],
                "created_at": wl["created_at"],
            }
        adjustment = "split_adjusted_from_raw"
        if timeframe == "1d":
            return save_snapshot(
                resample_daily(adjusted),
                self.settings.snapshot_dir,
                source=prov.name,
                adjustment=adjustment,
                label=label,
                provenance=hist.provenance,
                raw=hist.raw,
                actions=hist.actions,
                timeframe="1d",
            )
        return save_intraday_snapshot(
            adjusted,
            self.settings.snapshot_dir,
            source=prov.name,
            timeframe=timeframe,
            adjustment=adjustment,
            label=label,
            provenance=hist.provenance,
            raw=hist.raw,
            actions=hist.actions,
        )

    def _import_tiingo(
        self,
        prov: Provider,
        syms: list[str],
        wl: dict[str, Any] | None,
        start: date,
        end: date,
        label: str,
        refresh: bool,
        timeframe: str,
    ) -> Snapshot:
        if timeframe != "1d":
            raise ValueError(
                f"Tiingo supplies daily bars only (timeframe 1d), not {timeframe}; "
                "use Equibles for intraday bars"
            )
        hist = prov.fetch_history(syms, start, end, refresh=refresh)
        if wl is not None:
            hist.provenance["watchlist"] = {
                "name": wl["name"],
                "version": wl["version"],
                "label": wl["label"],
                "created_at": wl["created_at"],
            }
        return save_snapshot(
            hist.adjusted(),
            self.settings.snapshot_dir,
            source=prov.name,
            adjustment=ACTION_POLICY,
            label=label,
            provenance=hist.provenance,
            raw=hist.raw,
            actions=hist.actions,
            timeframe="1d",
        )

    def with_snapshot_timeframe(self, config: StrategyConfig, snapshot_id: str) -> StrategyConfig:
        """A config that names no bar size takes its snapshot's (1d for older snapshots)."""
        if config.timeframe is not None:
            return config
        tf = self.snapshot(snapshot_id).meta.get("timeframe") or "1d"
        return config.model_copy(update={"timeframe": tf})

    def _provider_for(self, snapshot_id: str) -> Provider:
        """The provider a snapshot came from: Tiingo for Tiingo snapshots, else Equibles."""
        source = str(self.snapshot(snapshot_id).meta.get("source", ""))
        return self.provider("tiingo" if source == "tiingo" else "equibles")

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
        prov = provider or self._provider_for(snapshot_id)
        hist = prov.fetch_sessions(syms, after, end)
        return new_sessions(known, hist.raw, hist.actions)

    def _fetch_symbols(
        self, symbols: Iterable[str], watchlist: str | None
    ) -> tuple[list[str], dict[str, Any] | None]:
        """Watchlist members first, then any extra symbols. Empty means the caller
        named neither."""
        wl = self.watchlist(watchlist.strip()) if watchlist and watchlist.strip() else None
        out: list[str] = []
        seen: set[str] = set()
        for raw in [*(wl["members"] if wl else []), *symbols]:
            sym = str(raw).strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
        if not out:
            raise ValueError("name at least one symbol or a watchlist")
        return out, wl

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
        config = self.with_snapshot_timeframe(config, snapshot_id)
        resolved = resolve(config, self.settings)
        if wl is not None:
            resolved["watchlist"] = {
                k: wl[k] for k in ("name", "version", "label", "members", "created_at")
            }
        return self.execute(resolved, snapshot_id, parent_id)

    def duplicate(self, run_id: str, name: str | None = None) -> str:
        """Re-run a saved run exactly as resolved: same settings, data and versions.

        Current defaults are not consulted. Refuses if a component or the engine has a
        different version than the one the original run used. The new run is named with
        the next number in the original's family unless ``name`` is given.
        """
        orig = self.store.get(run_id)
        resolved = {k: v for k, v in orig["resolved"].items() if k != "data"}
        check_versions(resolved)
        resolved["name"] = name or next_version_name(self.store.list_runs(), run_id)
        return self.execute(resolved, orig["snapshot_id"], parent_id=run_id)

    def market_data(
        self, snapshot_id: str, symbols: list[str], start: str | None, end: str | None
    ) -> MarketData:
        """Aligned bars, built once per (snapshot, symbols, dates) and shared read-only."""
        key = (snapshot_id, tuple(sorted(set(symbols))), start, end)
        if key not in self._data_cache:
            snap = self.snapshot(snapshot_id)
            tf = snap.meta.get("timeframe", "1d")
            self._data_cache[key] = MarketData.build(
                snap.bars, list(key[1]), start, end, timeframe=tf
            )
        return self._data_cache[key]

    def execute(
        self, resolved: dict[str, Any], snapshot_id: str, parent_id: str | None = None
    ) -> str:
        snap = self.snapshot(snapshot_id)
        _require_timeframe(resolved, snap)
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
        resolved = [
            resolve(self.with_snapshot_timeframe(c, snapshot_id), self.settings) for c in configs
        ]
        lap("resolve")
        snap = self.snapshot(snapshot_id)
        for r in resolved:
            _require_timeframe(r, snap)
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

        Daily accounts get one event per session date, resampled from hourly bars
        when the provider returned those bars. Other accounts get one event per
        grid slot, including an empty slot where nothing traded. A slot that is
        not yet final is absent from the provider result, and nothing after the
        last final bar is delivered. A split in the window refuses the advance.
        """
        acct = self.paper.store.account(paper_id)
        tf = acct["resolved"].get("timeframe") or "1d"
        st = self.paper_status(paper_id)
        frontier = st["latest_processed"] or acct["start_session"]
        after = date.fromisoformat(str(frontier)[:10])
        prov = provider or self._provider_for(acct["snapshot_id"])
        hist = _fetch_sessions(prov, acct["symbols"], after, end, tf)
        actions = hist.actions if not hist.actions.empty else empty_actions()
        if not actions.empty:
            raise ValueError(
                "corporate actions in the advance window; import a fresh snapshot, "
                "save a new run, and start a new paper account "
                "(see docs/paper-trading.md)"
            )
        received_at = datetime.now(UTC).isoformat()
        bars = _bars_after(hist.raw, frontier, tf)
        if bars.empty:
            return {
                "paper_id": paper_id,
                "after": frontier,
                "sessions": [],
                "ingested": [],
                "status": self.paper_status(paper_id),
            }
        if tf == "1d":
            ingested = self.paper_ingest_sessions(paper_id, bars, received_at=received_at)
        else:
            ingested = _ingest_grid(self, paper_id, bars, frontier, tf, received_at)
        sessions = sorted({r["session"] for r in ingested if r.get("session")})
        return {
            "paper_id": paper_id,
            "after": frontier,
            "sessions": sessions,
            "ingested": ingested,
            "status": self.paper_status(paper_id),
        }


def _require_timeframe(resolved: dict[str, Any], snap: Snapshot) -> None:
    run_tf = resolved.get("timeframe", "1d")
    snap_tf = snap.meta.get("timeframe", "1d")
    if run_tf != snap_tf:
        raise ValueError(
            f"strategy timeframe {run_tf} does not match snapshot {snap.id} ({snap_tf})"
        )


def _snap_info(snap: Snapshot) -> SnapshotInfo:
    return SnapshotInfo(snap.id, snap.meta["source"], snap.meta["adjustment"])


def _fetch_sessions(
    prov: Provider, symbols: list[str], after: date, end: date | None, timeframe: str
) -> Any:
    try:
        return prov.fetch_sessions(symbols, after, end, timeframe=timeframe)  # type: ignore[call-arg]
    except TypeError:
        return prov.fetch_sessions(symbols, after, end)


def _bars_after(raw: pd.DataFrame, frontier: str, timeframe: str) -> pd.DataFrame:
    """Bars strictly after the frontier. Hourly rows become a daily resample when asked."""
    if raw is None or raw.empty:
        return empty_bars()
    if "bar_start" not in raw.columns:
        bars = normalise(raw)
        cutoff = date.fromisoformat(str(frontier)[:10])
        kept: pd.DataFrame = bars[bars["date"].dt.date > cutoff].reset_index(drop=True)
        return kept
    adjusted = adjust(raw, empty_actions())
    labels = pd.to_datetime(adjusted["bar_start"]).map(session_label)
    adjusted = adjusted.loc[labels > frontier].reset_index(drop=True)
    if adjusted.empty or timeframe != "1d":
        return adjusted
    daily = normalise(resample_daily(adjusted))
    cutoff = date.fromisoformat(str(frontier)[:10])
    after_daily: pd.DataFrame = daily[daily["date"].dt.date > cutoff].reset_index(drop=True)
    return after_daily


def _ingest_grid(
    lab: Lab,
    paper_id: str,
    bars: pd.DataFrame,
    frontier: str,
    timeframe: str,
    received_at: str,
) -> list[dict[str, Any]]:
    from moneylab.paper.desk import next_session

    labels = pd.to_datetime(bars["bar_start"]).map(session_label)
    horizon = labels.max()
    ingested: list[dict[str, Any]] = []
    cur = frontier
    while True:
        nxt = next_session(cur, timeframe)
        if nxt > horizon:
            break
        chunk = bars.loc[labels == nxt]
        if chunk.empty:
            result = lab.paper.ingest(paper_id, bars.iloc[0:0].copy(), received_at, session=nxt)
        else:
            result = lab.paper.ingest(paper_id, chunk, received_at)
        ingested.append(result.as_dict())
        if result.status == "rejected":
            break
        cur = nxt
    return ingested
