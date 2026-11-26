"""Run history: SQLite for run records, Parquet for each run's ledgers.

A run record holds the resolved settings (defaults included), component and engine
versions, the data snapshot id, the universe and timestamps. Runs are never edited;
duplicating creates a new run linked to its parent.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from moneylab.engine.simulator import RunResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES runs(id),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    resolved_json TEXT NOT NULL,
    metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS watchlists (
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    label TEXT NOT NULL,
    members_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (name, version)
);
"""

JSON_COLUMNS = ("conditions", "values")


@dataclass(frozen=True)
class RunRecord:
    """Everything the ``runs`` table holds for one finished run."""

    run_id: str
    resolved: dict[str, Any]
    snapshot_id: str
    config_hash: str
    metrics: dict[str, Any]
    parent_id: str | None = None
    created_at: str | None = None


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def write_ledgers(run_dir: Path, result: RunResult) -> None:
    """Write a run's decisions, fills and equity as Parquet into a new directory."""
    run_dir.mkdir(parents=True)
    dec = result.decisions.copy()
    for col in JSON_COLUMNS:
        if col in dec:
            dec[col] = dec[col].map(lambda v: json.dumps(v) if isinstance(v, list | dict) else None)
    dec.to_parquet(run_dir / "decisions.parquet", index=False)
    result.fills.to_parquet(run_dir / "fills.parquet", index=False)
    result.equity.to_parquet(run_dir / "equity.parquet", index=False)
    trades = result.meta.get("trades")
    if trades is not None:
        trades.to_parquet(run_dir / "trades.parquet", index=False)


class RunStore:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = home / "moneylab.sqlite"
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def run_dir(self, run_id: str) -> Path:
        return self.home / "runs" / run_id

    def save(
        self,
        *,
        resolved: dict[str, Any],
        snapshot_id: str,
        config_hash: str,
        result: RunResult,
        metrics: dict[str, Any],
        parent_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        run_id = new_run_id()
        write_ledgers(self.run_dir(run_id), result)
        self.insert_records(
            [
                RunRecord(
                    run_id=run_id,
                    resolved=resolved,
                    snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    metrics=metrics,
                    parent_id=parent_id,
                    created_at=created_at,
                )
            ]
        )
        return run_id

    def insert_records(self, records: list[RunRecord]) -> None:
        """Insert run records whose ledgers are already written, in one transaction.

        Only this process writes SQLite. Batch workers write their own run's Parquet
        ledgers (a fresh directory per run) and hand the record back here.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as c:
            c.executemany(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        r.run_id,
                        r.parent_id,
                        r.resolved["name"],
                        r.created_at or now,
                        now,
                        "completed",
                        r.snapshot_id,
                        r.resolved["execution"]["engine_version"],
                        r.config_hash,
                        json.dumps(r.resolved),
                        json.dumps(r.metrics),
                    )
                    for r in records
                ],
            )

    # Watchlists: named, labelled, versioned. A version is never edited; saving
    # different members or a different label under the same name adds a version.
    def save_watchlist(self, name: str, members: list[str], label: str = "") -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("a watchlist needs a name")
        syms = sorted({m.strip().upper() for m in members if m.strip()})
        if not syms:
            raise ValueError("a watchlist needs at least one symbol")
        latest = self.watchlist(name) if self._has_watchlist(name) else None
        if latest and latest["members"] == syms and latest["label"] == label:
            return latest
        version = (latest["version"] + 1) if latest else 1
        with self._conn() as c:
            c.execute(
                "INSERT INTO watchlists VALUES (?,?,?,?,?)",
                (name, version, label, json.dumps(syms), datetime.now(UTC).isoformat()),
            )
        return self.watchlist(name, version)

    def _has_watchlist(self, name: str) -> bool:
        with self._conn() as c:
            r = c.execute("SELECT 1 FROM watchlists WHERE name = ?", (name,)).fetchone()
        return r is not None

    def watchlist(self, name: str, version: int | None = None) -> dict[str, Any]:
        """A watchlist version (the latest by default): name, version, label, members,
        created_at."""
        q = "SELECT * FROM watchlists WHERE name = ?"
        args: tuple[Any, ...] = (name,)
        if version is not None:
            q += " AND version = ?"
            args += (version,)
        with self._conn() as c:
            r = c.execute(q + " ORDER BY version DESC LIMIT 1", args).fetchone()
        if r is None:
            raise KeyError(f"no watchlist {name!r}" + (f" version {version}" if version else ""))
        out = dict(r)
        out["members"] = json.loads(out.pop("members_json"))
        return out

    def list_watchlists(self) -> list[dict[str, Any]]:
        """The latest version of every watchlist, by name."""
        with self._conn() as c:
            names = [r[0] for r in c.execute("SELECT DISTINCT name FROM watchlists ORDER BY name")]
        return [self.watchlist(n) for n in names]

    def list_runs(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, parent_id, name, created_at, status, snapshot_id, config_hash "
                "FROM runs ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, run_id: str) -> dict[str, Any]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            raise KeyError(run_id)
        out = dict(r)
        out["resolved"] = json.loads(out.pop("resolved_json"))
        out["metrics"] = json.loads(out.pop("metrics_json") or "{}")
        return out

    def update_metrics(self, run_id: str, metrics: dict[str, Any]) -> None:
        """Replace a run's stored metrics (metrics are derived from the ledgers, so they
        can be recomputed; the run's settings and ledgers are never touched)."""
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET metrics_json = ? WHERE id = ?", (json.dumps(metrics), run_id)
            )

    def write_trades(self, run_id: str, trades: pd.DataFrame) -> None:
        trades.to_parquet(self.run_dir(run_id) / "trades.parquet", index=False)

    def trades(self, run_id: str) -> pd.DataFrame | None:
        """The run's closed trades (see ``engine.analytics``), or None if not computed."""
        path = self.run_dir(run_id) / "trades.parquet"
        return pd.read_parquet(path) if path.is_file() else None

    def ledgers(self, run_id: str) -> RunResult:
        d = self.run_dir(run_id)
        dec = pd.read_parquet(d / "decisions.parquet")
        for col in JSON_COLUMNS:
            if col in dec:
                dec[col] = dec[col].map(lambda v: json.loads(v) if isinstance(v, str) else None)
        return RunResult(
            dec, pd.read_parquet(d / "fills.parquet"), pd.read_parquet(d / "equity.parquet")
        )
