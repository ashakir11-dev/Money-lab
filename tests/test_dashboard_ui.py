"""Dashboard presentation: demo vs real data labels, plain-English summaries, and a
backtest on Tiingo data started entirely from the dashboard (offline fake Tiingo)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from moneylab.dashboard.ui import data_source, strategy_summary
from moneylab.demo import rsi_strategy
from moneylab.engine.config import resolve
from moneylab.service import Lab
from moneylab.settings import Settings
from tiingo_util import TEST_KEY, FakeTiingo, payloads_from_csv

APP = Path(__file__).resolve().parents[1] / "src" / "moneylab" / "dashboard" / "app.py"


def test_data_source_labels() -> None:
    assert data_source("tiingo").kind == "real"
    assert data_source("file:rsi_divergence.csv").kind == "demo"
    assert data_source("file:my_prices.csv").kind == "file"
    assert data_source("file:my_prices.csv+actions:acts.csv").kind == "file"


def test_strategy_summary_reads_like_a_sentence() -> None:
    text = strategy_summary(resolve(rsi_strategy(30), Settings()))
    assert text == (
        "Buy when close crosses above SMA(20) and RSI(14) below 30; sell after 10 sessions. "
        "Trades 4 symbols against SPY, 10% of equity per position."
    )


def _rev(at: AppTest) -> int:
    return int(at.session_state["draft_rev"])


def _ok(at: AppTest) -> None:
    assert not at.exception, at.exception


def test_backtest_on_tiingo_data_from_the_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TIINGO_API_KEY", TEST_KEY)
    fake = FakeTiingo(payloads_from_csv())

    def provider(self: Lab, name: str = "tiingo") -> Any:
        return fake.provider(self.settings.cache_dir)

    monkeypatch.setattr(Lab, "provider", provider)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    _ok(at)

    at.sidebar.radio(key="page").set_value("Data").run()
    at.text_input(key="data.tiingo.symbols").set_value("AAA, BBB SPY").run()
    at.date_input(key="data.tiingo.start").set_value(date(2023, 1, 3)).run()
    at.date_input(key="data.tiingo.end").set_value(date(2023, 6, 30)).run()
    at.button(key="data.tiingo.fetch").click().run()
    _ok(at)
    assert len(fake.requests) == 3
    assert any("Saved real-data snapshot" in s.value for s in at.success)

    # One click to the builder, with the new snapshot and its symbols preselected.
    at.button(key="data.tiingo.build").click().run()
    _ok(at)
    assert at.session_state["page"] == "Strategy builder"
    r = _rev(at)
    assert at.multiselect(key=f"b{r}.universe").value == ["AAA", "BBB"]
    assert at.selectbox(key=f"b{r}.benchmark").value == "SPY"
    at.button(key="run").click().run()
    _ok(at)
    run_id = at.session_state["last_run"]

    at.sidebar.radio(key="page").set_value("Runs").run()
    _ok(at)
    assert at.selectbox(key="runs.open").value == run_id
    shown = " ".join(m.value for m in at.markdown)
    assert "Real data · Tiingo" in shown
    assert "Demo data" not in shown


def test_fetch_is_disabled_without_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TIINGO_API_KEY", "")
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio(key="page").set_value("Data").run()
    _ok(at)
    assert at.button(key="data.tiingo.fetch").disabled
    assert any("No Tiingo API key" in w.value for w in at.warning)
