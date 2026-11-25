"""Chart slice for one fill."""

from __future__ import annotations

BEFORE = 80
AFTER = 40


def chart_window(
    n: int, signal_i: int, fill_i: int, *, before: int = BEFORE, after: int = AFTER
) -> tuple[int, int]:
    """Inclusive indexes. 80 bars before the signal and 40 after the fill.

    Both indexes stay inside the slice. The ends clamp to the grid.
    """
    if n <= 0:
        raise ValueError("run has no bars")
    start = max(0, signal_i - before)
    end = min(n - 1, fill_i + after)
    start = min(start, signal_i, fill_i)
    end = max(end, signal_i, fill_i)
    return max(0, start), min(n - 1, end)
