"""Run many variants over the same market data, sequentially or on a worker pool.

Immutable inputs are shared, mutable state is isolated:

* The parent builds each ``MarketData`` once. With the ``fork`` start method (Linux) it
  is published in a module-level global before the pool starts, so workers inherit it
  copy-on-write and nothing is pickled. Where ``fork`` is unavailable, each worker
  receives one pickled copy through the pool initializer.
* Every variant gets its own ``Account`` (cash, positions, pending orders, ledgers)
  inside ``simulate``. Within that account all of its symbols compete for its cash;
  nothing is shared between accounts.
* Workers simulate, compute metrics and write the run's Parquet ledgers into a fresh
  run directory. They return a small ``RunRecord``; only the parent writes SQLite.

Finished results are never reused: every variant in every batch is simulated.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moneylab.data.snapshot import MarketData
from moneylab.engine.config import config_hash
from moneylab.engine.reporting import metrics
from moneylab.engine.simulator import RunResult, simulate
from moneylab.store import RunRecord, new_run_id, write_ledgers


@dataclass(frozen=True)
class SnapshotInfo:
    id: str
    source: str
    adjustment: str


@dataclass(frozen=True)
class Job:
    """One variant to run: its resolved settings and which shared dataset it uses."""

    resolved: dict[str, Any]
    data_key: int
    parent_id: str | None = None


def run_one(
    resolved: dict[str, Any], data: MarketData, snap: SnapshotInfo
) -> tuple[dict[str, Any], RunResult, dict[str, Any]]:
    """Simulate one variant on its own account. Returns (resolved + data block, result,
    metrics). Used by ``Lab.execute`` and by batch workers alike."""
    result = simulate(resolved, data)
    m = metrics(result.equity, result.fills)
    if "benchmark_equity" in result.equity:
        m["benchmark"] = metrics(result.equity, result.fills, column="benchmark_equity")
    out = dict(resolved)
    out["data"] = {
        "snapshot_id": snap.id,
        "source": snap.source,
        "adjustment": snap.adjustment,
        "first_session": data.sessions[0].date().isoformat(),
        "last_session": data.sessions[-1].date().isoformat(),
    }
    return out, result, m


# Read-only inputs for the current batch, set by the parent (fork) or the initializer.
_SHARED: dict[int, MarketData] = {}
_SNAP: SnapshotInfo | None = None
_RUNS_ROOT: Path | None = None


def _init_worker(shared: dict[int, MarketData] | None, snap: SnapshotInfo, runs_root: Path) -> None:
    global _SNAP, _RUNS_ROOT
    if shared is not None:  # not forked: this worker gets its own copy once
        _SHARED.clear()
        _SHARED.update(shared)
    _SNAP, _RUNS_ROOT = snap, runs_root


def _execute_job(job: Job) -> RunRecord:
    assert _SNAP is not None and _RUNS_ROOT is not None
    started = datetime.now(UTC).isoformat()
    resolved, result, m = run_one(job.resolved, _SHARED[job.data_key], _SNAP)
    run_id = new_run_id()
    write_ledgers(_RUNS_ROOT / run_id, result)
    return RunRecord(
        run_id=run_id,
        resolved=resolved,
        snapshot_id=_SNAP.id,
        config_hash=config_hash(resolved),
        metrics=m,
        parent_id=job.parent_id,
        created_at=started,
    )


def resolve_workers(requested: int, jobs: int) -> int:
    """0 means one worker per CPU. Never more workers than jobs."""
    n = requested if requested > 0 else (os.cpu_count() or 1)
    return max(1, min(n, jobs))


def run_jobs(
    jobs: list[Job],
    datasets: dict[int, MarketData],
    snap: SnapshotInfo,
    runs_root: Path,
    workers: int,
) -> list[RunRecord]:
    """Run every job and return their records in job order (ledgers already written).

    ``workers == 1`` runs in this process, one variant after another.
    """
    if not jobs:
        return []
    workers = resolve_workers(workers, len(jobs))
    _SHARED.clear()
    _SHARED.update(datasets)
    try:
        if workers == 1:
            _init_worker(None, snap, runs_root)
            return [_execute_job(j) for j in jobs]
        forking = "fork" in mp.get_all_start_methods()
        ctx = mp.get_context("fork" if forking else "spawn")
        init_args = (None if forking else datasets, snap, runs_root)
        with warnings.catch_warnings():
            # Python 3.12 warns that fork() in a process with threads may deadlock. The
            # children only read the inherited arrays and write their own files.
            warnings.filterwarnings("ignore", message=".*fork.*", category=DeprecationWarning)
            with ProcessPoolExecutor(
                max_workers=workers, mp_context=ctx, initializer=_init_worker, initargs=init_args
            ) as pool:
                return list(pool.map(_execute_job, jobs))
    finally:
        _SHARED.clear()
