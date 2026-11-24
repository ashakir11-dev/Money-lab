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
"""

JSON_COLUMNS = ("conditions", "values")


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
        run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = self.run_dir(run_id)
        d.mkdir(parents=True)
        dec = result.decisions.copy()
        for col in JSON_COLUMNS:
            if col in dec:
                dec[col] = dec[col].map(
                    lambda v: json.dumps(v) if isinstance(v, list | dict) else None
                )
        dec.to_parquet(d / "decisions.parquet", index=False)
        result.fills.to_parquet(d / "fills.parquet", index=False)
        result.equity.to_parquet(d / "equity.parquet", index=False)
        now = datetime.now(UTC).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    parent_id,
                    resolved["name"],
                    created_at or now,
                    now,
                    "completed",
                    snapshot_id,
                    resolved["execution"]["engine_version"],
                    config_hash,
                    json.dumps(resolved),
                    json.dumps(metrics),
                ),
            )
        return run_id

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

    def ledgers(self, run_id: str) -> RunResult:
        d = self.run_dir(run_id)
        dec = pd.read_parquet(d / "decisions.parquet")
        for col in JSON_COLUMNS:
            if col in dec:
                dec[col] = dec[col].map(lambda v: json.loads(v) if isinstance(v, str) else None)
        return RunResult(
            dec, pd.read_parquet(d / "fills.parquet"), pd.read_parquet(d / "equity.parquet")
        )
