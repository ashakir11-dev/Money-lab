from __future__ import annotations

from pathlib import Path

import pytest

from moneylab.demo import run_rsi_demo
from moneylab.service import Lab


@pytest.fixture()
def lab(tmp_path: Path) -> Lab:
    return Lab.open(tmp_path / "home")


@pytest.fixture()
def demo(lab: Lab) -> dict:
    return run_rsi_demo(lab)
