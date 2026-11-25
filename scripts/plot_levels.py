"""Draw close, support, and resistance for a snapshot.

One SVG per symbol, so the default move size can be judged on real prices.
No extra dependency. Not part of the test suite.

    uv run python scripts/plot_levels.py SNAPSHOT_ID --symbols AAPL SPY
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

from moneylab.components import get_component
from moneylab.data.snapshot import MarketData, load_snapshot
from moneylab.settings import get_settings


def chart_svg(close: np.ndarray, support: np.ndarray, resistance: np.ndarray) -> str:
    """An SVG of the three lines. Missing values break the line."""
    finite = [
        float(value)
        for series in (close, support, resistance)
        for value in series
        if math.isfinite(float(value))
    ]
    width, height = 960, 420
    left, right, top, bottom = 48, 16, 28, 36
    if not finite:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            '<text x="48" y="40">No prices</text></svg>'
        )
    low = min(finite)
    high = max(finite)
    if high == low:
        high = low + 1.0
    pad = (high - low) * 0.06
    low -= pad
    high += pad
    plot_w = width - left - right
    plot_h = height - top - bottom
    count = max(len(close), 2)

    def xy(index: int, price: float) -> tuple[float, float]:
        x = left + plot_w * index / (count - 1)
        y = top + plot_h * (high - price) / (high - low)
        return x, y

    def lines(series: np.ndarray, color: str) -> str:
        chunks: list[list[str]] = []
        chunk: list[str] = []
        for index, value in enumerate(series):
            if not math.isfinite(float(value)):
                if chunk:
                    chunks.append(chunk)
                    chunk = []
                continue
            x, y = xy(index, float(value))
            chunk.append(f"{x:.1f},{y:.1f}")
        if chunk:
            chunks.append(chunk)
        rendered: list[str] = []
        for points in chunks:
            joined = " ".join(points)
            rendered.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{joined}"/>'
            )
        return "".join(rendered)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#fff"/>'
        f"{lines(support, '#2a9d8f')}{lines(resistance, '#e76f51')}{lines(close, '#1d3557')}"
        f'<text x="{left}" y="18" font-size="12" fill="#1d3557">close</text>'
        f'<text x="{left + 48}" y="18" font-size="12" fill="#2a9d8f">support</text>'
        f'<text x="{left + 120}" y="18" font-size="12" fill="#e76f51">resistance</text>'
        f'<text x="{left}" y="{height - 12}" font-size="12" fill="#333">'
        f"{low + pad:.2f} to {high - pad:.2f}</text>"
        "</svg>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_id")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--out", type=Path, default=Path("var/level-charts"))
    parser.add_argument("--min-move", type=float, default=2.0)
    parser.add_argument("--zone", type=float, default=1.0)
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--atr-length", type=int, default=14)
    args = parser.parse_args()

    settings = get_settings()
    snapshot = load_snapshot(args.snapshot_id, settings.snapshot_dir)
    symbols = list(args.symbols or snapshot.meta.get("symbols") or [])
    if not symbols:
        raise SystemExit(f"snapshot {args.snapshot_id} has no symbols")
    timeframe = str(snapshot.meta.get("timeframe") or "1d")
    data = MarketData.build(snapshot.bars, symbols, timeframe=timeframe)
    resolved = get_component("support").resolve_params(
        {
            "min_move": args.min_move,
            "zone": args.zone,
            "lookback": args.lookback,
            "atr_length": args.atr_length,
        }
    )
    support_fn = get_component("support").compute
    resistance_fn = get_component("resistance").compute
    assert support_fn is not None and resistance_fn is not None
    args.out.mkdir(parents=True, exist_ok=True)
    for symbol in symbols:
        frame = data.frames[symbol]
        support = support_fn(frame, resolved).to_numpy(dtype=float)
        resistance = resistance_fn(frame, resolved).to_numpy(dtype=float)
        close = frame["close"].to_numpy(dtype=float)
        path = args.out / f"{symbol}.svg"
        path.write_text(chart_svg(close, support, resistance))
        print(path)


if __name__ == "__main__":
    main()
