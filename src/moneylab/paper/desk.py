"""Paper forward testing: a frozen saved run, stepped one incoming session at a time.

Lifecycle (see docs/paper-trading.md):

1. ``start`` freezes a completed run: its resolved settings (component and engine
   versions are re-checked), its snapshot cut at the start session as the warm-up
   history, and a fresh ``Account`` with the run's starting cash that has "already
   processed" every history session.
2. ``ingest`` records a delivered session event (one exchange session of bars in the
   import format) and classifies it: ``accepted``, ``duplicate``, ``conflict`` or
   ``rejected``. Every event is stored, whatever its outcome.
3. ``process`` steps the account through every accepted session that directly follows
   the last processed one, using the historical engine's ``Account.step`` unchanged.
   A gap (missing session) stops processing until that session arrives.
4. ``replay`` rebuilds a fresh account from the same initial state and the recorded
   events and compares it with the live record row by row.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import pandas as pd

from moneylab.data.bars import (
    label_in_range,
    minutes_of,
    on_grid,
    regular_starts,
    session_label,
)
from moneylab.data.snapshot import (
    COLUMNS,
    INTRADAY_COLUMNS,
    MarketData,
    content_id,
    intraday_content_id,
    load_snapshot,
    normalise,
    normalise_intraday,
    save_intraday_snapshot,
    save_snapshot,
)
from moneylab.engine.config import VersionMismatch, check_versions
from moneylab.engine.reporting import first_difference
from moneylab.engine.simulator import Account, MarketView, RunResult
from moneylab.paper.store import AlreadyProcessed, PaperStore
from moneylab.settings import Settings
from moneylab.store import RunStore

ACCEPTED = "accepted"
DUPLICATE = "duplicate"
CONFLICT = "conflict"
REJECTED = "rejected"

_COUNTS = ("n_decisions", "n_fills", "n_equity")


@dataclass(frozen=True)
class IngestResult:
    paper_id: str
    event_id: str
    session: str | None
    status: str
    detail: str
    processed: list[str] = field(default_factory=list)
    blocked: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Calendar helpers --------------------------------------------------------------------
def _cal() -> Any:
    return xcals.get_calendar("XNYS")


def xnys_sessions(lo: str | pd.Timestamp, hi: str | pd.Timestamp) -> list[str]:
    lo, hi = pd.Timestamp(lo), pd.Timestamp(hi)
    if lo > hi:
        return []
    idx = pd.DatetimeIndex(_cal().sessions_in_range(lo, hi)).tz_localize(None)
    return [t.date().isoformat() for t in idx]


def account_timeframe(resolved: dict[str, Any]) -> str:
    return str(resolved.get("timeframe") or "1d")


def next_session(session: str, timeframe: str = "1d") -> str:
    """Next session id. Daily accounts step by NYSE date. Others step one grid slot."""
    if timeframe == "1d":
        t = pd.Timestamp(session[:10])
        return xnys_sessions(t + timedelta(days=1), t + timedelta(days=15))[0]
    minutes = minutes_of(timeframe)
    ts = datetime.fromisoformat(session)
    labels = [session_label(s) for s in regular_starts(ts.date(), minutes)]
    if session in labels:
        i = labels.index(session)
        if i + 1 < len(labels):
            return labels[i + 1]
    nxt = xnys_sessions(
        pd.Timestamp(ts.date()) + timedelta(days=1), pd.Timestamp(ts.date()) + timedelta(days=15)
    )[0]
    return session_label(regular_starts(date.fromisoformat(nxt), minutes)[0])


def is_bar_session(session: str, timeframe: str = "1d") -> bool:
    if timeframe == "1d":
        return is_session(session[:10])
    if "T" not in session:
        return False
    try:
        ts = datetime.fromisoformat(session)
    except ValueError:
        return False
    return on_grid(ts, minutes_of(timeframe)) and is_session(ts.date().isoformat())


def grid_labels(first: str, last: str, timeframe: str) -> list[str]:
    """Session ids from ``first`` through ``last`` inclusive, on this timeframe's grid."""
    if timeframe == "1d":
        return xnys_sessions(first[:10], last[:10])
    minutes = minutes_of(timeframe)
    out: list[str] = []
    for day in xnys_sessions(first[:10], last[:10]):
        for start in regular_starts(date.fromisoformat(day), minutes):
            label = session_label(start)
            if label_in_range(label, first, last):
                out.append(label)
    return out


