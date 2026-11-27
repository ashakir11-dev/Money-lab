"""Paper forward testing (acceptance gate 4, forward part).

Two frozen variants (the demo's RSI < 30 and RSI < 35 runs) are paper traded over
sessions split off the end of the RSI fixture: the account's history stops at
2023-03-31 and each later session arrives as a delivered event.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from moneylab.components import registry
from moneylab.data.snapshot import MarketData, read_import_file
from moneylab.demo import FIXTURE, run_rsi_demo
from moneylab.engine.config import VersionMismatch
from moneylab.engine.simulator import Account, MarketView
from moneylab.paper.desk import PaperDesk
from moneylab.service import Lab

START = "2023-03-31"  # last history session
END = "2023-04-28"  # 19 paper sessions: 2023-04-03 .. 2023-04-28 (Good Friday 04-07 closed)
BARS = read_import_file(FIXTURE)


def sessions_between(lo: str, hi: str) -> list[str]:
    days = sorted({d.date().isoformat() for d in BARS["date"]})
    return [d for d in days if lo < d <= hi]


def event(session: str) -> pd.DataFrame:
    """One delivered session, in the import format, as a provider would hand it over."""
    df = BARS[BARS["date"] == pd.Timestamp(session)].copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df.reset_index(drop=True)


def rows(lab: Lab, pid: str, ledger: str) -> list[dict[str, Any]]:
    return lab.paper.store.ledger_rows(pid, ledger)


def feed(lab: Lab, pid: str, sessions: list[str]) -> list[dict[str, Any]]:
    return [lab.paper_ingest(pid, event(s)) for s in sessions]


def reference(lab: Lab, run_id: str, start: str, end: str) -> Account:
    """The historical engine over the same period, started from the same state: a fresh
    account with the run's starting cash at ``start``, stepping every later session of
    the full fixture."""
    run = lab.store.get(run_id)
    resolved = {k: v for k, v in run["resolved"].items() if k != "data"}
    symbols = sorted(set(resolved["universe"]) | {resolved["benchmark"]})
    data = MarketData.build(BARS, symbols, run["resolved"]["data"]["first_session"], end)
    view = MarketView.build(resolved, data)
    acc = Account(resolved)
    acc.last_index = int(view.sessions.get_loc(pd.Timestamp(start)))
    for i in range(acc.last_index + 1, len(view.sessions)):
        acc.step(view, i)
    return acc


def plain(x: Any) -> Any:
    return json.loads(json.dumps(x))


@pytest.fixture(scope="module")
def traded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    lab = Lab.open(tmp_path_factory.mktemp("paper") / "home")
    demo = run_rsi_demo(lab)
    out: dict[str, Any] = {"lab": lab, "runs": {}, "papers": {}, "results": {}}
    for variant, run_id in (("rsi30", demo["original"]), ("rsi35", demo["changed"])):
        pid = lab.start_paper(run_id, START, name=f"{variant} paper")
        out["runs"][variant] = run_id
        out["papers"][variant] = pid
        out["results"][variant] = feed(lab, pid, sessions_between(START, END))
    return out


def test_two_frozen_variants_process_every_new_session(traded) -> None:  # type: ignore[no-untyped-def]
    lab: Lab = traded["lab"]
    new = sessions_between(START, END)
    assert len(new) == 19 and "2023-04-07" not in new
    for variant, pid in traded["papers"].items():
        assert [r["status"] for r in traded["results"][variant]] == ["accepted"] * 19
        assert [r["processed"] for r in traded["results"][variant]] == [[s] for s in new]
        st = lab.paper_status(pid)
        assert st["latest_processed"] == END and st["processed_sessions"] == 19
        assert st["missing"] == [] and st["conflicts"] == [] and st["blocked"] is None
        # The paper account holds the frozen run's resolved settings.
        acct = lab.paper.store.account(pid)
        run = lab.store.get(traded["runs"][variant])
        assert acct["resolved"] == {k: v for k, v in run["resolved"].items() if k != "data"}
    assert {a["id"] for a in lab.paper_accounts()} == set(traded["papers"].values())


def test_paper_equals_historical_engine_from_the_same_state(traded) -> None:  # type: ignore[no-untyped-def]
    lab: Lab = traded["lab"]
    for variant, pid in traded["papers"].items():
        ref = reference(lab, traded["runs"][variant], START, END)
        assert rows(lab, pid, "decisions") == plain(ref.decisions)
        assert rows(lab, pid, "fills") == plain(ref.fills)
        assert rows(lab, pid, "equity") == plain(ref.equity_rows)
    assert rows(lab, traded["papers"]["rsi30"], "fills") == []


def test_hand_checked_paper_fills(traded) -> None:  # type: ignore[no-untyped-def]
    # RSI < 35 variant, fresh account with 100,000.00, target 10%, no transaction cost,
    # 10-session hold.
    # 2023-04-04 close: AAA crosses above SMA(20) with RSI 31.68 < 35 -> enter,
    #   qty = floor(0.10 x 100,000.00 / 74.56) = floor(134.12) = 134.
    # 2023-04-05 open 74.91: gross 134 x 74.91 = 10,037.94; cost 0;
    #   cash 100,000.00 - 10,037.94 = 89,962.06.
    # 2023-04-19 close: held 10 sessions, counting the 2023-04-05 fill session as one
    #   (2023-04-07 is a holiday) -> exit. 2023-04-20 open 76.82:
    #   gross 10,293.88; cost 0; cash 89,962.06 + 10,293.88 = 100,255.94.
    lab: Lab = traded["lab"]
    pid = traded["papers"]["rsi35"]
    trading = [
        (d["session"], d["symbol"], d["action"], d.get("qty"))
        for d in rows(lab, pid, "decisions")
        if d["action"] in ("enter", "exit")
    ]
    assert trading == [("2023-04-04", "AAA", "enter", 134), ("2023-04-19", "AAA", "exit", 134)]
    cols = ("session", "symbol", "side", "qty", "price", "gross", "cost", "cash_after")
    assert [tuple(f[c] for c in cols) for f in rows(lab, pid, "fills")] == [
        ("2023-04-05", "AAA", "buy", 134, 74.91, 10037.94, 0.0, 89962.06),
        ("2023-04-20", "AAA", "sell", 134, 76.82, 10293.88, 0.0, 100255.94),
    ]
    st = lab.paper_status(pid)
    assert st["cash"] == 100255.94 and st["positions"] == {}


def test_replay_matches_exactly_and_detects_a_tampered_record(traded) -> None:  # type: ignore[no-untyped-def]
    lab: Lab = traded["lab"]
    for pid in traded["papers"].values():
        rep = lab.paper_replay(pid)
        assert rep["match"], rep
        assert rep["sessions"] == 19 and rep["input_mismatches"] == []
    pid = traded["papers"]["rsi35"]
    assert lab.paper_replay(pid)["fills"] == 2

    # Compare really compares: alter one stored fill and replay reports it.
    db = lab.paper.store.path
    with sqlite3.connect(db) as c:
        (raw,) = c.execute(
            "SELECT row_json FROM paper_fills WHERE paper_id = ? AND fill_no = 0", (pid,)
        ).fetchone()
        bad = json.loads(raw) | {"qty": 135}
        c.execute(
            "UPDATE paper_fills SET row_json = ? WHERE paper_id = ? AND fill_no = 0",
            (json.dumps(bad), pid),
        )
    try:
        rep = lab.paper_replay(pid)
        assert not rep["match"]
        assert rep["first_difference"]["ledger"] == "fills"
        assert rep["first_difference"]["live"]["qty"] == 135
        assert rep["first_difference"]["replay"]["qty"] == 134
    finally:
        with sqlite3.connect(db) as c:
            c.execute(
                "UPDATE paper_fills SET row_json = ? WHERE paper_id = ? AND fill_no = 0",
                (raw, pid),
            )
    assert lab.paper_replay(pid)["match"]


def test_preserved_input_rebuilds_each_decision(traded) -> None:  # type: ignore[no-untyped-def]
    lab: Lab = traded["lab"]
    pid = traded["papers"]["rsi35"]
    desk = lab.paper
    decisions = rows(lab, pid, "decisions")
    fills = rows(lab, pid, "fills")
    for rec in desk.store.sessions(pid):
        s = rec["session"]
        bars = desk.session_input(pid, s)  # raises if its hash no longer matches
        assert bars["date"].max() == pd.Timestamp(s)  # nothing after the session
        assert len(rec["input"]["event_ids"]) == rec["idx"] - 61  # history ends at index 61
        again = desk.recompute_session(pid, s)
        assert again["input_content_id"] == rec["input_content_id"]
        assert again["decisions"] == [d for d in decisions if d["session"] == s]
        assert again["fills"] == [f for f in fills if f["session"] == s]
        assert again["state"] == rec["state"]


def test_restart_and_duplicate_delivery_add_zero_orders_or_fills(
    demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    lab: Lab = demo["lab"]
    home = lab.settings.home
    new = sessions_between(START, "2023-04-14")
    pid = lab.start_paper(demo["changed"], START)
    feed(lab, pid, new[:3])

    # Crash after an event was stored but before it was processed.
    def crash(self: PaperDesk, paper_id: str) -> tuple[list[str], str | None]:
        raise KeyboardInterrupt

    monkeypatch.setattr(PaperDesk, "process", crash)
    with pytest.raises(KeyboardInterrupt):
        lab.paper_ingest(pid, event(new[3]))
    monkeypatch.undo()

    # Restart: a new Lab reads everything from disk; the provider re-delivers it all.
    lab2 = Lab.open(home)
    assert lab2.paper_status(pid)["latest_processed"] == new[2]
    assert lab2.paper_status(pid)["waiting"] == [new[3]]
    out = feed(lab2, pid, new)
    assert [r["status"] for r in out] == ["duplicate"] * 4 + ["accepted"] * (len(new) - 4)
    assert out[0]["processed"] == [new[3]]  # the stored event is processed on resume
    counts = {k: len(rows(lab2, pid, k)) for k in ("decisions", "fills", "equity")}

    # Same result as one uninterrupted pass of the engine.
    ref = reference(lab2, demo["changed"], START, new[-1])
    assert rows(lab2, pid, "decisions") == plain(ref.decisions)
    assert rows(lab2, pid, "fills") == plain(ref.fills)
    assert counts["fills"] == 1  # AAA bought 2023-04-05, still held on 2023-04-14

    # Another restart and a second full re-delivery: zero new orders, fills or rows.
    lab3 = Lab.open(home)
    again = feed(lab3, pid, new)
    assert {r["status"] for r in again} == {"duplicate"}
    assert all(r["processed"] == [] for r in again)
    assert {k: len(rows(lab3, pid, k)) for k in counts} == counts
    assert lab3.paper.process(pid) == ([], None)
    st = lab3.paper_status(pid)
    assert st["duplicates"] == 4 + len(new) and st["events"] == 2 * len(new) + 4
    assert st["positions"]["AAA"] == {
        "qty": 134,
        "entry_price": 74.91,
        "fill_session": "2023-04-05",
    }
    assert lab3.paper_replay(pid)["match"]


def test_conflict_is_rejected_and_skipped_session_is_missing(demo: dict[str, Any]) -> None:
    lab: Lab = demo["lab"]
    s = sessions_between(START, "2023-04-14")
    pid = lab.start_paper(demo["changed"], START)
    feed(lab, pid, s[:2])
    before = {k: rows(lab, pid, k) for k in ("decisions", "fills", "equity")}

    # Same session, different content: rejected, flagged, nothing reprocessed.
    changed = event(s[1])
    changed.loc[changed["symbol"] == "AAA", "close"] += 1.0
    r = lab.paper_ingest(pid, changed)
    assert r["status"] == "conflict" and r["processed"] == []
    assert "AAA close 74.56 -> 75.56" in r["detail"]
    assert {k: rows(lab, pid, k) for k in before} == before

    # Session s[2] never arrives; s[3] and s[4] do (out of order): stored, not processed.
    for x in (s[3], s[4]):
        r = lab.paper_ingest(pid, event(x))
        assert r["status"] == "accepted" and r["processed"] == []
        assert "waiting for an earlier session" in r["detail"]
    st = lab.paper_status(pid)
    assert st["latest_processed"] == s[1]
    assert st["missing"] == [s[2]] and st["waiting"] == [s[3], s[4]]
    assert [c["session"] for c in st["conflicts"]] == [s[1]]
    assert lab.paper_status(pid, as_of="2023-04-11")["missing"] == [s[2], s[5]]

    # Other bad deliveries are recorded as rejected, never processed.
    weekend = event(s[0]).assign(date="2023-04-08")
    history = event(START)
    two_days = pd.concat([event(s[5]), event(s[6])])
    for bad, why in (
        (weekend, "not an XNYS session"),
        (history, "part of the history"),
        (two_days, "one session"),
        (event(s[5]).drop(columns="volume"), "missing column"),
    ):
        r = lab.paper_ingest(pid, bad)
        assert r["status"] == "rejected" and why in r["detail"], r
    assert len(lab.paper_status(pid)["rejected"]) == 4

    # The missing session arrives: it and the waiting ones are processed in order and the
    # result equals in-order delivery.
    r = lab.paper_ingest(pid, event(s[2]))
    assert r["processed"] == [s[2], s[3], s[4]]
    st = lab.paper_status(pid)
    assert st["missing"] == [] and st["waiting"] == [] and st["latest_processed"] == s[4]
    ref = reference(lab, demo["changed"], START, s[4])
    assert rows(lab, pid, "fills") == plain(ref.fills)
    assert rows(lab, pid, "decisions") == plain(ref.decisions)
    assert lab.paper_replay(pid)["match"]


def test_changing_future_prices_changes_no_earlier_decision(demo: dict[str, Any]) -> None:
    lab: Lab = demo["lab"]
    s = sessions_between(START, END)
    a = lab.start_paper(demo["changed"], START)
    b = lab.start_paper(demo["changed"], START)
    feed(lab, a, s)
    k = 3  # from 2023-04-05 on, B sees very different prices
    feed(lab, b, s[:k])
    for x in s[k:]:
        e = event(x)
        e[["open", "high", "low", "close"]] *= 0.5
        lab.paper_ingest(b, e)
    da, db = rows(lab, a, "decisions"), rows(lab, b, "decisions")
    earlier_a = [d for d in da if d["session"] < s[k]]
    earlier_b = [d for d in db if d["session"] < s[k]]
    assert earlier_a and earlier_a == earlier_b
    assert da != db  # the change did matter for later sessions
    sa = {r["session"]: r for r in lab.paper.store.sessions(a)}
    sb = {r["session"]: r for r in lab.paper.store.sessions(b)}
    for x in s[:k]:
        assert sa[x]["state"] == sb[x]["state"]
        assert sa[x]["input_content_id"] == sb[x]["input_content_id"]


def test_frozen_versions_are_enforced(
    demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    lab: Lab = demo["lab"]
    pid = lab.start_paper(demo["original"], START)
    rsi = registry.get_component("rsi")
    monkeypatch.setitem(registry._REGISTRY, "rsi", replace(rsi, version="2.0.0"))
    with pytest.raises(VersionMismatch):
        lab.start_paper(demo["original"], START)
    r = lab.paper_ingest(pid, event("2023-04-03"))
    assert r["status"] == "accepted" and r["processed"] == []
    assert "rsi 1.0.0 -> 2.0.0" in r["blocked"]
    monkeypatch.undo()
    assert lab.paper.process(pid) == (["2023-04-03"], None)


def test_start_validation(demo: dict[str, Any]) -> None:
    lab: Lab = demo["lab"]
    with pytest.raises(ValueError, match="after the snapshot's last bar"):
        lab.start_paper(demo["original"], "2023-07-31")
    # Default start: the snapshot's last session; a weekend start snaps back to Friday.
    assert lab.paper_status(lab.start_paper(demo["original"]))["start_session"] == "2023-06-30"
    assert (
        lab.paper_status(lab.start_paper(demo["original"], "2023-04-02"))["start_session"]
        == "2023-03-31"
    )


def test_cli_paper_commands(demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from moneylab.cli import app

    lab: Lab = demo["lab"]
    monkeypatch.setenv("MONEYLAB_HOME", str(lab.settings.home))
    runner = CliRunner()
    res = runner.invoke(app, ["paper", "start", demo["changed"], "--start", START])
    assert res.exit_code == 0, res.output
    pid = res.output.split()[2].rstrip(":")
    path = Path(lab.settings.home) / "new_sessions.csv"
    BARS[(BARS["date"] > START) & (BARS["date"] <= "2023-04-06")].to_csv(path, index=False)
    res = runner.invoke(app, ["paper", "ingest", pid, str(path)])
    assert res.exit_code == 0, res.output
    assert res.output.count("accepted") == 4
    res = runner.invoke(app, ["paper", "status", pid])
    assert json.loads(res.output)["latest_processed"] == "2023-04-06"
    res = runner.invoke(app, ["paper", "replay", pid])
    assert res.exit_code == 0 and json.loads(res.output)["match"] is True
    res = runner.invoke(app, ["paper", "list"])
    assert pid in res.output
