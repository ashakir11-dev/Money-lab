"""Drives the real dashboard's Paper trading page headlessly.

Start a paper account from the saved RSI < 35 run, deliver new sessions from a file
(one of them twice), see the latest processed session and fills, and replay.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from moneylab.data.snapshot import read_import_file
from moneylab.demo import FIXTURE, run_rsi_demo
from moneylab.service import Lab

APP = Path(__file__).resolve().parents[1] / "src" / "moneylab" / "dashboard" / "app.py"


def _click(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def test_paper_page_start_ingest_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("MONEYLAB_HOME", str(home))
    st.cache_resource.clear()  # the dashboard caches its Lab per process
    demo = run_rsi_demo(Lab.open(home))
    bars = read_import_file(FIXTURE)
    feed = tmp_path / "sessions.csv"
    new = bars[(bars["date"] > "2023-03-31") & (bars["date"] <= "2023-04-06")]
    # 2023-04-04 delivered twice: the second copy must be ignored as a duplicate.
    again = new[new["date"] == "2023-04-04"]
    new.to_csv(feed, index=False)

    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio(key="page").set_value("Paper trading").run()
    assert not at.exception, at.exception
    at.selectbox(key="paper.run").set_value(demo["changed"]).run()
    assert at.text_input(key="paper.start").value == "2023-06-30"
    at.text_input(key="paper.start").set_value("2023-03-31").run()
    _click(at, "paper.create")
    pid = at.session_state["paper.selected"]

    at.text_input(key="paper.path").set_value(str(feed)).run()
    _click(at, "paper.ingest")
    assert [r["status"] for r in at.session_state["paper.last_ingest"]] == ["accepted"] * 4
    again.to_csv(feed, index=False)
    _click(at, "paper.ingest")
    assert [r["status"] for r in at.session_state["paper.last_ingest"]] == ["duplicate"]

    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Latest processed session"] == "2023-04-06"
    assert metrics["Next expected"] == "2023-04-10"
    assert metrics["Open positions"] == "1"
    assert metrics["Cash"] == "89,957.04"

    _click(at, "paper.replay")
    assert any("Replay matches exactly" in s.value for s in at.success)
    lab = Lab.open(home)
    assert len(lab.paper_ledgers(pid).fills) == 1