def last_slot_on_or_before(d: str | date, timeframe: str) -> str:
    if timeframe == "1d":
        return last_session_on_or_before(d)
    sess = last_session_on_or_before(d)
    return session_label(regular_starts(date.fromisoformat(sess), minutes_of(timeframe))[-1])


def last_session_on_or_before(d: str | date) -> str:
    t = pd.Timestamp(d)
    return xnys_sessions(t - timedelta(days=15), t)[-1]


def is_session(session: str) -> bool:
    try:
        return bool(_cal().is_session(pd.Timestamp(session)))
    except Exception:  # outside the calendar's bounds
        return False


# Event and state helpers -------------------------------------------------------------
def canonical_csv(df: pd.DataFrame) -> str:
    """The text that ``content_id`` hashes; stored so an event can be rebuilt exactly."""
    return df.to_csv(index=False, float_format="%.6f", date_format="%Y-%m-%d")


def parse_event(content: str, timeframe: str = "1d") -> pd.DataFrame:
    if content.startswith("session,"):
        cols = COLUMNS if timeframe == "1d" else INTRADAY_COLUMNS
        return pd.DataFrame(columns=cols)
    frame = pd.read_csv(io.StringIO(content))
    if timeframe == "1d":
        return normalise(frame)
    return normalise_intraday(frame)


def _intraday_csv(df: pd.DataFrame) -> str:
    view = df[INTRADAY_COLUMNS].copy()
    view["bar_start"] = pd.to_datetime(view["bar_start"]).dt.strftime("%Y-%m-%dT%H:%M")
    view["date"] = pd.to_datetime(view["date"]).dt.strftime("%Y-%m-%d")
    return view.to_csv(index=False, float_format="%.6f")


def _prepare_intraday(
    frame: pd.DataFrame,
    symbols: list[str],
    timeframe: str,
    session: str | None,
    raw: str,
    raw_id: str,
) -> tuple[pd.DataFrame | None, str, str | None, str, str]:
    if frame.empty:
        if not session:
            return None, raw_id, None, "empty event", raw
        content = f"session,{session}\n"
        eid = hashlib.sha256(content.encode()).hexdigest()[:16]
        return pd.DataFrame(columns=INTRADAY_COLUMNS), eid, session, "", content
    if "bar_start" not in frame.columns:
        return None, raw_id, None, "missing column(s) ['bar_start']", raw
    try:
        df = normalise_intraday(frame)
    except (ValueError, TypeError) as e:
        return None, raw_id, None, f"invalid bars: {e}", raw
    starts = sorted({session_label(t) for t in pd.to_datetime(df["bar_start"])})
    if len(starts) != 1:
        return None, raw_id, None, f"an event holds one session, got {len(starts)}", raw
    slot = starts[0]
    if not is_bar_session(slot, timeframe):
        return None, raw_id, slot, f"{slot} is not on the {timeframe} grid", raw
    df = df[df["symbol"].isin(symbols)].reset_index(drop=True)
    content = _intraday_csv(df) if not df.empty else raw
    if df.empty:
        return None, raw_id, slot, "no bars for this account's symbols", content
    return df, intraday_content_id(df), slot, "", content


def _plain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """JSON round trip: exactly what the store gives back, so live and replay compare."""
    out: list[dict[str, Any]] = json.loads(json.dumps(rows))
    return out


def _compact_state(acc: Account) -> dict[str, Any]:
    """Account state without the ledgers (they are stored row by row), plus row counts."""
    st = acc.state()
    out = {k: st[k] for k in ("cash", "positions", "pending", "last_close", "last_index")}
    out.update(
        n_decisions=len(acc.decisions), n_fills=len(acc.fills), n_equity=len(acc.equity_rows)
    )
    if acc.idle_in_benchmark:  # kept out otherwise so existing accounts' state is unchanged
        out["sweep_qty"] = acc.sweep_qty
    plain: dict[str, Any] = json.loads(json.dumps(out))
    return plain


