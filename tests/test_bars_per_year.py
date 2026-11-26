"""Bars per year from the bar size, used for Sharpe and annualised indicator outputs."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from moneylab.data.bars import bars_per_session, bars_per_year, timeframe_of_index
from moneylab.engine.reporting import metrics


@pytest.mark.parametrize(
    ("tf", "per_day"),
    # Grid bars overlapping 09:30-16:00: 1h is 09:00..15:00, 30min is 09:30..15:30.
    [("1d", 1), ("1h", 7), ("30min", 13), ("15min", 26), ("5min", 78), ("1min", 390)],
)
def test_bars_per_session_and_year(tf: str, per_day: int) -> None:
    assert bars_per_session(tf) == per_day
    assert bars_per_year(tf) == 252 * per_day


def test_timeframe_of_index() -> None:
    assert timeframe_of_index(pd.DatetimeIndex(["2024-01-02", "2024-01-03"])) == "1d"
    assert timeframe_of_index(pd.RangeIndex(5)) == "1d"
    hourly = pd.DatetimeIndex(["2024-01-02 15:00", "2024-01-03 09:00", "2024-01-03 10:00"])
    assert timeframe_of_index(hourly) == "1h"
    five = pd.DatetimeIndex(["2024-01-02 09:30", "2024-01-02 09:35"])
    assert timeframe_of_index(five) == "5min"


def test_sharpe_uses_bars_per_year() -> None:
    equity = pd.DataFrame({"equity": [100.0, 101.0, 100.5, 102.0, 101.0]})
    equity["positions_value"] = 0.0
    fills = pd.DataFrame(columns=["side"])
    e = equity["equity"].to_numpy()
    r = np.diff(e) / e[:-1]
    per_bar = r.mean() / r.std(ddof=1)
    daily = metrics(equity, fills)
    hourly = metrics(equity, fills, timeframe="1h")
    assert daily["sharpe"] == pytest.approx(per_bar * math.sqrt(252))
    assert hourly["sharpe"] == pytest.approx(per_bar * math.sqrt(1764))
    assert "sqrt(252)" in daily["sharpe_assumptions"]
    assert "sqrt(1764)" in hourly["sharpe_assumptions"]


def test_a_config_without_a_bar_size_takes_the_snapshots(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from moneylab.demo import rsi_strategy
    from moneylab.engine.config import resolve
    from moneylab.service import Lab
    from moneylab.settings import Settings

    cfg = rsi_strategy(30).model_copy(update={"timeframe": None})
    assert cfg.timeframe is None
    assert resolve(cfg, Settings(_env_file=None))["timeframe"] == "1d"
    lab = Lab.open(tmp_path / "home")
    snap = lab.import_file(Path(__file__).parents[1] / "fixtures" / "data" / "rsi_divergence.csv")
    assert lab.with_snapshot_timeframe(cfg, snap.id).timeframe == "1d"
    hourly = cfg.model_copy(update={"timeframe": "1h"})
    assert lab.with_snapshot_timeframe(hourly, snap.id).timeframe == "1h"
