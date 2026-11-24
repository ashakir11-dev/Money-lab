"""Gate 3: from a saved run, change the universe and an entry setting, run, and find
the first changed trade and its reason, entirely in the dashboard.

Interactions are counted from the saved run: duplicate (1), edit universe (2), edit
threshold (3), run (4), open the comparison (5). The Compare page then leads with the
first differing decision and its reason: zero further interactions (gate: at most two).
The fixture is known to diverge (fixtures/data/rsi_divergence.csv).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from moneylab.demo import FIXTURE

APP = Path(__file__).resolve().parents[1] / "src" / "moneylab" / "dashboard" / "app.py"


def _rev(at: AppTest) -> int:
    return int(at.session_state["draft_rev"])


class _Driver:
    def __init__(self, at: AppTest) -> None:
        self.at = at
        self.interactions = 0

    def act(self, widget: object) -> None:
        widget.run()  # type: ignore[attr-defined]
        self.interactions += 1
        assert not self.at.exception, self.at.exception


def _saved_run(at: AppTest) -> str:
    """Setup (not counted): import the fixture and save SMA20 cross + RSI < 30."""
    at.sidebar.radio(key="page").set_value("Data").run()
    at.text_input(key="data.path").set_value(str(FIXTURE)).run()
    at.button(key="data.import").click().run()
    at.sidebar.radio(key="page").set_value("Strategy builder").run()
    at.multiselect(key=f"b{_rev(at)}.universe").set_value(["AAA", "BBB", "CCC", "DDD"]).run()
    at.button(key=f"b{_rev(at)}.entry.add").click().run()
    at.text_input(key=f"b{_rev(at)}.name").set_value("SMA20 cross + RSI < 30").run()
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    return str(at.session_state["last_run"])


@pytest.mark.parametrize(
    ("universe", "expected"),
    [
        # Dropping DDD removes the earliest trade: the first difference is DDD's entry,
        # and run B says why it has none.
        (
            ["AAA", "BBB", "CCC"],
            ("DDD", "2023-03-07", "enter", "none", "DDD is not in this run's universe"),
        ),
        # Dropping CCC changes nothing early; the threshold 30 -> 35 makes AAA enter.
        (
            ["AAA", "BBB", "DDD"],
            ("AAA", "2023-04-04", "no_signal", "enter", "all entry conditions passed"),
        ),
    ],
)
def test_change_universe_and_entry_then_find_first_changed_trade(
    universe: list[str],
    expected: tuple[str, str, str, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    original = _saved_run(at)
    at.sidebar.radio(key="page").set_value("Runs").run()
    at.selectbox(key="runs.open").set_value(original).run()

    d = _Driver(at)
    d.act(at.button(key="runs.duplicate").click())
    r = _rev(at)
    d.act(at.multiselect(key=f"b{r}.universe").set_value(universe))
    d.act(at.number_input(key=f"b{r}.entry1.value").set_value(35.0))
    d.act(at.button(key="run").click())
    changed = at.session_state["last_run"]
    d.act(at.button(key="run.compare").click())
    assert d.interactions == 5

    # The comparison page is open with the parent as A and the new run as B.
    assert at.session_state["page"] == "Compare"
    assert at.selectbox(key="cmp.a").value == original
    assert at.selectbox(key="cmp.b").value == changed
    diff = at.dataframe[0].value
    assert sorted(diff["setting"]) == ["entry[1].params.value", "universe"]

    # The first differing decision and its reason are on screen with no more clicks.
    symbol, session, a_action, b_action, reason = expected
    fd = at.session_state["first_difference"]
    assert (fd["symbol"], fd["session"]) == (symbol, session)
    assert (fd["a"]["action"], fd["b"]["action"]) == (a_action, b_action)
    shown = " ".join(m.value for m in at.markdown)
    assert f"{symbol} on {session}" in shown
    assert reason in shown