def _account_from(
    resolved: dict[str, Any],
    state: dict[str, Any],
    ledgers: dict[str, list[dict[str, Any]]],
) -> Account:
    compact = {k: v for k, v in state.items() if k not in _COUNTS}
    return Account.from_state(
        resolved,
        {
            **compact,
            "decisions": ledgers["decisions"][: state["n_decisions"]],
            "fills": ledgers["fills"][: state["n_fills"]],
            "equity_rows": ledgers["equity"][: state["n_equity"]],
        },
    )


def _frame_diff(old: pd.DataFrame, new: pd.DataFrame, limit: int = 6) -> str:
    """Short human description of how a conflicting delivery differs from the accepted one."""
    a: dict[str, dict[Any, Any]] = {str(r["symbol"]): r for r in old.to_dict("records")}
    b: dict[str, dict[Any, Any]] = {str(r["symbol"]): r for r in new.to_dict("records")}
    parts: list[str] = []
    for sym in sorted(set(a) | set(b)):
        if sym not in b:
            parts.append(f"{sym} missing in new delivery")
        elif sym not in a:
            parts.append(f"{sym} only in new delivery")
        else:
            for col in ("open", "high", "low", "close", "volume"):
                va, vb = float(a[sym][col]), float(b[sym][col])
                if va != vb:
                    parts.append(f"{sym} {col} {va:g} -> {vb:g}")
    more = f" (+{len(parts) - limit} more)" if len(parts) > limit else ""
    return "; ".join(parts[:limit]) + more


