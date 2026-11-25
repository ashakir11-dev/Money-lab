"""Trade-chart window and payload."""

from __future__ import annotations

import pytest

from moneylab.engine.chart import chart_window


def test_window_in_the_middle() -> None:
    assert chart_window(400, 100, 250) == (20, 290)


def test_window_clamps_the_start() -> None:
    assert chart_window(400, 10, 11) == (0, 51)


def test_window_clamps_the_end() -> None:
    assert chart_window(100, 90, 95) == (10, 99)


def test_window_keeps_a_long_wait() -> None:
    start, end = chart_window(400, 100, 250)
    assert start <= 100 <= end
    assert start <= 250 <= end
    assert end - start + 1 > 80 + 40


def test_window_keeps_a_signal_after_the_fill() -> None:
    start, end = chart_window(200, 150, 10)
    assert start <= 10 <= end
    assert start <= 150 <= end


def test_window_rejects_an_empty_grid() -> None:
    with pytest.raises(ValueError, match="no bars"):
        chart_window(0, 0, 0)
