"""One causality check over every registered indicator.

For each indicator, at default parameters (and at small ones so warm-ups end inside
the sample): the value at bar t computed on data truncated after t equals the value
computed on the full data, and rewriting every later bar never changes earlier values.
A new indicator is covered as soon as it is registered.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from moneylab.components import get_component, list_components

INDICATORS = sorted(d.id for d in list_components("indicator"))
N = 160
CUTS = (12, 45, 90, 131, N - 1)


def frame(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, N))
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.3, N),
            "high": close + rng.uniform(0.1, 2, N),
            "low": close - rng.uniform(0.1, 2, N),
            "close": close,
            "volume": rng.integers(1_000, 10_000, N).astype(float),
        },
        index=pd.date_range("2024-01-02", periods=N, freq="B"),
    )


def small(ind: str) -> dict[str, Any]:
    """Defaults, with every bar-count parameter shrunk so values appear early."""
    decl = get_component(ind)
    out: dict[str, Any] = {}
    for p in decl.params:
        if p.type == "int" and p.name in {
            "length",
            "fast",
            "slow",
            "signal",
            "lookback",
            "atr_length",
        }:
            lo = int(p.min or 1)
            out[p.name] = max(lo, min(int(p.default), 20))
    if ind == "macd":
        out.update({"fast": 5, "slow": 12, "signal": 4})
    return decl.resolve_params(out)


_CONTEXT = {
    "stock_minus_sector": ("sector_close",),
    "sector_minus_spy": ("sector_close", "benchmark_close"),
    "stock_minus_spy": ("benchmark_close",),
    "vix_level": ("vix_close",),
    "vix_change_1": ("vix_close",),
    "vix_change_5": ("vix_close",),
    "vix_percentile_252": ("vix_close",),
}


def compute(ind: str, data: pd.DataFrame, params: dict[str, Any]) -> np.ndarray:
    decl = get_component(ind)
    prepared = data.copy()
    for offset, column in enumerate(_CONTEXT.get(ind, ())):
        # A supplied series, not a stand-in invented inside the indicator.
        prepared[column] = data["close"] * (1.1 + 0.05 * offset)
    if decl.compute_universe is not None:
        other = prepared * 1.01
        return decl.compute_universe({"A": prepared, "B": other}, params).to_numpy(dtype=float)
    assert decl.compute is not None
    return decl.compute(prepared, params).to_numpy(dtype=float)


@pytest.mark.parametrize("ind", INDICATORS)
@pytest.mark.parametrize("which", ["defaults", "small"])
def test_no_lookahead(ind: str, which: str) -> None:
    params = get_component(ind).resolve_params({}) if which == "defaults" else small(ind)
    data = frame(11)
    full = compute(ind, data, params)
    assert full.shape == (N,)
    for cut in CUTS:
        head = compute(ind, data.iloc[: cut + 1], params)
        np.testing.assert_array_equal(head, full[: cut + 1], err_msg=f"{ind} truncated at {cut}")
        changed = data.copy()
        changed.iloc[cut + 1 :] = changed.iloc[cut + 1 :].to_numpy() * 2.5
        np.testing.assert_array_equal(
            compute(ind, changed, params)[: cut + 1],
            full[: cut + 1],
            err_msg=f"{ind} future at {cut}",
        )


def test_every_indicator_is_checked_and_most_produce_values() -> None:
    assert len(INDICATORS) >= 20
    silent = [i for i in INDICATORS if np.isnan(compute(i, frame(3), small(i))).all()]
    assert silent == [], silent
