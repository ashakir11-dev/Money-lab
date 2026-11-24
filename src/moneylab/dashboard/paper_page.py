"""Dashboard page: paper forward testing of frozen runs (docs/paper-trading.md).

All work goes through ``moneylab.service.Lab``; this module only presents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from moneylab.service import Lab


def _table(rows: list[dict[str, Any]] | pd.DataFrame, empty: str) -> None:
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if df.empty:
        st.caption(empty)
        return
    out = df.copy()
    for col in ("conditions", "values"):
        if col in out:
            out[col] = out[col].map(lambda v: "" if v is None else str(v))
    st.dataframe(out, hide_index=True, width="stretch")


def _start_form(lab: Lab) -> None:
    runs = [r for r in lab.store.list_runs() if r["status"] == "completed"]
    if not runs:
        st.info("Save a run first: a paper account freezes a completed run.")
        return
    with st.expander("Start a paper account from a saved run", expanded=not lab.paper_accounts()):
        ids = [r["id"] for r in runs]
        run_id = st.selectbox(
            "Frozen run",
            ids,
            key="paper.run",
            format_func=lambda i: f"{i} - {next(r for r in runs if r['id'] == i)['name']}",
        )
        last = lab.store.get(run_id)["resolved"]["data"]["last_session"]
        start = st.text_input(
            "Last history session (paper starts with the next one)", last, key="paper.start"
        )
        name = st.text_input("Name (optional)", "", key="paper.name")
        if st.button("Start paper account", key="paper.create", type="primary"):
            try:
                pid = lab.start_paper(run_id, start or None, name)
            except Exception as e:  # refused (version change, bad date): shown, not started
                st.error(f"Not started: {e}")
            else:
                st.session_state["paper.selected"] = pid
                st.success(f"Started paper account {pid}")


def page_paper(lab: Lab) -> None:
    _start_form(lab)
    accounts = lab.paper_accounts()
    if not accounts:
        st.info("No paper accounts yet.")
        return
    st.dataframe(pd.DataFrame(accounts), hide_index=True, width="stretch")
    ids = [a["id"] for a in accounts]
    cur = st.session_state.get("paper.selected")
    pid = st.selectbox(
        "Paper account",
        ids,
        index=ids.index(cur) if cur in ids else 0,
        key="paper.account",
        format_func=lambda i: f"{i} - {next(a for a in accounts if a['id'] == i)['name']}",
    )

    path = st.text_input(
        "Deliver sessions from a file (CSV: date,symbol,open,high,low,close,volume)",
        "",
        key="paper.path",
    )
    if st.button("Ingest sessions", key="paper.ingest") and path:
        try:
            results = lab.paper_ingest_sessions(pid, pd.read_csv(Path(path)))
        except Exception as e:
            st.error(str(e))
        else:
            st.session_state["paper.last_ingest"] = results
    if st.session_state.get("paper.last_ingest"):
        st.caption("Last delivery")
        _table(
            [
                {**r, "processed": ", ".join(r["processed"])}
                for r in st.session_state["paper.last_ingest"]
            ],
            "",
        )

    status = lab.paper_status(pid, as_of=pd.Timestamp.today().date())
    if status["blocked"]:
        st.error(f"Processing blocked: {status['blocked']}")
    cols = st.columns(5)
    cols[0].metric("Latest processed session", status["latest_processed"] or "none yet")
    cols[1].metric("Next expected", status["next_expected"])
    cols[2].metric("Cash", f"{status['cash']:,.2f}")
    cols[3].metric("Equity", f"{status['equity']:,.2f}")
    cols[4].metric("Open positions", len(status["positions"]))
    st.caption(
        f"Frozen run `{status['run_id']}`, history to {status['start_session']}. "
        f"{status['events']} events received, {status['duplicates']} duplicates ignored."
    )

    st.subheader("Missing and conflicting data")
    c1, c2 = st.columns(2)
    c1.caption("Missing sessions (processing waits for them)")
    c1.write(", ".join(status["missing"]) or "none")
    c2.caption("Received, waiting for an earlier session")
    c2.write(", ".join(status["waiting"]) or "none")
    st.caption("Conflicting deliveries (rejected; the first accepted version stands)")
    _table(status["conflicts"], "none")
    st.caption("Rejected deliveries")
    _table(status["rejected"], "none")

    led = lab.paper_ledgers(pid)
    t1, t2, t3, t4 = st.tabs(["Decisions", "Fills", "Daily cash and equity", "Positions"])
    with t1:
        _table(led.decisions, "No decisions yet.")
    with t2:
        _table(led.fills, "No fills yet.")
    with t3:
        _table(led.equity, "No sessions processed yet.")
    with t4:
        _table(
            [{"symbol": s, **p} for s, p in status["positions"].items()]
            + [{"symbol": s, "pending": True, **o} for s, o in status["pending_orders"].items()],
            "Flat.",
        )

    if st.button("Replay recorded inputs and compare", key="paper.replay"):
        st.session_state["paper.replay_result"] = lab.paper_replay(pid)
    rep = st.session_state.get("paper.replay_result")
    if rep and rep["paper_id"] == pid:
        if rep["match"]:
            st.success(
                f"Replay matches exactly: {rep['sessions']} sessions, "
                f"{rep['decisions']} decisions, {rep['fills']} fills."
            )
        else:
            st.error("Replay differs from the live record.")
            st.json(rep)
