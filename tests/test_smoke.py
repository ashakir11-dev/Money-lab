"""Smoke test: the package imports, components register and settings load."""

from __future__ import annotations

import moneylab
from moneylab.components import list_components
from moneylab.settings import Settings


def test_package_imports() -> None:
    assert moneylab.__version__


def test_builtin_components_registered() -> None:
    ids = {d.id for d in list_components()}
    assert {"sma", "rsi", "price_cross", "threshold", "holding_period"} <= ids
    assert {d.id for d in list_components("indicator")} >= {"sma", "rsi"}


def test_settings_defaults_match_spec() -> None:
    s = Settings(_env_file=None)
    assert (s.starting_cash, s.target_allocation, s.max_positions, s.cost_bps_per_side) == (
        100_000.0,
        0.10,
        10,
        5.0,
    )
