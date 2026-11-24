"""Breadth filter: fraction of eligible universe symbols above their own SMA."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from moneylab.components import get_component
from moneylab.demo import FIXTURE, UNIVERSE
from moneylab.engine.config import Condition, IndicatorRef, StrategyConfig, resolve
from moneylab.service import Lab

NAN = float("nan")


def _breadth(closes: dict[str, list[float]], length: int) -> np.ndarray:
    decl = get_component("breadth")
    assert decl.compute_universe is not None
    frames = {s: pd.DataFrame({"close": c}) for s, c in closes.items()}
    return decl.compute_universe(frames, decl.resolve_params({"length": length})).to_numpy()


def test_hand_checked_values_with_late_listing_and_gap() -> None:
    # SMA2. X: 1,2,3,2,1 -> sma -,1.5,2.5,2.5,1.5 -> above: -,T,T,F,F
    #       Y: listed from idx 2 (NaN before): 5,6,4 -> sma -,-,-,5.5,5 -> above: -,-,-,T,F
    #       Z: gap at idx 3: 4,3,5,NaN,6 -> sma -,3.5,4,-,- -> above: -,F,T,-,-
    got = _breadth(
        {
            "X": [1, 2, 3, 2, 1],
            "Y": [NAN, NAN, 5, 6, 4],
            "Z": [4, 3, 5, NAN, 6],
        },
        2,
    )
    np.testing.assert_allclose(got, [NAN, 1 / 2, 2 / 2, 1 / 2, 0 / 1], equal_nan=True)


def test_causal_and_bounded() -> None:
    rng = np.random.default_rng(5)
    closes = {s: list(100 + np.cumsum(rng.normal(0, 1, 90))) for s in "ABCDE"}
    full = _breadth(closes, 10)
    assert np.nanmin(full) >= 0 and np.nanmax(full) <= 1
    for cut in (11, 40, 89):
        part = _breadth({s: c[:cut] for s, c in closes.items()}, 10)
        np.testing.assert_array_equal(part, full[:cut])


def _config(value: float, universe: list[str] = UNIVERSE) -> StrategyConfig:
    return StrategyConfig(
        name="SMA20 cross + breadth filter",
        universe=universe,
        benchmark="SPY",
        entry=[
            Condition(
                kind="price_cross",
                indicator=IndicatorRef(id="sma", params={"length": 20}),
                params={"direction": "above"},
            ),
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="breadth", params={"length": 20}),
                params={"op": "above", "value": value},
            ),
        ],
    )


def test_breadth_filter_in_a_run_uses_the_universe_and_records_its_value(lab: Lab) -> None:
    snap = lab.import_file(FIXTURE)
    loose = lab.run(_config(-1.0), snap.id)  # always passes once defined
    strict = lab.run(_config(1.0), snap.id)  # never passes (needs > 100%)
    key = "breadth(length=20)"

    dec = lab.store.ledgers(loose).decisions
    enters = dec[dec["action"] == "enter"]
    assert len(enters) > 0
    # One shared value per session: every symbol saw the same breadth that day.
    vals = dec[dec["values"].map(lambda v: isinstance(v, dict) and key in v)]
    per_session = vals.groupby("session")["values"].apply(lambda s: {v[key] for v in s})
    assert per_session.map(len).max() == 1
    assert len(lab.store.ledgers(strict).fills) == 0

    # Recomputed by hand from the snapshot's closes for one entry session.
    data = lab.market_data(snap.id, UNIVERSE, None, None)
    row = enters.iloc[0]
    i = data.sessions.get_loc(pd.Timestamp(row["session"]))
    above = eligible = 0
    for s in UNIVERSE:
        c = data.frames[s]["close"].iloc[: i + 1]
        sma = c.iloc[-20:].mean() if c.iloc[-20:].notna().sum() == 20 else NAN
        if not np.isnan(c.iloc[-1]) and not np.isnan(sma):
            eligible += 1
            above += c.iloc[-1] > sma
    assert row["values"][key] == pytest.approx(above / eligible)


def test_price_cross_rejects_indicators_not_in_price_units() -> None:
    for ind in ("rsi", "macd", "breadth"):
        cfg = StrategyConfig(
            name="bad",
            universe=UNIVERSE,
            benchmark="SPY",
            entry=[
                Condition(
                    kind="price_cross",
                    indicator=IndicatorRef(id=ind),
                    params={"direction": "above"},
                )
            ],
        )
        with pytest.raises(ValueError, match="not in price units"):
            resolve(cfg)


def test_dashboard_offers_breadth_for_thresholds_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONEYLAB_HOME", str(tmp_path / "home"))
    app = Path(__file__).resolve().parents[1] / "src" / "moneylab" / "dashboard" / "app.py"
    at = AppTest.from_file(str(app), default_timeout=60)
    at.run()
    at.sidebar.radio(key="page").set_value("Data").run()
    at.text_input(key="data.path").set_value(str(FIXTURE)).run()
    at.button(key="data.import").click().run()
    at.sidebar.radio(key="page").set_value("Strategy builder").run()
    rev = int(at.session_state["draft_rev"])
    assert at.selectbox(key=f"b{rev}.entry0.ind").options == ["Simple moving average"]
    at.button(key=f"b{rev}.entry.add").click().run()
    rev = int(at.session_state["draft_rev"])
    assert "Breadth (fraction above SMA)" in at.selectbox(key=f"b{rev}.entry1.ind").options
    at.selectbox(key=f"b{rev}.entry1.ind").set_value("breadth").run()
    assert not at.exception, at.exception
    rev = int(at.session_state["draft_rev"])
    assert at.number_input(key=f"b{rev}.entry1.ind.length").value == 50
