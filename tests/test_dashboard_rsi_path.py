"""Drives the real dashboard script headlessly: the threshold change happens in the UI.

Flow: import the fixture, build SMA20-cross in the builder, add an RSI condition
through the UI (default threshold 30), run; open that run, duplicate it, change only
30 -> 35, run; the compare page then shows exactly one setting difference and the
first changed decision (AAA on 2023-04-04, observed RSI 31.68).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from moneylab.demo import FIXTURE

APP = Path(__file__).resolve().parents[1] / "src" / "moneylab" / "dashboard" / "app.py"


def _btn(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def _rev(at: AppTest) -> int:
    return int(at.session_state["draft_rev"])


def test_rsi_threshold_change_in_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception

    # Data: import the offline fixture as a snapshot.
    at.sidebar.radio(key="page").set_value("Data").run()
    at.text_input(key="data.path").set_value(str(FIXTURE)).run()
    _btn(at, "data.import")

    # Builder: default draft is "close crosses above SMA20". Add an RSI entry filter.
    at.sidebar.radio(key="page").set_value("Strategy builder").run()
    at.multiselect(key=f"b{_rev(at)}.universe").set_value(["AAA", "BBB", "CCC", "DDD"]).run()
    _btn(at, f"b{_rev(at)}.entry.add")
    r = _rev(at)
    assert at.selectbox(key=f"b{r}.entry1.ind").value == "rsi"
    assert at.number_input(key=f"b{r}.entry1.ind.length").value == 14
    assert at.selectbox(key=f"b{r}.entry1.op").value == "below"
    assert at.number_input(key=f"b{r}.entry1.value").value == 30.0
    at.text_input(key=f"b{r}.name").set_value("RSI below 30").run()
    _btn(at, "run")
    original = at.session_state["last_run"]

    # Runs: open it, duplicate, change only the RSI threshold to 35, run.
    at.sidebar.radio(key="page").set_value("Runs").run()
    at.selectbox(key="runs.open").set_value(original).run()
    _btn(at, "runs.duplicate")
    assert at.session_state["page"] == "Strategy builder"
    assert at.session_state["draft_parent"] == original
    r = _rev(at)
    at.number_input(key=f"b{r}.entry1.value").set_value(35.0).run()
    _btn(at, "run")
    changed = at.session_state["last_run"]
    assert changed != original

    # Compare: one config difference and the first changed decision.
    at.sidebar.radio(key="page").set_value("Compare").run()
    assert not at.exception
    assert at.selectbox(key="cmp.a").value == original
    assert at.selectbox(key="cmp.b").value == changed
    diff = at.dataframe[0].value
    assert diff["setting"].tolist() == ["entry[1].params.value"]
    assert diff[["a", "b"]].values.tolist() == [["30.0", "35.0"]]

    fd = at.session_state["first_difference"]
    assert (fd["symbol"], fd["session"]) == ("AAA", "2023-04-04")
    assert fd["a"]["action"] == "no_signal"
    assert fd["b"]["action"] == "enter"
    assert fd["b"]["fill"]["qty"] == 134
    text = " ".join(m.value for m in at.markdown)
    assert "AAA on 2023-04-04" in text
    assert "Filled 134 @ 74.91 on 2023-04-05" in text
