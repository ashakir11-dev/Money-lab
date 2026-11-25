"""After-close helper: fetch new sessions from a provider and ingest into paper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from moneylab.data.providers.base import ProviderHistory, empty_actions
from moneylab.demo import run_rsi_demo
from moneylab.service import Lab
from tiingo_util import FakeTiingo, payloads_from_csv

START = "2023-03-31"


def test_paper_advance_fetches_and_ingests(lab: Lab) -> None:
    demo = run_rsi_demo(lab)
    pid = lab.start_paper(demo["original"], START, name="advance")
    fake = FakeTiingo(payloads_from_csv())
    out = lab.paper_advance(pid, end=date(2023, 4, 5), provider=fake.provider(None))
    assert out["after"] == START
    assert "2023-04-03" in out["sessions"]
    assert "2023-04-04" in out["sessions"]
    assert "2023-04-05" in out["sessions"]
    assert out["status"]["latest_processed"] == "2023-04-05"
    assert all(r["status"] == "accepted" for r in out["ingested"])
    # Re-advance is idempotent: duplicates, no new processed sessions.
    again = lab.paper_advance(pid, end=date(2023, 4, 5), provider=fake.provider(None))
    assert again["status"]["latest_processed"] == "2023-04-05"
    assert all(r["status"] == "duplicate" for r in again["ingested"])
    assert lab.paper_replay(pid)["match"]


def test_paper_advance_empty_when_no_newer_sessions(lab: Lab) -> None:
    demo = run_rsi_demo(lab)
    pid = lab.start_paper(demo["original"], START, name="empty")
    fake = FakeTiingo(payloads_from_csv())
    out = lab.paper_advance(pid, end=date(2023, 3, 31), provider=fake.provider(None))
    assert out["sessions"] == []
    assert out["ingested"] == []
    assert out["status"]["latest_processed"] is None


def test_paper_advance_refuses_corporate_actions(lab: Lab) -> None:
    demo = run_rsi_demo(lab)
    pid = lab.start_paper(demo["original"], START, name="actions")

    class ActionProvider:
        name = "fake"

        def fetch_sessions(self, symbols, after, end=None):
            bars = pd.DataFrame(
                {
                    "date": ["2023-04-03"],
                    "symbol": ["AAA"],
                    "open": [70.0],
                    "high": [71.0],
                    "low": [69.0],
                    "close": [70.5],
                    "volume": [1000],
                }
            )
            actions = pd.DataFrame(
                {
                    "date": ["2023-04-03"],
                    "symbol": ["DDD"],
                    "type": ["split"],
                    "value": [2.0],
                }
            )
            return ProviderHistory(bars, actions, {"provider": self.name})

        def fetch_history(self, symbols, start, end, *, refresh=False):
            return ProviderHistory(
                pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "volume"]),
                empty_actions(),
                {"provider": self.name},
            )

    with pytest.raises(ValueError, match="corporate actions"):
        lab.paper_advance(pid, end=date(2023, 4, 5), provider=ActionProvider())  # type: ignore[arg-type]


def test_cli_paper_advance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from moneylab.cli import app

    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    result = CliRunner().invoke(app, ["paper", "advance", "paper-missing"])
    assert result.exit_code != 0
