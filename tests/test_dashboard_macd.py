"""Drives the real dashboard: MACD is picked, configured and run entirely in the UI.

The dashboard has no MACD-specific code. Its four parameter controls come from the
declaration in src/moneylab/components/macd.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from moneylab.demo import FIXTURE
from moneylab.service import Lab

APP = Path(__file__).resolve().parents[1] / "src" / "moneylab" / "dashboard" / "app.py"


def _btn(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def _rev(at: AppTest) -> int:
    return int(at.session_state["draft_rev"])


def _builder_with_macd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AppTest, int]:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception

    at.sidebar.radio(key="page").set_value("Data").run()
    at.text_input(key="data.path").set_value(str(FIXTURE)).run()
    _btn(at, "data.import")

    at.sidebar.radio(key="page").set_value("Strategy builder").run()
    at.multiselect(key=f"b{_rev(at)}.universe").set_value(["AAA", "BBB", "CCC", "DDD"]).run()
    _btn(at, f"b{_rev(at)}.entry.add")
    r = _rev(at)
    assert "MACD" in at.selectbox(key=f"b{r}.entry1.ind").options  # declared label
    at.selectbox(key=f"b{r}.entry1.ind").set_value("macd").run()
    assert not at.exception, at.exception
    return at, _rev(at)


def test_macd_configured_and_run_from_the_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, r = _builder_with_macd(tmp_path, monkeypatch)

    # The four declared parameters appear with their defaults.
    assert at.number_input(key=f"b{r}.entry1.ind.fast").value == 12
    assert at.number_input(key=f"b{r}.entry1.ind.slow").value == 26
    assert at.number_input(key=f"b{r}.entry1.ind.signal").value == 9
    assert at.selectbox(key=f"b{r}.entry1.ind.output").value == "histogram"
    # Bounds come from the declaration too.
    assert at.number_input(key=f"b{r}.entry1.ind.fast").min == 1
    assert at.number_input(key=f"b{r}.entry1.ind.slow").max == 400

    # Change one parameter and the condition threshold, then run.
    at.number_input(key=f"b{r}.entry1.ind.fast").set_value(8).run()
    at.selectbox(key=f"b{r}.entry1.op").set_value("above").run()
    at.number_input(key=f"b{r}.entry1.value").set_value(0.0).run()
    at.text_input(key=f"b{r}.name").set_value("SMA20 cross + MACD hist > 0").run()
    _btn(at, "run")
    run_id = at.session_state["last_run"]

    lab = Lab.open(tmp_path / "home")
    entry = lab.store.get(run_id)["resolved"]["entry"]
    assert entry[1] == {
        "kind": "threshold",
        "version": "1.0.0",
        "params": {"op": "above", "value": 0.0},
        "indicator": {
            "id": "macd",
            "version": "1.0.0",
            "params": {"fast": 8, "slow": 26, "signal": 9, "output": "histogram"},
        },
    }
    assert len(lab.store.ledgers(run_id).fills) > 0

    # Reopen it on the Runs page: the resolved settings show the MACD parameters.
    at.sidebar.radio(key="page").set_value("Runs").run()
    at.selectbox(key="runs.open").set_value(run_id).run()
    assert not at.exception
    shown = at.json[0].value
    assert '"fast": 8' in shown or '"fast":8' in shown


def test_fast_not_below_slow_is_rejected_in_the_dashboard_and_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, r = _builder_with_macd(tmp_path, monkeypatch)
    at.number_input(key=f"b{r}.entry1.ind.fast").set_value(30).run()
    at.button(key="run").click().run()
    # The editor keeps drawing the controls; the run button reports the error.
    assert not at.exception, at.exception
    errors = [str(e.value) for e in at.error]
    assert any("Not run" in m and "fast (30) must be shorter than slow (26)" in m for m in errors)
    assert at.number_input(key=f"b{r}.entry1.ind.fast").value == 30
    assert "last_run" not in at.session_state
    assert Lab.open(tmp_path / "home").store.list_runs() == []
