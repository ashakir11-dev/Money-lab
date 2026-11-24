"""Generate the deterministic capacity dataset: 250 stocks plus SPY over five years.

Each stock is a seeded geometric random walk over XNYS sessions from 2019-01-02 to
2023-12-29 (about 1,258 sessions) with its own drift and daily volatility (1% to 3%),
slow volatility regimes and occasional jumps, so SMA crosses and RSI extremes fire
regularly. A few bars are removed (missing bars) and a few symbols list late, so the
engine's missing-bar and not-yet-listed paths are exercised at scale.

The CSV is written in the file-import format under ``var/`` (git-ignored) and is
byte-for-byte reproducible. ``--import`` also imports it as a snapshot.

Run: ``uv run python scripts/make_capacity_dataset.py [--out PATH] [--import]``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "var" / "capacity" / "capacity_250x5y.csv"
START, END = "2019-01-02", "2023-12-29"
N_STOCKS = 250
SEED = 20260101


def symbols() -> list[str]:
    return [f"S{i:03d}" for i in range(1, N_STOCKS + 1)]


def _series(rng: np.random.Generator, n: int, start: float, drift: float, vol: float) -> np.ndarray:
    # Volatility regimes: a smooth multiplier between ~0.6x and ~1.6x.
    regime = np.exp(0.25 * np.sin(np.arange(n) / rng.uniform(40, 120) + rng.uniform(0, 6)))
    rets = rng.normal(drift, vol, n) * regime
    jumps = rng.random(n) < 0.004
    rets[jumps] += rng.normal(0, 4 * vol, int(jumps.sum()))
    return np.asarray(start * np.exp(np.cumsum(rets)))


def _frame(
    rng: np.random.Generator, sym: str, sessions: pd.DatetimeIndex, close: np.ndarray
) -> pd.DataFrame:
    n = close.size
    gap = rng.normal(0, 0.004, n)
    opens = np.concatenate([[close[0]], close[:-1] * (1 + gap[1:])])
    wiggle = np.abs(rng.normal(0, 0.006, n))
    hi = np.maximum(opens, close) * (1 + wiggle)
    lo = np.minimum(opens, close) * (1 - wiggle)
    return pd.DataFrame(
        {
            "date": sessions.strftime("%Y-%m-%d"),
            "symbol": sym,
            "open": opens.round(2),
            "high": hi.round(2),
            "low": lo.round(2),
            "close": close.round(2),
            "volume": rng.integers(200_000, 5_000_000, n),
        }
    )


def build() -> pd.DataFrame:
    cal = xcals.get_calendar("XNYS")
    sessions = pd.DatetimeIndex(cal.sessions_in_range(START, END)).tz_localize(None)
    n = len(sessions)
    rng = np.random.default_rng(SEED)
    parts = []
    for k, sym in enumerate(symbols()):
        vol = rng.uniform(0.01, 0.03)
        drift = rng.normal(0.0003, 0.0004)
        start = float(np.round(rng.uniform(10, 400), 2))
        f = _frame(rng, sym, sessions, _series(rng, n, start, drift, vol))
        keep = np.ones(n, dtype=bool)
        if k % 50 == 7:  # five late listings
            keep[: int(rng.integers(100, 600))] = False
        if k % 10 == 3:  # a few missing bars on every tenth symbol
            holes = rng.choice(np.arange(30, n), size=int(rng.integers(1, 4)), replace=False)
            keep[holes] = False
        parts.append(f[keep])
    spy = _frame(rng, "SPY", sessions, _series(rng, n, 250.0, 0.0004, 0.011))
    parts.append(spy)
    return pd.concat(parts).sort_values(["symbol", "date"]).reset_index(drop=True)


def write(out: Path = DEFAULT_OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    build().to_csv(out, index=False)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--home", type=Path, default=None, help="MONEYLAB_HOME for --import")
    args = ap.parse_args()
    path = write(args.out)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")
    if args.do_import:
        from moneylab.service import Lab

        snap = Lab.open(args.home).import_file(path, label="capacity 250x5y")
        print(f"snapshot {snap.id}: {snap.meta['rows']} rows")


if __name__ == "__main__":
    main()
