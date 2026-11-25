"""MACD: hand-checked values, warm-up, gaps, causality, validation and a saved run.

MACD is added as one new file (src/moneylab/components/macd.py); these tests check it
works through the existing config resolution, simulator, store and reopen path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from moneylab.components import get_component, list_components
from moneylab.demo import FIXTURE, UNIVERSE
from moneylab.engine.config import (
    Condition,
    IndicatorRef,
    StrategyConfig,
    config_from_resolved,
    resolve,
)
from moneylab.service import Lab

ROOT = Path(__file__).resolve().parents[1]
SMALL = {"fast": 3, "slow": 7, "signal": 3}
CLOSES = [10.0, 12.0, 11.0, 13.0, 15.0, 14.0, 16.0, 18.0, 17.0, 20.0]


def _macd(closes: list[float], **params: object) -> pd.Series:
    decl = get_component("macd")
    bars = pd.DataFrame({"close": closes})
    return decl.compute(bars, decl.resolve_params(params))  # type: ignore[misc]


def _nan_count_then(values: pd.Series) -> int:
    return int(values.isna().to_numpy().argmin())


# Hand calculation, fast=3 (alpha 2/4 = 0.5), slow=7 (alpha 2/8 = 0.25), signal=3 (0.5).
# closes: idx 0..9 = 10, 12, 11, 13, 15, 14, 16, 18, 17, 20
# EMA3 seed at idx 2 = (10+12+11)/3 = 11
#   idx3 = 11 + .5(13-11) = 12;  idx4 = 12 + .5(15-12) = 13.5;  idx5 = 13.5 + .5(14-13.5) = 13.75
#   idx6 = 13.75 + .5(16-13.75) = 14.875;  idx7 = 14.875 + .5(18-14.875) = 16.4375
#   idx8 = 16.4375 + .5(17-16.4375) = 16.71875;  idx9 = 16.71875 + .5(20-16.71875) = 18.359375
# EMA7 seed at idx 6 = (10+12+11+13+15+14+16)/7 = 91/7 = 13
#   idx7 = 13 + .25(18-13) = 14.25;  idx8 = 14.25 + .25(17-14.25) = 14.9375
#   idx9 = 14.9375 + .25(20-14.9375) = 16.203125
# MACD line (first at slow-1 = 6):
#   idx6 = 14.875-13 = 1.875;  idx7 = 16.4375-14.25 = 2.1875
#   idx8 = 16.71875-14.9375 = 1.78125;  idx9 = 18.359375-16.203125 = 2.15625
# Signal = EMA3 of the MACD line, seed at idx 8 (slow+signal-2) = mean(1.875, 2.1875, 1.78125)
#   = 5.84375/3 = 1.9479166..;  idx9 = 1.9479166.. + .5(2.15625-1.9479166..) = 2.0520833..
# Histogram: idx8 = 1.78125-1.9479166.. = -0.1666666..;  idx9 = 2.15625-2.0520833.. = 0.1041666..
NAN = float("nan")
EXPECTED = {
    "macd": [NAN] * 6 + [1.875, 2.1875, 1.78125, 2.15625],
    "signal": [NAN] * 8 + [5.84375 / 3, 5.84375 / 6 + 2.15625 / 2],
    "histogram": [NAN] * 8 + [1.78125 - 5.84375 / 3, 2.15625 / 2 - 5.84375 / 6],
}


@pytest.mark.parametrize("output", ["macd", "signal", "histogram"])
def test_hand_checked_values(output: str) -> None:
    got = _macd(CLOSES, **SMALL, output=output).to_numpy()
    np.testing.assert_allclose(got, EXPECTED[output], rtol=0, atol=1e-12)


def test_hand_values_are_the_documented_ones() -> None:
    assert EXPECTED["signal"][8] == pytest.approx(1.9479166666666667)
    assert EXPECTED["histogram"][8] == pytest.approx(-1 / 6)
    assert EXPECTED["histogram"][9] == pytest.approx(0.1041666666666667)


def test_default_warmup_is_25_for_the_line_and_33_for_signal_and_histogram() -> None:
    closes = list(100 + np.sin(np.arange(60) / 3.0) * 5)
    assert _nan_count_then(_macd(closes, output="macd")) == 25
    assert _nan_count_then(_macd(closes, output="signal")) == 33
    assert _nan_count_then(_macd(closes, output="histogram")) == 33
    assert not _macd(closes).iloc[33:].isna().any()


def test_matches_independent_pandas_ewm_once_seeded() -> None:
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 1, 120))
    got = _macd(list(closes), output="macd").to_numpy()

    def ema(x: pd.Series, n: int) -> pd.Series:
        # Seeded with the mean of the first n values, then the recursive EMA.
        seeded = x.copy()
        seeded.iloc[: n - 1] = np.nan
        seeded.iloc[n - 1] = x.iloc[:n].mean()
        return seeded.iloc[n - 1 :].ewm(alpha=2 / (n + 1), adjust=False).mean()

    s = pd.Series(closes)
    line = (ema(s, 12) - ema(s, 26)).reindex(s.index)
    np.testing.assert_allclose(got, line.to_numpy(), rtol=0, atol=1e-9)


def test_causal_prefix_values_do_not_change_when_future_bars_arrive() -> None:
    rng = np.random.default_rng(1)
    closes = list(100 + np.cumsum(rng.normal(0, 1, 80)))
    full = _macd(closes).to_numpy()
    for cut in (34, 50, 79):
        np.testing.assert_array_equal(_macd(closes[:cut]).to_numpy(), full[:cut])


def test_missing_bar_restarts_warmup() -> None:
    rng = np.random.default_rng(3)
    closes = list(100 + np.cumsum(rng.normal(0, 1, 60)))
    gapped = [*closes[:20], NAN, *closes[21:]]
    got = _macd(gapped, **SMALL, output="histogram").to_numpy()
    assert np.isnan(got[20])
    # After the gap the series is exactly MACD of the post-gap run on its own.
    np.testing.assert_array_equal(
        got[21:], _macd(closes[21:], **SMALL, output="histogram").to_numpy()
    )
    # ... so the warm-up restarts: slow+signal-2 = 8 NaNs after the gap.
    assert np.isnan(got[21:29]).all() and not np.isnan(got[29])
    # Before the gap, values are the same as without the gap.
    np.testing.assert_array_equal(
        got[:20], _macd(closes[:20], **SMALL, output="histogram").to_numpy()
    )


def test_short_history_is_all_nan() -> None:
    assert _macd(CLOSES[:6], **SMALL, output="macd").isna().all()
    assert _macd(CLOSES[:8], **SMALL).isna().all()


# Validation through config resolution ---------------------------------------------


def _config(**macd_params: object) -> StrategyConfig:
    return StrategyConfig(
        name="SMA20 cross + MACD histogram > 0",
        universe=UNIVERSE,
        benchmark="SPY",
        timeframe="1d",
        entry=[
            Condition(
                kind="price_cross",
                indicator=IndicatorRef(id="sma", params={"length": 20}),
                params={"direction": "above"},
            ),
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="macd", params=dict(macd_params)),
                params={"op": "above", "value": 0.0},
            ),
        ],
    )


def test_registered_and_declared() -> None:
    decl = get_component("macd")
    assert decl in list_components("indicator")
    assert (decl.id, decl.version) == ("macd", "1.0.0")
    assert [p.name for p in decl.params] == ["fast", "slow", "signal", "output"]
    assert decl.param("output").choices == ("macd", "signal", "histogram")


def test_defaults_are_filled_by_resolve() -> None:
    ind = resolve(_config())["entry"][1]["indicator"]
    assert ind == {
        "id": "macd",
        "version": "1.0.0",
        "params": {"fast": 12, "slow": 26, "signal": 9, "output": "histogram"},
    }


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"fast": 0}, "fast must be >= 1"),
        ({"slow": 401}, "slow must be <= 400"),
        ({"signal": 2.5}, "signal must be a whole number"),
        ({"output": "line"}, "output must be one of"),
        ({"fast": 26, "slow": 26}, "fast (26) must be shorter than slow (26)"),
        ({"fast": 30}, "fast (30) must be shorter than slow (26)"),
        ({"length": 14}, "unknown parameter"),
    ],
)
def test_invalid_params_are_rejected_before_execution(
    params: dict[str, object], message: str, lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = lab.import_file(FIXTURE)

    def boom(*_: object, **__: object) -> None:
        raise AssertionError("simulator must not run on an invalid config")

    monkeypatch.setattr("moneylab.engine.batch.simulate", boom)
    with pytest.raises(ValueError, match=re.escape(message)):
        resolve(_config(**params))
    with pytest.raises(ValueError, match=re.escape(message)):
        lab.run(_config(**params), snap.id)
    assert lab.store.list_runs() == []


# Historical run, persistence and reopen ------------------------------------------


def test_historical_run_persists_and_reopens(lab: Lab) -> None:
    snap = lab.import_file(FIXTURE)
    run_id = lab.run(_config(fast=8, signal=5), snap.id)

    saved = lab.store.get(run_id)
    ind = saved["resolved"]["entry"][1]["indicator"]
    assert ind == {
        "id": "macd",
        "version": "1.0.0",
        "params": {"fast": 8, "slow": 26, "signal": 5, "output": "histogram"},
    }
    led = lab.store.ledgers(run_id)
    assert len(led.fills) > 0
    # Every entry decision recorded the MACD value it saw, and it was above 0.
    enters = led.decisions[led.decisions["action"] == "enter"]
    assert len(enters) > 0
    key = "macd(fast=8,output=histogram,signal=5,slow=26)"
    assert all(vals[key] > 0 for vals in enters["values"])
    # Warm-up decisions exist before the histogram is defined.
    assert (led.decisions["reason"] == "warmup").any()

    # Reopen: a fresh Lab on the same home sees the same run, and it rebuilds an
    # editable config that resolves back to the same settings.
    again = Lab.open(lab.settings.home)
    reopened = again.store.get(run_id)
    assert reopened["resolved"] == saved["resolved"]
    rebuilt = resolve(config_from_resolved(reopened["resolved"]), again.settings)
    assert rebuilt["entry"] == saved["resolved"]["entry"]

    # An unchanged duplicate reproduces the ledger exactly.
    dup = again.duplicate(run_id)
    pd.testing.assert_frame_equal(
        again.store.ledgers(dup).fills.drop(columns=["decision_id"]),
        led.fills.drop(columns=["decision_id"]),
    )


# Zero edits outside the component folder -----------------------------------------


def test_macd_commit_only_adds_new_files() -> None:
    """The commit that introduced macd.py added files and modified none."""
    try:
        sha = subprocess.run(
            [
                "git",
                "log",
                "--diff-filter=A",
                "--format=%H",
                "--",
                "src/moneylab/components/macd.py",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        if not sha:
            pytest.skip("macd.py commit not in history")
        out = subprocess.run(
            ["git", "show", "--name-status", "--format=", sha[-1]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        pytest.skip(f"git history unavailable: {e}")
    rows = [line.split("\t") for line in out.strip().splitlines()]
    assert rows and all(status == "A" for status, *_ in rows), rows
    paths = {path for _, path in rows}
    assert "src/moneylab/components/macd.py" in paths
    assert not any(
        p.startswith(("src/moneylab/dashboard", "src/moneylab/engine", "src/moneylab/store"))
        for p in paths
    )
