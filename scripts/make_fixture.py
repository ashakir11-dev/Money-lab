"""Generate the deterministic offline fixture used by the RSI divergence demo and tests.

``fixtures/data/rsi_divergence.csv`` holds XNYS sessions for the first half of 2023:

* ``AAA`` is hand-shaped: flat near 100, a three-session sell-off to about 70, a quiet
  base that drags its 20-session SMA down, then a single up session that closes above
  the SMA from below while RSI(14) sits between 30 and 35. A strategy that needs
  "RSI below 30" skips that cross; "RSI below 35" buys it.
* ``DDD`` has the same shape a month earlier with a smaller up session, so its cross
  has RSI(14) below 30 and both variants buy it.
* ``BBB`` and ``CCC`` are seeded random walks that give the account other activity.
* ``SPY`` is a seeded random walk used as the benchmark.

Run: ``uv run python scripts/make_fixture.py``. Output is byte-for-byte reproducible.
"""

from __future__ import annotations

from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "rsi_divergence.csv"
START, END = "2023-01-03", "2023-06-30"


def shaped_closes(n: int, crash_at: int, cross_up_pct: float) -> np.ndarray:
    """Flat, sharp sell-off, quiet base, then one up session that crosses the SMA20."""
    c = np.empty(n)
    k = crash_at
    c[:k] = 100 + 0.2 * np.sin(np.arange(k))  # quiet, RSI mid-range
    c[k : k + 3] = [91.0, 82.0, 73.0]  # sharp sell-off: large average loss
    # Quiet, slightly declining base: SMA20 falls toward price, average loss decays slowly.
    base = 73.0 - 0.05 * np.arange(1, 21) + 0.03 * np.where(np.arange(20) % 2 == 0, 1, -1)
    c[k + 3 : k + 23] = base
    c[k + 23] = c[k + 22] * (1 + cross_up_pct)  # the cross-up session
    rest = np.arange(n - k - 24)
    c[k + 24 :] = c[k + 23] * (1 + 0.004 * rest) + 0.3 * np.sin(rest / 2.0)  # recovery
    return c


def walk(seed: int, n: int, start: float, drift: float, vol: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, vol, n)))


def frame(symbol: str, sessions: pd.DatetimeIndex, close: np.ndarray, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1000)
    # Next open gaps a little from the previous close; first open equals first close.
    gap = rng.normal(0, 0.003, close.size)
    opens = np.concatenate([[close[0]], close[:-1] * (1 + gap[1:])])
    hi = np.maximum(opens, close) * 1.004
    lo = np.minimum(opens, close) * 0.996
    return pd.DataFrame(
        {
            "date": sessions.strftime("%Y-%m-%d"),
            "symbol": symbol,
            "open": opens.round(2),
            "high": hi.round(2),
            "low": lo.round(2),
            "close": close.round(2),
            "volume": rng.integers(500_000, 2_000_000, close.size),
        }
    )


def build() -> pd.DataFrame:
    cal = xcals.get_calendar("XNYS")
    sessions = pd.DatetimeIndex(cal.sessions_in_range(START, END)).tz_localize(None)
    n = len(sessions)
    parts = [
        # Cross-up on 2023-04-04 with RSI(14) ~31.68: only "RSI below 35" buys it.
        frame("AAA", sessions, shaped_closes(n, 40, 0.036), 1),
        # Cross-up on 2023-03-07 with RSI(14) ~24.89: both thresholds buy it.
        frame("DDD", sessions, shaped_closes(n, 20, 0.025), 5),
        frame("BBB", sessions, walk(2, n, 50.0, 0.0005, 0.015), 2),
        frame("CCC", sessions, walk(3, n, 120.0, 0.0003, 0.012), 3),
        frame("SPY", sessions, walk(4, n, 380.0, 0.0004, 0.009), 4),
    ]
    return pd.concat(parts).sort_values(["symbol", "date"]).reset_index(drop=True)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().to_csv(OUT, index=False)
    print(f"wrote {OUT}")