def _first_row_difference(
    name: str, live: list[dict[str, Any]], replay: list[dict[str, Any]]
) -> dict[str, Any] | None:
    a = [json.dumps(r, sort_keys=True) for r in live]
    b = [json.dumps(r, sort_keys=True) for r in replay]
    if a == b:
        return None
    i = next((k for k, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    return {
        "ledger": name,
        "index": i,
        "live": live[i] if i < len(live) else None,
        "replay": replay[i] if i < len(replay) else None,
    }


def ledger_frames(ledgers: dict[str, list[dict[str, Any]]]) -> RunResult:
    dec = pd.DataFrame(ledgers["decisions"])
    if dec.empty:
        dec = pd.DataFrame(columns=["decision_id", "session", "symbol", "action", "reason"])
    fl = pd.DataFrame(ledgers["fills"])
    if fl.empty:
        fl = pd.DataFrame(
            columns=["session", "symbol", "side", "qty", "price", "cost", "cash_after"]
        )
    eq = pd.DataFrame(ledgers["equity"])
    if eq.empty:
        eq = pd.DataFrame(columns=["session", "cash", "positions_value", "equity", "positions"])
    return RunResult(dec, fl, eq)


class PaperDesk:
    """All paper accounts under one ``MONEYLAB_HOME``."""

    def __init__(self, settings: Settings, runs: RunStore) -> None:
        self.settings = settings
        self.runs = runs
        self.store = PaperStore(settings.home / "paper.sqlite")
        self.history_dir: Path = settings.home / "paper" / "history"
        self._history: dict[str, pd.DataFrame] = {}
        self._events: dict[str, pd.DataFrame] = {}

    # Start ----------------------------------------------------------------------------
    def start(self, run_id: str, start_date: str | date | None = None, name: str = "") -> str:
        """Freeze a completed run and open a paper account after ``start_date``.

        The run's snapshot up to the start session (from the run's own first session,
        so indicators warm up exactly as in the historical run) becomes the account's
        history. Sessions after it are only taken from delivered events.
        """
        run = self.runs.get(run_id)
        if run["status"] != "completed":
            raise ValueError(f"run {run_id} is {run['status']}; only completed runs can be frozen")
        resolved = {k: v for k, v in run["resolved"].items() if k != "data"}
        check_versions(resolved)
        snap = load_snapshot(run["snapshot_id"], self.settings.snapshot_dir)
        symbols = sorted(set(resolved["universe"]) | {resolved["benchmark"]})
        tf = account_timeframe(resolved)
        first = run["resolved"]["data"]["first_session"]
        bars = snap.bars
        if tf == "1d":
            snap_last = bars["date"].max().date().isoformat()
            start = last_session_on_or_before(start_date or snap_last)
            if start > snap_last:
                raise ValueError(
                    f"start {start} is after the snapshot's last bar {snap_last}; "
                    "the history must cover the start session"
                )
            if start < first:
                raise ValueError(f"start {start} is before the run's first session {first}")
            hist = bars[
                bars["symbol"].isin(symbols)
                & (bars["date"] >= pd.Timestamp(first))
                & (bars["date"] <= pd.Timestamp(start))
            ]
            history = save_snapshot(
                hist,
                self.history_dir,
                source=f"paper-history:{snap.id}",
                adjustment=snap.meta["adjustment"],
                label=f"{snap.id} from {first} to {start}",
            )
        else:
            if "bar_start" not in bars.columns:
                raise ValueError(f"snapshot {snap.id} has no bar_start column for {tf}")
            last_bar = session_label(pd.to_datetime(bars["bar_start"]).max())
            if start_date is None:
                start = last_bar
            elif "T" in str(start_date):
                start = str(start_date)[:16]
            else:
                start = last_slot_on_or_before(start_date, tf)
            if start > last_bar:
                raise ValueError(
                    f"start {start} is after the snapshot's last bar {last_bar}; "
                    "the history must cover the start session"
                )
            if start < first:
                raise ValueError(f"start {start} is before the run's first session {first}")
            labels = pd.to_datetime(bars["bar_start"]).map(session_label)
            hist = bars[
                bars["symbol"].isin(symbols) & labels.map(lambda s: label_in_range(s, first, start))
            ].copy()
            history = save_intraday_snapshot(
                hist,
                self.history_dir,
                source=f"paper-history:{snap.id}",
                timeframe=tf,
                adjustment=snap.meta["adjustment"],
                label=f"{snap.id} from {first} to {start}",
            )
        data = MarketData.build(history.bars, symbols, first, start, timeframe=tf)
        acc = Account(resolved)
        acc.last_index = len(data.sessions) - 1  # history sessions count as processed
        paper_id = "paper-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.store.create_account(
            {
                "id": paper_id,
                "name": name or f"{resolved['name']} (paper from {start})",
                "run_id": run_id,
                "snapshot_id": snap.id,
                "history_snapshot_id": history.id,
                "first_session": first,
                "start_session": start,
                "symbols": symbols,
                "resolved": resolved,
                "initial_state": _compact_state(acc),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return paper_id

    def accounts(self) -> list[dict[str, Any]]:
        return self.store.list_accounts()

    # Ingest ---------------------------------------------------------------------------
    def ingest(
        self,
        paper_id: str,
        frame: pd.DataFrame,
        received_at: str | None = None,
        session: str | None = None,
    ) -> IngestResult:
        """Record one delivered session and process whatever is now ready.

        ``session`` names an empty grid slot. An empty hour is accepted so the
        account can advance through a slot where nothing traded.
        """
        acct = self.store.account(paper_id)
        tf = account_timeframe(acct["resolved"])
        now = datetime.now(UTC).isoformat()
        df, event_id, session, why, content = self._prepare(
            frame, acct["symbols"], timeframe=tf, session=session
        )
        if df is not None and session is not None and not why:
            if not is_bar_session(session, tf):
                why = f"{session} is not an XNYS session"
            elif session <= acct["start_session"]:
                why = f"{session} is part of the history (start {acct['start_session']})"
        ev = {
            "event_id": event_id,
            "session": session,
            "received_at": received_at or now,
            "recorded_at": now,
            "content": content,
        }
        with self.store.transaction() as c:
            if why or df is None or session is None:
                status, detail = REJECTED, why
            else:
                prev = self.store.accepted_event_in(c, paper_id, session)
                if prev is None:
                    status = ACCEPTED
                    absent = sorted(set(acct["resolved"]["universe"]) - set(df["symbol"]))
                    detail = "accepted" + (
                        f"; no bar for {', '.join(absent)} (missing-bar rule applies)"
                        if absent
                        else ""
                    )
                elif prev["event_id"] == event_id:
                    status, detail = DUPLICATE, f"same content as accepted event {event_id}"
                else:
                    status = CONFLICT
                    detail = f"differs from accepted event {prev['event_id']}: " + _frame_diff(
                        parse_event(prev["content"], tf), df
                    )
            self.store.insert_event(c, paper_id, {**ev, "status": status, "detail": detail})
        processed, blocked = self.process(paper_id)
        if status == ACCEPTED and session not in processed:
            done = {s["session"] for s in self.store.sessions(paper_id)}
            if session not in done:
                detail += "; waiting for an earlier session"
        return IngestResult(paper_id, event_id, session, status, detail, processed, blocked)

    def ingest_sessions(
        self, paper_id: str, frame: pd.DataFrame, received_at: str | None = None
    ) -> list[IngestResult]:
        """Split a multi-session frame and ingest each session in order."""
        acct = self.store.account(paper_id)
        tf = account_timeframe(acct["resolved"])
        if tf != "1d" and "bar_start" in frame.columns and not frame.empty:
            labels = pd.to_datetime(frame["bar_start"]).map(session_label)
            return [
                self.ingest(paper_id, frame.loc[labels == lab].copy(), received_at)
                for lab in sorted(set(labels))
            ]
        dates = pd.to_datetime(frame["date"]).dt.normalize()
        return [
            self.ingest(paper_id, frame[dates == d], received_at) for d in sorted(dates.unique())
        ]

    @staticmethod
    def _prepare(
        frame: pd.DataFrame,
        symbols: list[str],
        timeframe: str = "1d",
        session: str | None = None,
    ) -> tuple[pd.DataFrame | None, str, str | None, str, str]:
        """(normalised bars | None, event id, session | None, rejection reason, content)."""
        raw = frame.to_csv(index=False)
        raw_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        if timeframe != "1d":
            return _prepare_intraday(frame, symbols, timeframe, session, raw, raw_id)
        missing = [c for c in COLUMNS if c not in frame.columns]
        if missing:
            return None, raw_id, None, f"missing column(s) {missing}", raw
        if frame.empty:
            return None, raw_id, None, "empty event", raw
        try:
            df = normalise(frame)
        except (ValueError, TypeError) as e:
            return None, raw_id, None, f"invalid bars: {e}", raw
        days = sorted({d.date().isoformat() for d in df["date"]})
        if len(days) != 1:
            return None, raw_id, None, f"an event holds one session, got {len(days)}", raw
        df = df[df["symbol"].isin(symbols)].reset_index(drop=True)
        canon = canonical_csv(df)
        if df.empty:
            return None, raw_id, days[0], "no bars for this account's symbols", canon
        return df, content_id(df), days[0], "", canon

    # Process --------------------------------------------------------------------------
    def process(self, paper_id: str) -> tuple[list[str], str | None]:
        """Step through every ready session in order. Returns (processed, blocked reason)."""
        acct = self.store.account(paper_id)
        try:
            check_versions(acct["resolved"])
        except VersionMismatch as e:
            return [], str(e)
        done: list[str] = []
        while True:
            last = self.store.last_session(paper_id)
            prev = last["session"] if last else None
            nxt = next_session(prev or acct["start_session"], account_timeframe(acct["resolved"]))
            events = self.store.accepted_events(paper_id)
            if nxt not in events:
                return done, None
            acc = _account_from(
                acct["resolved"],
                last["state"] if last else acct["initial_state"],
                self._ledgers(paper_id),
            )
            used = [e for s, e in events.items() if s <= nxt]
            out = self._step(acct, acc, used, nxt)
            try:
                self.store.commit_session(
                    paper_id,
                    session=nxt,
                    expected_previous=prev,
                    row={**out, "processed_at": datetime.now(UTC).isoformat()},
                    decisions=out["decisions"],
                    fills=out["fills"],
                    equity=out["equity"],
                )
            except AlreadyProcessed:
                continue  # another process got there first: re-read and carry on
            done.append(nxt)

    def _history_bars(self, acct: dict[str, Any]) -> pd.DataFrame:
        sid = acct["history_snapshot_id"]
        if sid not in self._history:
            self._history[sid] = load_snapshot(sid, self.history_dir).bars
        return self._history[sid]

    def _event_bars(self, event: dict[str, Any], timeframe: str = "1d") -> pd.DataFrame:
        """Parsed bars of an accepted event (immutable: keyed by its content hash)."""
        eid = event["event_id"]
        if eid not in self._events:
            self._events[eid] = parse_event(event["content"], timeframe)
        return self._events[eid]

    def _input_bars(self, acct: dict[str, Any], events: list[dict[str, Any]]) -> pd.DataFrame:
        tf = account_timeframe(acct["resolved"])
        frames = [self._history_bars(acct)] + [self._event_bars(e, tf) for e in events]
        combined = pd.concat(frames, ignore_index=True)
        if tf == "1d":
            return normalise(combined)
        return normalise_intraday(combined)

    def _step(
        self, acct: dict[str, Any], acc: Account, events: list[dict[str, Any]], session: str
    ) -> dict[str, Any]:
        """Run the engine's own ``Account.step`` for one session on the recorded input."""
        bars = self._input_bars(acct, events)
        tf = account_timeframe(acct["resolved"])
        n_dec, n_fill, n_eq = len(acc.decisions), len(acc.fills), len(acc.equity_rows)
        data = MarketData.build(bars, acct["symbols"], acct["first_session"], session, timeframe=tf)
        if session_label(data.sessions[-1]) != session:
            raise RuntimeError(f"input for {session} does not end on that session")
        view = MarketView.build(acct["resolved"], data)
        idx = len(view.sessions) - 1
        acc.step(view, idx)
        return {
            "idx": idx,
            "event_id": events[-1]["event_id"],
            "input_content_id": content_id(bars) if tf == "1d" else intraday_content_id(bars),
            "input": {
                "history_snapshot_id": acct["history_snapshot_id"],
                "event_ids": [e["event_id"] for e in events],
            },
            "state": _compact_state(acc),
            "decisions": _plain(acc.decisions[n_dec:]),
            "fills": list(enumerate(_plain(acc.fills[n_fill:]), start=n_fill)),
            "equity": _plain(acc.equity_rows[n_eq:]),
        }

    # Read back ------------------------------------------------------------------------
    def _ledgers(self, paper_id: str) -> dict[str, list[dict[str, Any]]]:
        return {k: self.store.ledger_rows(paper_id, k) for k in ("decisions", "fills", "equity")}

    def ledgers(self, paper_id: str) -> RunResult:
        return ledger_frames(self._ledgers(paper_id))

    def session_input(self, paper_id: str, session: str) -> pd.DataFrame:
        """Rebuild exactly the bars a processed session's decisions saw (hash-checked)."""
        acct = self.store.account(paper_id)
        rec = next(s for s in self.store.sessions(paper_id) if s["session"] == session)
        by_id = {e["event_id"]: e for e in self.store.events(paper_id, ACCEPTED)}
        bars = self._input_bars(acct, [by_id[i] for i in rec["input"]["event_ids"]])
        tf = account_timeframe(acct["resolved"])
        got = content_id(bars) if tf == "1d" else intraday_content_id(bars)
        if got != rec["input_content_id"]:
            raise RuntimeError(f"input for {session} no longer matches its recorded hash")
        return bars

    def recompute_session(self, paper_id: str, session: str) -> dict[str, Any]:
        """Re-run one processed session from the state before it and its preserved input."""
        acct = self.store.account(paper_id)
        sessions = self.store.sessions(paper_id)
        k = next(i for i, s in enumerate(sessions) if s["session"] == session)
        before = sessions[k - 1]["state"] if k else acct["initial_state"]
        acc = _account_from(acct["resolved"], before, self._ledgers(paper_id))
        by_id = {e["event_id"]: e for e in self.store.events(paper_id, ACCEPTED)}
        out = self._step(acct, acc, [by_id[i] for i in sessions[k]["input"]["event_ids"]], session)
        out["fills"] = [f for _, f in out["fills"]]
        return out

    def status(self, paper_id: str, as_of: str | date | None = None) -> dict[str, Any]:
        acct = self.store.account(paper_id)
        sessions = self.store.sessions(paper_id)
        last = sessions[-1] if sessions else None
        latest = last["session"] if last else None
        state = last["state"] if last else acct["initial_state"]
        cur = latest or acct["start_session"]
        tf = account_timeframe(acct["resolved"])
        events = self.store.events(paper_id)
        accepted = {e["session"] for e in events if e["status"] == ACCEPTED}
        seen = [
            e["session"]
            for e in events
            if e["session"] and e["status"] in (ACCEPTED, CONFLICT) and e["session"] > cur
        ]
        if as_of is not None:
            seen.append(last_slot_on_or_before(as_of, tf))
        horizon = max(seen) if seen else cur
        if tf == "1d":
            expected = xnys_sessions(pd.Timestamp(cur) + timedelta(days=1), horizon)
            days = xnys_sessions(acct["first_session"], cur)
        else:
            expected = [s for s in grid_labels(acct["first_session"], horizon, tf) if s > cur]
            days = grid_labels(acct["first_session"], cur, tf)
        missing = [s for s in expected if s not in accepted]
        waiting = [s for s in expected if s in accepted]
        equity = self.store.ledger_rows(paper_id, "equity")
        try:
            check_versions(acct["resolved"])
            blocked = None
        except VersionMismatch as e:
            blocked = str(e)
        return {
            "paper_id": paper_id,
            "name": acct["name"],
            "run_id": acct["run_id"],
            "start_session": acct["start_session"],
            "latest_processed": latest,
            "processed_sessions": len(sessions),
            "next_expected": next_session(cur, tf),
            "cash": state["cash"],
            "equity": equity[-1]["equity"] if equity else state["cash"],
            "positions": {
                s: {
                    "qty": p["qty"],
                    "entry_price": p["entry_price"],
                    "fill_session": days[p["fill_idx"]],
                }
                for s, p in state["positions"].items()
            },
            "pending_orders": {
                s: {"side": o["side"], "qty": o["qty"], "decided": days[o["decision_idx"]]}
                for s, o in state["pending"].items()
            },
            "missing": missing,
            "waiting": waiting,
            "conflicts": [
                {k: e[k] for k in ("session", "event_id", "received_at", "detail")}
                for e in events
                if e["status"] == CONFLICT
            ],
            "rejected": [
                {k: e[k] for k in ("session", "event_id", "received_at", "detail")}
                for e in events
                if e["status"] == REJECTED
            ],
            "duplicates": sum(1 for e in events if e["status"] == DUPLICATE),
            "events": len(events),
            "blocked": blocked,
        }

    # Replay ---------------------------------------------------------------------------
    def score_against_frozen(self, paper_id: str, data: Any) -> dict[str, Any]:
        """Compare this account's fills with the frozen model's fills on ``data``.

        The stored recipe is not edited.
        """
        from moneylab.paper.score import score_account

        account = self.store.account(paper_id)
        return score_account(account["resolved"], data, self.ledgers(paper_id).fills)

    def replay(self, paper_id: str) -> dict[str, Any]:
        """Re-run every processed session from the initial state on the recorded events
        and compare decisions, fills and equity with the live record (exact match)."""
        acct = self.store.account(paper_id)
        sessions = self.store.sessions(paper_id)
        events = self.store.accepted_events(paper_id)
        empty: dict[str, list[dict[str, Any]]] = {"decisions": [], "fills": [], "equity": []}
        acc = _account_from(acct["resolved"], acct["initial_state"], empty)
        used: list[dict[str, Any]] = []
        input_mismatches = []
        for rec in sessions:
            used.append(events[rec["session"]])
            out = self._step(acct, acc, used, rec["session"])
            if out["input_content_id"] != rec["input_content_id"]:
                input_mismatches.append(rec["session"])
        live = self._ledgers(paper_id)
        replay = {
            "decisions": _plain(acc.decisions),
            "fills": _plain(acc.fills),
            "equity": _plain(acc.equity_rows),
        }
        diff = None
        for name in ("decisions", "fills", "equity"):
            diff = _first_row_difference(name, live[name], replay[name])
            if diff:
                break
        trading = None
        if diff is not None and live["decisions"] and replay["decisions"]:
            lf, rf = ledger_frames(live), ledger_frames(replay)
            with contextlib.suppress(KeyError, IndexError):
                trading = first_difference(
                    lf.decisions, rf.decisions, lf.fills, rf.fills, lf.equity, rf.equity
                )
        return {
            "paper_id": paper_id,
            "match": diff is None and not input_mismatches,
            "sessions": len(sessions),
            "decisions": len(replay["decisions"]),
            "fills": len(replay["fills"]),
            "first_difference": diff,
            "first_trading_difference": trading,
            "input_mismatches": input_mismatches,
        }
