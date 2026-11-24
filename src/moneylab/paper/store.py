"""SQLite persistence for paper accounts (``<home>/paper.sqlite``).

Everything a paper account needs to resume after a restart, and to be replayed, is
here: the frozen account record, every delivered event (whatever its outcome), and,
per processed session, the input it saw, the account state after it, and the new
decision, fill and equity rows.

Idempotency is enforced by the schema as well as by the code:

* at most one ``accepted`` event per (account, session) (partial unique index);
* a processed session, a decision, a fill and an equity row each have a primary key,
  and a session's rows are written in one ``BEGIN IMMEDIATE`` transaction together with
  its state, so a crash leaves either all of them or none.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    history_snapshot_id TEXT NOT NULL,
    first_session TEXT NOT NULL,
    start_session TEXT NOT NULL,
    symbols_json TEXT NOT NULL,
    resolved_json TEXT NOT NULL,
    initial_state_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL REFERENCES paper_accounts(id),
    event_id TEXT NOT NULL,
    session TEXT,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    received_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_events_one_accepted
    ON paper_events(paper_id, session) WHERE status = 'accepted';
CREATE TABLE IF NOT EXISTS paper_sessions (
    paper_id TEXT NOT NULL REFERENCES paper_accounts(id),
    session TEXT NOT NULL,
    idx INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    input_content_id TEXT NOT NULL,
    input_json TEXT NOT NULL,
    state_json TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, session)
);
CREATE TABLE IF NOT EXISTS paper_decisions (
    paper_id TEXT NOT NULL,
    decision_id INTEGER NOT NULL,
    session TEXT NOT NULL,
    row_json TEXT NOT NULL,
    PRIMARY KEY (paper_id, decision_id)
);
CREATE TABLE IF NOT EXISTS paper_fills (
    paper_id TEXT NOT NULL,
    fill_no INTEGER NOT NULL,
    session TEXT NOT NULL,
    row_json TEXT NOT NULL,
    PRIMARY KEY (paper_id, fill_no)
);
CREATE TABLE IF NOT EXISTS paper_equity (
    paper_id TEXT NOT NULL,
    session TEXT NOT NULL,
    row_json TEXT NOT NULL,
    PRIMARY KEY (paper_id, session)
);
"""

LEDGERS = {
    "decisions": ("paper_decisions", "decision_id"),
    "fills": ("paper_fills", "fill_no"),
    "equity": ("paper_equity", "session"),
}


class AlreadyProcessed(RuntimeError):
    """The session was processed by an earlier call or another process."""


class PaperStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction that holds the database lock from its first statement."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    # Accounts -----------------------------------------------------------------------
    def create_account(self, rec: dict[str, Any]) -> None:
        with self.transaction() as c:
            c.execute(
                "INSERT INTO paper_accounts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec["id"],
                    rec["name"],
                    rec["run_id"],
                    rec["snapshot_id"],
                    rec["history_snapshot_id"],
                    rec["first_session"],
                    rec["start_session"],
                    json.dumps(rec["symbols"]),
                    json.dumps(rec["resolved"]),
                    json.dumps(rec["initial_state"]),
                    rec["created_at"],
                ),
            )

    def account(self, paper_id: str) -> dict[str, Any]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM paper_accounts WHERE id = ?", (paper_id,)).fetchone()
        if r is None:
            raise KeyError(paper_id)
        out = dict(r)
        out["symbols"] = json.loads(out.pop("symbols_json"))
        out["resolved"] = json.loads(out.pop("resolved_json"))
        out["initial_state"] = json.loads(out.pop("initial_state_json"))
        return out

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT a.id, a.name, a.run_id, a.start_session, a.created_at, "
                "(SELECT MAX(session) FROM paper_sessions s WHERE s.paper_id = a.id) "
                "AS latest_session, "
                "(SELECT COUNT(*) FROM paper_sessions s WHERE s.paper_id = a.id) "
                "AS processed_sessions "
                "FROM paper_accounts a ORDER BY a.created_at DESC, a.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # Events -------------------------------------------------------------------------
    @staticmethod
    def accepted_event_in(c: sqlite3.Connection, paper_id: str, session: str) -> Any:
        return c.execute(
            "SELECT * FROM paper_events WHERE paper_id = ? AND session = ? AND status = 'accepted'",
            (paper_id, session),
        ).fetchone()

    @staticmethod
    def insert_event(c: sqlite3.Connection, paper_id: str, ev: dict[str, Any]) -> None:
        c.execute(
            "INSERT INTO paper_events (paper_id, event_id, session, status, detail, "
            "received_at, recorded_at, content) VALUES (?,?,?,?,?,?,?,?)",
            (
                paper_id,
                ev["event_id"],
                ev["session"],
                ev["status"],
                ev["detail"],
                ev["received_at"],
                ev["recorded_at"],
                ev["content"],
            ),
        )

    def events(self, paper_id: str, status: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM paper_events WHERE paper_id = ?"
        args: tuple[Any, ...] = (paper_id,)
        if status is not None:
            q += " AND status = ?"
            args += (status,)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY seq", args).fetchall()]

    def accepted_events(self, paper_id: str) -> dict[str, dict[str, Any]]:
        """session -> accepted event, in session order."""
        evs = self.events(paper_id, "accepted")
        return {e["session"]: e for e in sorted(evs, key=lambda e: e["session"])}

    # Processed sessions -----------------------------------------------------------
    def sessions(self, paper_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM paper_sessions WHERE paper_id = ? ORDER BY session", (paper_id,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["input"] = json.loads(d.pop("input_json"))
            d["state"] = json.loads(d.pop("state_json"))
            out.append(d)
        return out

    def last_session(self, paper_id: str) -> dict[str, Any] | None:
        s = self.sessions(paper_id)
        return s[-1] if s else None

    def ledger_rows(self, paper_id: str, ledger: str) -> list[dict[str, Any]]:
        table, key = LEDGERS[ledger]
        with self._conn() as c:
            rows = c.execute(
                f"SELECT row_json FROM {table} WHERE paper_id = ? ORDER BY {key}", (paper_id,)
            ).fetchall()
        return [json.loads(r["row_json"]) for r in rows]

    def commit_session(
        self,
        paper_id: str,
        *,
        session: str,
        expected_previous: str | None,
        row: dict[str, Any],
        decisions: list[dict[str, Any]],
        fills: list[tuple[int, dict[str, Any]]],
        equity: list[dict[str, Any]],
    ) -> None:
        """Write one processed session atomically.

        Raises ``AlreadyProcessed`` (and writes nothing) if the session is already
        recorded or another process advanced the account since it was read.
        """
        with self.transaction() as c:
            cur = c.execute(
                "SELECT MAX(session) AS s FROM paper_sessions WHERE paper_id = ?", (paper_id,)
            ).fetchone()["s"]
            if cur != expected_previous:
                raise AlreadyProcessed(
                    f"{paper_id}: last processed is {cur}, not {expected_previous}"
                )
            c.execute(
                "INSERT INTO paper_sessions VALUES (?,?,?,?,?,?,?,?)",
                (
                    paper_id,
                    session,
                    row["idx"],
                    row["event_id"],
                    row["input_content_id"],
                    json.dumps(row["input"]),
                    json.dumps(row["state"]),
                    row["processed_at"],
                ),
            )
            c.executemany(
                "INSERT INTO paper_decisions VALUES (?,?,?,?)",
                [(paper_id, d["decision_id"], d["session"], _dumps(d)) for d in decisions],
            )
            c.executemany(
                "INSERT INTO paper_fills VALUES (?,?,?,?)",
                [(paper_id, n, f["session"], _dumps(f)) for n, f in fills],
            )
            c.executemany(
                "INSERT INTO paper_equity VALUES (?,?,?)",
                [(paper_id, e["session"], _dumps(e)) for e in equity],
            )


def _dumps(row: dict[str, Any]) -> str:
    return json.dumps(row)
