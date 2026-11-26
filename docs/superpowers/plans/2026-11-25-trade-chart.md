# Trade Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking a buy or sell on the detailed report opens a dialog with that symbol's candlesticks around the fill, every indicator the strategy uses, and the stored signal.

**Architecture:** `chart_window` picks a deterministic slice. `assemble_chart` recomputes the run's indicators on the full bar grid with the existing indicator functions, then cuts the arrays to that slice. `GET /api/runs/{run_id}/chart` loads the run and returns that payload. The report dialog only draws it.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pandas, numpy, the existing indicator registry. Next.js, React, the generated OpenAPI client, hand-drawn SVG. No chart library.

## Global Constraints

- Slice is `start = max(0, signal_i - 80)`, `end = min(n - 1, fill_i + 40)`, then `start = min(start, signal_i, fill_i)` and `end = max(end, signal_i, fill_i)`. Counts are bars.
- The dialog draws that slice only. It does not pan or zoom.
- `GET /api/runs/{run_id}/chart?decision_id=` with `decision_id` required.
- Each `required_indicators` ref is one series under `indicator_key`. A MACD ref is only the output it names.
- `price_scale` comes from the indicator's registry declaration.
- A missing bar is `null` in `candles`. An undefined indicator value is `null`. Indicator numbers are rounded to 6 decimal places, the same rounding as stored decision values.
- A decision with no stored conditions returns `conditions: []` and `values: {}`.
- A recomputed value that differs from the stored value does not fail the request.
- Close with the Close button or Escape. Clicking the page behind the dialog does not close it.
- Opening moves focus to Close. Closing returns focus to the control that opened it.
- `close >= open` uses the up color. `close < open` uses the down color.
- A vertical line runs through every pane on the fill, with a filled circle at the fill price. The signal bar gets a diamond at its close and no vertical line. A missing close omits the diamond.
- A hold-period exit shows the stored reason and no conditions table.
- Unknown run, unknown decision, or a decision with no fill: 404 naming the id (`run {id} not found` or `decision {id} not found`). More than one fill for that id: 422 `decision {id} has more than one fill`. Missing snapshot: 404 naming the snapshot. No bars, a signal or fill session off the grid, or an indicator that fails: 422 with the exception text. No partial chart.
- Clicks exist only on the detailed report's Trades table and round-trip Entry and Exit. Compare and paper do not gain them.
- `make api-types` refreshes `frontend/openapi.json` and `frontend/src/lib/api-schema.ts`.

---

### Task 1: Slice function

**Files:**
- Create: `src/moneylab/engine/chart.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: nothing
- Produces: `chart_window(n: int, signal_i: int, fill_i: int, *, before: int = 80, after: int = 40) -> tuple[int, int]` and `BEFORE = 80`, `AFTER = 40` in `moneylab.engine.chart`. The tuple is inclusive.

- [ ] **Step 1: Write the failing test**

Create `tests/test_chart.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_chart.py -v`

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `moneylab.engine.chart`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/moneylab/engine/chart.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_chart.py -v`

Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/moneylab/engine/chart.py tests/test_chart.py
git commit -m "$(cat <<'EOF'
Add the deterministic window for a trade chart.

EOF
)"
```

---

### Task 2: Assemble one symbol's chart

**Files:**
- Modify: `src/moneylab/engine/chart.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: `chart_window` from Task 1. `required_indicators` from `moneylab.engine.config`. `indicator_key` and `get_component` from `moneylab.components.registry`. `session_label` from `moneylab.data.bars`. `MarketData` from `moneylab.data.snapshot`. `Lab.market_data` and `Lab.store` as the tests use them.
- Produces:
  - `class ChartRejected(ValueError)`
  - `assemble_chart(resolved: dict[str, Any], data: MarketData, decision: dict[str, Any], fill: dict[str, Any]) -> dict[str, Any]`
  - Return keys: `symbol`, `timeframe`, `sessions`, `candles`, `series`, `fill`, `signal`.
  - `series` items: `key: str`, `price_scale: bool`, `values: list[float | None]`.
  - `fill`: `session`, `side`, `price`.
  - `signal`: `session`, `action`, `reason`, `conditions`, `values`.
  - `ChartRejected` messages, verbatim:
    - `no data for {symbol} in snapshot`
    - `signal session {label} is not on the run's bar grid`
    - `fill session {label} is not on the run's bar grid`
    - any other compute failure: `str(exc)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chart.py`:

```python
from pathlib import Path
from typing import Any

from moneylab.demo import FIXTURE, rsi_strategy
from moneylab.engine.chart import ChartRejected, assemble_chart, chart_window
from moneylab.service import Lab


def _lab(tmp_path: Path) -> tuple[Lab, str]:
    lab = Lab.open(tmp_path / "home")
    snap = lab.import_file(FIXTURE, label="demo")
    return lab, lab.run(rsi_strategy(30), snap.id)


def _pieces(lab: Lab, run_id: str, fill: Any) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    full = lab.store.get(run_id)
    resolved = full["resolved"]
    data = lab.market_data(
        full["snapshot_id"],
        list(resolved["universe"]) + [resolved["benchmark"]],
        resolved["start"],
        resolved["end"],
    )
    did = int(fill["decision_id"])
    row = lab.store.ledgers(run_id).decisions
    row = row[row["decision_id"] == did].iloc[0].to_dict()
    return resolved, data, row


def test_buy_chart_matches_the_stored_rsi(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    resolved, data, decision = _pieces(lab, run_id, buy)
    chart = assemble_chart(resolved, data, decision, buy.to_dict())

    from moneylab.data.bars import session_label

    labels = [session_label(ts) for ts in data.sessions]
    signal_i = labels.index(chart["signal"]["session"])
    fill_i = labels.index(chart["fill"]["session"])
    start, end = chart_window(len(labels), signal_i, fill_i)
    assert chart["sessions"] == labels[start : end + 1]
    assert len(chart["candles"]) == len(chart["sessions"])
    assert chart["fill"]["session"] in chart["sessions"]
    assert chart["signal"]["session"] in chart["sessions"]
    by_key = {s["key"]: s for s in chart["series"]}
    assert by_key["sma(length=20)"]["price_scale"] is True
    assert by_key["rsi(length=14)"]["price_scale"] is False
    at = chart["sessions"].index(chart["signal"]["session"])
    assert by_key["rsi(length=14)"]["values"][at] == decision["values"]["rsi(length=14)"]
    assert len(by_key["rsi(length=14)"]["values"]) == len(chart["sessions"])


def test_holding_period_sell_has_no_conditions(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    sell = fills[fills["side"] == "sell"].iloc[0]
    resolved, data, decision = _pieces(lab, run_id, sell)
    chart = assemble_chart(resolved, data, decision, sell.to_dict())
    assert chart["signal"]["conditions"] == []
    assert chart["signal"]["values"] == {}
    assert chart["signal"]["reason"].startswith("held ")
    assert chart["fill"]["side"] == "sell"


def test_symbol_missing_from_the_grid_is_rejected(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0].to_dict()
    resolved, data, decision = _pieces(lab, run_id, buy)
    buy["symbol"] = "ZZZ"
    with pytest.raises(ChartRejected, match="no data for ZZZ in snapshot"):
        assemble_chart(resolved, data, decision, buy)


def test_signal_off_the_grid_is_rejected(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    resolved, data, decision = _pieces(lab, run_id, buy)
    decision["session"] = "1999-01-01"
    with pytest.raises(
        ChartRejected, match="signal session 1999-01-01 is not on the run's bar grid"
    ):
        assemble_chart(resolved, data, decision, buy.to_dict())
```

The first import block of the file already imports `pytest` and `chart_window`. Replace that import with the combined import in the snippet above (`ChartRejected`, `assemble_chart`, `chart_window`) so `chart_window` is imported once.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_chart.py -v`

Expected: the six window tests PASS. The four new tests FAIL with `ImportError` for `assemble_chart` or `ChartRejected`.

- [ ] **Step 3: Write the implementation**

Replace `src/moneylab/engine/chart.py` with:

```python
"""Chart slice for one fill.

Indicators are computed on the run's full bar grid, then cut to ``chart_window``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from moneylab.components.registry import get_component, indicator_key
from moneylab.data.bars import session_label
from moneylab.data.snapshot import MarketData
from moneylab.engine.config import required_indicators

BEFORE = 80
AFTER = 40


class ChartRejected(ValueError):
    """The fill exists, but the chart cannot be built. The API returns 422."""


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


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 6)


def _candle(row: Any) -> dict[str, float] | None:
    open_ = _finite(row["open"])
    high = _finite(row["high"])
    low = _finite(row["low"])
    close = _finite(row["close"])
    if open_ is None or high is None or low is None or close is None:
        return None
    return {"open": open_, "high": high, "low": low, "close": close}


def _indicator_order(resolved: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    ordered: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for ref in required_indicators(resolved):
        key = indicator_key(ref)
        if key in seen:
            continue
        seen.add(key)
        ordered.append((key, ref))
    return ordered


def _compute_symbol(
    data: MarketData,
    ordered: list[tuple[str, dict[str, Any]]],
    symbol: str,
    universe: list[str],
) -> dict[str, np.ndarray]:
    if symbol not in data.frames:
        raise ChartRejected(f"no data for {symbol} in snapshot")
    members = [name for name in universe if name in data.frames]
    out: dict[str, np.ndarray] = {}
    try:
        for key, ref in ordered:
            decl = get_component(str(ref["id"]))
            if decl.compute_universe is not None:
                frames = {name: data.frames[name] for name in members}
                series = decl.compute_universe(frames, ref["params"])
                out[key] = series.to_numpy(dtype=float)
                continue
            if decl.compute is None:
                raise ChartRejected(f"{ref['id']} has no compute function")
            series = decl.compute(data.frames[symbol], ref["params"])
            out[key] = series.to_numpy(dtype=float)
    except ChartRejected:
        raise
    except Exception as exc:
        raise ChartRejected(str(exc)) from exc
    return out


def assemble_chart(
    resolved: dict[str, Any],
    data: MarketData,
    decision: dict[str, Any],
    fill: dict[str, Any],
) -> dict[str, Any]:
    """Candles and indicator series for one fill, already sliced."""
    symbol = str(fill["symbol"])
    labels = [session_label(ts) for ts in data.sessions]
    signal_session = str(decision["session"])
    fill_session = str(fill["session"])
    if signal_session not in labels:
        raise ChartRejected(f"signal session {signal_session} is not on the run's bar grid")
    if fill_session not in labels:
        raise ChartRejected(f"fill session {fill_session} is not on the run's bar grid")
    signal_i = labels.index(signal_session)
    fill_i = labels.index(fill_session)
    start, end = chart_window(len(labels), signal_i, fill_i)
    window = slice(start, end + 1)
    ordered = _indicator_order(resolved)
    computed = _compute_symbol(data, ordered, symbol, list(resolved["universe"]))
    frame = data.frames[symbol]
    candles: list[dict[str, float] | None] = [
        _candle(frame.loc[ts]) if ts in frame.index else None for ts in data.sessions[window]
    ]
    series: list[dict[str, Any]] = []
    for key, ref in ordered:
        series.append(
            {
                "key": key,
                "price_scale": bool(get_component(str(ref["id"])).price_scale),
                "values": [_finite(value) for value in computed[key][window]],
            }
        )
    conditions = decision.get("conditions")
    values = decision.get("values")
    return {
        "symbol": symbol,
        "timeframe": str(resolved.get("timeframe") or "1d"),
        "sessions": labels[window],
        "candles": candles,
        "series": series,
        "fill": {
            "session": fill_session,
            "side": str(fill["side"]),
            "price": float(fill["price"]),
        },
        "signal": {
            "session": signal_session,
            "action": str(decision.get("action") or ""),
            "reason": str(decision.get("reason") or ""),
            "conditions": conditions if isinstance(conditions, list) else [],
            "values": values if isinstance(values, dict) else {},
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_chart.py -v`

Expected: PASS, 10 tests.

Run: `uv run ruff check src/moneylab/engine/chart.py tests/test_chart.py` and `uv run mypy src/moneylab/engine/chart.py`

Expected: both exit 0. If mypy reports that `frame.loc[ts]` is a `DataFrame`, index with `frame.loc[ts].iloc[0]` only when the result is a `DataFrame`; a `MarketData` frame index is unique, so prefer keeping `Series` access and a cast if mypy requires it.

- [ ] **Step 5: Commit**

```bash
git add src/moneylab/engine/chart.py tests/test_chart.py
git commit -m "$(cat <<'EOF'
Build a trade chart from the run's own indicators.

EOF
)"
```

---

### Task 3: Chart endpoint

**Files:**
- Modify: `src/moneylab/api/app.py` (imports near the top, response models after `FillRow`, route after `fills`)
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: `assemble_chart` and `ChartRejected` from Task 2. `lab.store.get`, `lab.store.ledgers`, `lab.market_data`, `lab.snapshot` via `FileNotFoundError`.
- Produces: `GET /api/runs/{run_id}/chart?decision_id={int}` response model `TradeChartOut`. Nested models `CandleOut`, `IndicatorSeriesOut`, `ChartFillOut`, `ChartSignalOut`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chart.py`:

```python
import pandas as pd
from fastapi.testclient import TestClient

from moneylab.api.app import create_app


def _client(lab: Lab, tmp_path: Path) -> TestClient:
    app = create_app(lab, web_dir=tmp_path / "no-web")
    return TestClient(app)


def test_chart_route_returns_the_buy(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    buy = fills[fills["side"] == "buy"].iloc[0]
    with _client(lab, tmp_path) as client:
        response = client.get(
            f"/api/runs/{run_id}/chart", params={"decision_id": int(buy["decision_id"])}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == buy["symbol"]
    assert body["fill"]["side"] == "buy"
    assert body["fill"]["session"] in body["sessions"]
    assert body["signal"]["session"] in body["sessions"]
    rsi = next(s for s in body["series"] if s["key"] == "rsi(length=14)")
    assert rsi["price_scale"] is False
    at = body["sessions"].index(body["signal"]["session"])
    stored = lab.store.ledgers(run_id).decisions
    stored = stored[stored["decision_id"] == int(buy["decision_id"])].iloc[0]
    assert rsi["values"][at] == stored["values"]["rsi(length=14)"]


def test_chart_route_unknown_run(tmp_path: Path) -> None:
    lab, _run_id = _lab(tmp_path)
    with _client(lab, tmp_path) as client:
        response = client.get("/api/runs/missing/chart", params={"decision_id": 1})
    assert response.status_code == 404
    assert response.json()["detail"] == "run missing not found"


def test_chart_route_unknown_decision(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": 999999})
    assert response.status_code == 404
    assert response.json()["detail"] == "decision 999999 not found"


def test_chart_route_decision_without_a_fill(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    led = lab.store.ledgers(run_id)
    filled = {int(v) for v in led.fills["decision_id"]}
    open_ids = [int(v) for v in led.decisions["decision_id"] if int(v) not in filled]
    assert open_ids, "the demo run should record a decision that does not fill"
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": open_ids[0]})
    assert response.status_code == 404
    assert response.json()["detail"] == f"decision {open_ids[0]} not found"


def test_chart_route_duplicate_fill(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    path = lab.store.run_dir(run_id) / "fills.parquet"
    fills = pd.read_parquet(path)
    did = int(fills.iloc[0]["decision_id"])
    pd.concat([fills, fills.iloc[[0]]], ignore_index=True).to_parquet(path, index=False)
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": did})
    assert response.status_code == 422
    assert response.json()["detail"] == f"decision {did} has more than one fill"


def test_chart_route_holding_period_sell(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    fills = lab.store.ledgers(run_id).fills
    sell = fills[fills["side"] == "sell"].iloc[0]
    with _client(lab, tmp_path) as client:
        response = client.get(
            f"/api/runs/{run_id}/chart", params={"decision_id": int(sell["decision_id"])}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["signal"]["conditions"] == []
    assert body["signal"]["reason"].startswith("held ")
    assert body["fill"]["side"] == "sell"


def test_chart_route_signal_off_the_grid(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    path = lab.store.run_dir(run_id) / "decisions.parquet"
    dec = pd.read_parquet(path)
    did = int(lab.store.ledgers(run_id).fills.iloc[0]["decision_id"])
    dec.loc[dec["decision_id"] == did, "session"] = "1999-01-01"
    dec.to_parquet(path, index=False)
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": did})
    assert response.status_code == 422
    assert response.json()["detail"] == "signal session 1999-01-01 is not on the run's bar grid"


def test_chart_route_missing_snapshot(tmp_path: Path) -> None:
    lab, run_id = _lab(tmp_path)
    full = lab.store.get(run_id)
    sid = full["snapshot_id"]
    for suffix in (".parquet", ".json"):
        (lab.settings.snapshot_dir / f"{sid}{suffix}").unlink()
    did = int(lab.store.ledgers(run_id).fills.iloc[0]["decision_id"])
    with _client(lab, tmp_path) as client:
        response = client.get(f"/api/runs/{run_id}/chart", params={"decision_id": did})
    assert response.status_code == 404
    assert response.json()["detail"] == f"snapshot {sid} not found"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_chart.py -v`

Expected: the new route tests FAIL with 404 because the path is not registered (`{"detail":"Not Found"}`), not the messages above.

- [ ] **Step 3: Write the route**

In `src/moneylab/api/app.py`, add `ChartRejected` and `assemble_chart` to the imports from `moneylab.engine` (add a new import line):

```python
from moneylab.engine.chart import ChartRejected, assemble_chart
```

After the `FillRow` model, add:

```python
class CandleOut(BaseModel):
    open: float
    high: float
    low: float
    close: float


class IndicatorSeriesOut(BaseModel):
    key: str
    price_scale: bool
    values: list[float | None]


class ChartFillOut(BaseModel):
    session: str
    side: Literal["buy", "sell"]
    price: float


class ChartSignalOut(BaseModel):
    session: str
    action: str
    reason: str
    conditions: list[dict[str, Any]]
    values: dict[str, Any]


class TradeChartOut(BaseModel):
    symbol: str
    timeframe: str
    sessions: list[str]
    candles: list[CandleOut | None]
    series: list[IndicatorSeriesOut]
    fill: ChartFillOut
    signal: ChartSignalOut
```

After the `fills` route, add:

```python
    @app.get("/api/runs/{run_id}/chart", response_model=TradeChartOut, tags=["runs"])
    def trade_chart(run_id: str, decision_id: Annotated[int, Query()]) -> Any:
        """Candles and indicator lines around one fill. ``decision_id`` is the fill's."""
        try:
            full = lab.store.get(run_id)
        except KeyError:
            raise HTTPException(404, f"run {run_id} not found") from None
        led = lab.store.ledgers(run_id)
        decisions = led.decisions
        if "decision_id" not in decisions.columns or decisions.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        matched = decisions[decisions["decision_id"] == decision_id]
        if matched.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        fills = led.fills
        if "decision_id" not in fills.columns or fills.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        matched_fills = fills[fills["decision_id"] == decision_id]
        if matched_fills.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        if len(matched_fills) > 1:
            raise HTTPException(422, f"decision {decision_id} has more than one fill")
        resolved = full["resolved"]
        try:
            data = lab.market_data(
                full["snapshot_id"],
                list(resolved["universe"]) + [resolved["benchmark"]],
                resolved["start"],
                resolved["end"],
            )
        except FileNotFoundError:
            raise HTTPException(404, f"snapshot {full['snapshot_id']} not found") from None
        try:
            payload = assemble_chart(
                resolved,
                data,
                matched.iloc[0].to_dict(),
                matched_fills.iloc[0].to_dict(),
            )
        except ChartRejected as exc:
            raise HTTPException(422, str(exc)) from exc
        return payload
```

`Query` and `Annotated` are already imported in `app.py`. `Any` and `Literal` are already imported.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_chart.py -v`

Expected: PASS, 16 tests.

Run: `uv run ruff check src/moneylab/api/app.py tests/test_chart.py` and `uv run mypy src/moneylab/api/app.py src/moneylab/engine/chart.py`

Expected: both exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/moneylab/api/app.py tests/test_chart.py
git commit -m "$(cat <<'EOF'
Serve a trade chart for one fill on a saved run.

EOF
)"
```

---

### Task 4: Client type

**Files:**
- Modify: `frontend/openapi.json` (generated)
- Modify: `frontend/src/lib/api-schema.ts` (generated)
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: `TradeChartOut` from Task 3.
- Produces: `export type TradeChart = S["TradeChartOut"]` and `api.chart(id: string, decisionId: number): Promise<TradeChart>` calling `GET /api/runs/{id}/chart?decision_id=`.

- [ ] **Step 1: Regenerate the contract**

Run: `make api-types`

Expected: exit 0. `frontend/openapi.json` contains `/api/runs/{run_id}/chart`. `frontend/src/lib/api-schema.ts` contains `TradeChartOut`.

- [ ] **Step 2: Add the client method**

In `frontend/src/lib/api.ts`, after `export type FillRow = S["FillRow"];` add:

```typescript
export type TradeChart = S["TradeChartOut"];
```

Inside `api`, after `fills`:

```typescript
  chart: (id: string, decisionId: number) =>
    request<TradeChart>(
      `/api/runs/${encodeURIComponent(id)}/chart${qs({ decision_id: decisionId })}`,
    ),
```

- [ ] **Step 3: Typecheck**

Run: `npm --prefix frontend run typecheck`

Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/openapi.json frontend/src/lib/api-schema.ts frontend/src/lib/api.ts
git commit -m "$(cat <<'EOF'
Add the trade-chart type to the dashboard client.

EOF
)"
```

---

### Task 5: Dialog on the detailed report

**Files:**
- Modify: `frontend/src/lib/analysis.ts` (the `Lot` type, `RoundTrip`, and `lotsFromFills`)
- Modify: `frontend/src/components/tables.tsx` (`FillsTable` only)
- Create: `frontend/src/components/TradeChartView.tsx`
- Create: `frontend/src/components/TradeChartDialog.tsx`
- Modify: `frontend/src/app/report/page.tsx` (round-trip cells, `FillsTable`, dialog state)
- Modify: `frontend/src/app/globals.css` (append the dialog rules)

**Interfaces:**
- Consumes: `api.chart`, `TradeChart`, `ConditionResult` from `@/lib/api`. `ConditionsTable` from `@/components/tables`. `ErrorBox` and `Loading` from `@/components/ui`. `useAsync` from `@/lib/hooks`.
- Produces:
  - `RoundTrip.entryDecisionId: number | null` and `RoundTrip.exitDecisionId: number | null`.
  - `FillsTable` gains optional `onOpen?: (fill: FillRow, opener: HTMLElement) => void`. When omitted, rows stay as they are today. Compare and paper do not pass it.
  - `TradeChartView({ chart }: { chart: TradeChart })`
  - `TradeChartDialog({ runId, decisionId, title, onClose }: { runId: string; decisionId: number; title: string; onClose: () => void })`

- [ ] **Step 1: Carry decision ids through round trips**

In `frontend/src/lib/analysis.ts`, extend the types and the lot book:

```typescript
export type RoundTrip = {
  symbol: string;
  entrySession: string;
  exitSession: string;
  entryDecisionId: number | null;
  exitDecisionId: number | null;
  qty: number;
  entryPrice: number;
  exitPrice: number;
  costs: number;
  pnl: number;
  holdSessions: number | null;
};
```

Change `Lot` inside the file to:

```typescript
type Lot = {
  session: string;
  qty: number;
  price: number;
  cost: number;
  decisionId: number | null;
};
```

When a buy is pushed:

```typescript
      book.push({
        session: f.session,
        qty: f.qty,
        price: f.price,
        cost: f.cost,
        decisionId: typeof f.decision_id === "number" ? f.decision_id : null,
      });
```

On each trip, set `entryDecisionId: lot.decisionId` and `exitDecisionId: typeof f.decision_id === "number" ? f.decision_id : null`. A sell that closes several lots keeps that same `exitDecisionId` on each trip. The remaining lot keeps `decisionId`.

- [ ] **Step 2: Make a fill row open the chart**

Replace `FillsTable` in `frontend/src/components/tables.tsx` with:

```tsx
export function FillsTable({
  fills,
  empty = "No trades.",
  onOpen,
}: {
  fills: FillRow[];
  empty?: string;
  onOpen?: (fill: FillRow, opener: HTMLElement) => void;
}) {
  if (!fills.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Session</th>
            <th>Symbol</th>
            <th>Side</th>
            <th className="num">Shares</th>
            <th className="num">Price</th>
            <th className="num">Gross</th>
            <th className="num">Cost</th>
            <th className="num">Cash after</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((f, i) => {
            const open = onOpen && typeof f.decision_id === "number" ? onOpen : null;
            return (
              <tr
                key={`${f.session}-${f.symbol}-${f.side}-${i}`}
                className={open ? "click-row" : undefined}
                role={open ? "button" : undefined}
                tabIndex={open ? 0 : undefined}
                aria-label={open ? `${f.side} ${f.symbol} ${f.session}` : undefined}
                onClick={open ? (e) => open(f, e.currentTarget) : undefined}
                onKeyDown={
                  open
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          open(f, e.currentTarget);
                        }
                      }
                    : undefined
                }
              >
                <td>{f.session}</td>
                <td>{f.symbol}</td>
                <td>
                  <span className={`side side-${f.side}`}>{f.side}</span>
                  {f.note ? <span className="muted small"> · {f.note}</span> : null}
                </td>
                <td className="num">{int(f.qty)}</td>
                <td className="num">{money(f.price)}</td>
                <td className="num">{money(f.gross)}</td>
                <td className="num">{money(f.cost)}</td>
                <td className="num">{money(f.cash_after)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

Do not pass `onOpen` from `frontend/src/app/compare/page.tsx` or `frontend/src/app/paper/page.tsx`.

- [ ] **Step 3: Draw the chart**

Create `frontend/src/components/TradeChartView.tsx`:

```tsx
"use client";

import type { TradeChart } from "@/lib/api";

const W = 760;
const CANDLE_H = 280;
const PANE_H = 96;
const PAD_X = 12;
const TOP = 16;

function shortDate(d: string): string {
  const [, m, day] = d.slice(0, 10).split("-");
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1];
  const clock = d.length > 10 ? d.slice(11, 16) : "";
  return clock ? `${mon} ${Number(day)} ${clock}` : `${mon} ${Number(day)}`;
}

function extent(values: number[]): [number, number] {
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const pad = (hi - lo) * 0.08;
  return [lo - pad, hi + pad];
}

function linePath(values: (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (typeof v !== "number") {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

export function TradeChartView({ chart }: { chart: TradeChart }) {
  const n = chart.sessions.length;
  if (n < 1) return null;
  const overlays = chart.series.filter((s) => s.price_scale);
  const panes = chart.series.filter((s) => !s.price_scale);
  const plotW = W - PAD_X * 2;
  const slot = plotW / n;
  const x = (i: number) => PAD_X + slot * i + slot / 2;
  const priceValues: number[] = [];
  chart.candles.forEach((c) => {
    if (!c) return;
    priceValues.push(c.high, c.low);
  });
  overlays.forEach((s) => s.values.forEach((v) => typeof v === "number" && priceValues.push(v)));
  const [lo, hi] = extent(priceValues);
  const yPrice = (v: number) => TOP + (1 - (v - lo) / (hi - lo)) * (CANDLE_H - TOP - 28);
  const fillAt = chart.sessions.indexOf(chart.fill.session);
  const signalAt = chart.sessions.indexOf(chart.signal.session);
  const signalClose = signalAt >= 0 ? chart.candles[signalAt]?.close : undefined;
  const height = CANDLE_H + panes.length * PANE_H;
  const ticks = [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i);

  return (
    <figure className="chart trade-chart">
      <div className="legend">
        <span className="legend-item">
          <i style={{ background: "var(--ink)" }} /> {chart.symbol}
        </span>
        {overlays.map((s, i) => (
          <span key={s.key} className="legend-item">
            <i style={{ background: i % 2 === 0 ? "var(--series-1)" : "var(--series-2)" }} /> {s.key}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${chart.symbol} at ${chart.fill.session}`}>
        {chart.candles.map((c, i) => {
          if (!c) return null;
          const up = c.close >= c.open;
          const color = up ? "var(--good)" : "var(--bad)";
          const bodyTop = yPrice(Math.max(c.open, c.close));
          const bodyH = Math.max(1, Math.abs(yPrice(c.open) - yPrice(c.close)));
          return (
            <g key={chart.sessions[i]}>
              <line x1={x(i)} x2={x(i)} y1={yPrice(c.high)} y2={yPrice(c.low)} stroke={color} strokeWidth="1" />
              <rect x={x(i) - slot * 0.28} y={bodyTop} width={Math.max(1, slot * 0.56)} height={bodyH} fill={color} />
            </g>
          );
        })}
        {overlays.map((s, k) => (
          <path
            key={s.key}
            d={linePath(s.values, x, yPrice)}
            fill="none"
            stroke={k % 2 === 0 ? "var(--series-1)" : "var(--series-2)"}
            strokeWidth="1.6"
          />
        ))}
        {fillAt >= 0 ? (
          <g>
            <line x1={x(fillAt)} x2={x(fillAt)} y1={TOP} y2={height - 8} stroke="var(--ink)" strokeDasharray="3 3" />
            <circle
              cx={x(fillAt)}
              cy={yPrice(chart.fill.price)}
              r="4.5"
              fill={chart.fill.side === "buy" ? "var(--good)" : "var(--bad)"}
              stroke="var(--well)"
              strokeWidth="1.5"
            />
          </g>
        ) : null}
        {signalAt >= 0 && typeof signalClose === "number" ? (
          <polygon
            points={`${x(signalAt)},${yPrice(signalClose) - 6} ${x(signalAt) + 6},${yPrice(signalClose)} ${x(signalAt)},${yPrice(signalClose) + 6} ${x(signalAt) - 6},${yPrice(signalClose)}`}
            fill="var(--accent)"
          />
        ) : null}
        {panes.map((s, p) => {
          const top = CANDLE_H + p * PANE_H;
          const nums = s.values.filter((v): v is number => typeof v === "number");
          const [pLo, pHi] = extent(nums.length ? nums : [0, 1]);
          const y = (v: number) => top + 16 + (1 - (v - pLo) / (pHi - pLo)) * (PANE_H - 36);
          return (
            <g key={s.key}>
              <line x1={PAD_X} x2={W - PAD_X} y1={top} y2={top} stroke="var(--line)" />
              <text x={PAD_X} y={top + 14} className="tick">
                {s.key}
              </text>
              <path d={linePath(s.values, x, y)} fill="none" stroke="var(--series-1)" strokeWidth="1.6" />
              {fillAt >= 0 ? (
                <line x1={x(fillAt)} x2={x(fillAt)} y1={top} y2={top + PANE_H - 8} stroke="var(--ink)" strokeDasharray="3 3" />
              ) : null}
            </g>
          );
        })}
        {ticks.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={height - 6}
            textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
            className="tick"
          >
            {shortDate(chart.sessions[i])}
          </text>
        ))}
      </svg>
    </figure>
  );
}
```

- [ ] **Step 4: Build the dialog**

Create `frontend/src/components/TradeChartDialog.tsx`:

```tsx
"use client";

import { useEffect, useRef } from "react";
import { TradeChartView } from "@/components/TradeChartView";
import { ConditionsTable } from "@/components/tables";
import { ErrorBox, Loading } from "@/components/ui";
import { api, type ConditionResult } from "@/lib/api";
import { money } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

export function TradeChartDialog({
  runId,
  decisionId,
  title,
  onClose,
}: {
  runId: string;
  decisionId: number;
  title: string;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const chart = useAsync(() => api.chart(runId, decisionId), [runId, decisionId]);
  useEffect(() => {
    closeRef.current?.focus();
  }, [decisionId]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const data = chart.data;
  const noBars = !!data && data.candles.every((c) => c == null);
  const conditions = (data?.signal.conditions ?? []) as ConditionResult[];

  return (
    <div className="trade-dialog-backdrop">
      <div className="trade-dialog" role="dialog" aria-modal="true" aria-labelledby="trade-chart-title">
        <header className="trade-dialog-head">
          <h2 id="trade-chart-title">{title}</h2>
          <button ref={closeRef} type="button" onClick={onClose}>
            Close
          </button>
        </header>
        {chart.loading ? <Loading what="Loading chart" /> : null}
        {chart.error ? <ErrorBox error={chart.error} title="Could not load this chart" /> : null}
        {data ? (
          <div className="trade-dialog-grid">
            <div>
              {noBars ? <p>No bars in this window.</p> : <TradeChartView chart={data} />}
            </div>
            <aside className="trade-readout">
              <p>
                Filled {data.fill.session} · {data.fill.side} @ {money(data.fill.price)}
              </p>
              <p>Signal {data.signal.session}</p>
              <p>{data.signal.reason}</p>
              <ConditionsTable conditions={conditions} />
            </aside>
          </div>
        ) : null}
      </div>
    </div>
  );
}
```

The backdrop element has no click handler.

- [ ] **Step 5: Wire the detailed report**

In `frontend/src/app/report/page.tsx`:

Add imports: `useRef` from React, `TradeChartDialog` from `@/components/TradeChartDialog`.

Inside `ReportView`, next to the other state:

```tsx
  const opener = useRef<HTMLElement | null>(null);
  const [chartPick, setChartPick] = useState<{ decisionId: number; title: string } | null>(null);

  function openChart(decisionId: number, title: string, el: HTMLElement) {
    opener.current = el;
    setChartPick({ decisionId, title });
  }

  function closeChart() {
    setChartPick(null);
    opener.current?.focus();
  }
```

In the round-trip table, replace the Entry and Exit cells:

```tsx
                        <td>
                          {t.entryDecisionId != null ? (
                            <button
                              type="button"
                              className="linkish"
                              onClick={(e) =>
                                openChart(t.entryDecisionId as number, `${t.symbol} buy · ${t.entrySession}`, e.currentTarget)
                              }
                            >
                              {t.entrySession} @ {money(t.entryPrice)}
                            </button>
                          ) : (
                            <>
                              {t.entrySession} @ {money(t.entryPrice)}
                            </>
                          )}
                        </td>
                        <td>
                          {t.exitDecisionId != null ? (
                            <button
                              type="button"
                              className="linkish"
                              onClick={(e) =>
                                openChart(t.exitDecisionId as number, `${t.symbol} sell · ${t.exitSession}`, e.currentTarget)
                              }
                            >
                              {t.exitSession} @ {money(t.exitPrice)}
                            </button>
                          ) : (
                            <>
                              {t.exitSession} @ {money(t.exitPrice)}
                            </>
                          )}
                        </td>
```

Replace the fills render:

```tsx
            {fills.data ? (
              <FillsTable
                fills={fills.data}
                onOpen={(fill, el) =>
                  openChart(fill.decision_id as number, `${fill.symbol} ${fill.side} · ${fill.session}`, el)
                }
              />
            ) : (
              <Loading />
            )}
```

At the end of the detailed fragment, just before the fragment closes, render:

```tsx
      {chartPick ? (
        <TradeChartDialog
          runId={id}
          decisionId={chartPick.decisionId}
          title={chartPick.title}
          onClose={closeChart}
        />
      ) : null}
```

`closeChart` must be stable enough for the dialog's key effect. Wrap it in `useCallback` with an empty dependency list if the lint rule requires it. The ref read stays valid.

- [ ] **Step 6: Style the dialog**

Append to `frontend/src/app/globals.css`:

```css
.click-row {
  cursor: pointer;
}
.click-row:hover,
.click-row:focus-visible {
  background: var(--accent-soft);
}
button.linkish {
  background: none;
  border: 0;
  padding: 0;
  color: var(--accent);
  font: inherit;
  cursor: pointer;
  text-align: left;
}
.trade-dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 48px 16px;
  overflow: auto;
}
.trade-dialog {
  width: min(1100px, 100%);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px;
}
.trade-dialog-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.trade-dialog-head h2 {
  margin: 0;
  font-size: 1.15rem;
}
.trade-dialog-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 16px;
}
.trade-readout p {
  margin: 0 0 8px;
}
@media (max-width: 800px) {
  .trade-dialog-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 7: Typecheck**

Run: `npm --prefix frontend run typecheck`

Expected: exit 0.

Run: `uv run pytest tests/test_chart.py -q`

Expected: PASS.

- [ ] **Step 8: Check the dialog in the browser**

Terminal A: `uv run moneylab demo` if this home has no saved run, then `uv run moneylab serve`.

Terminal B: `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm --prefix frontend run dev`.

Open `http://127.0.0.1:3000/report`, choose the demo run, switch to Detailed.

- Click a buy row in Trades. The dialog title is that symbol, `buy`, and the fill session. Candles are visible. A moving-average line is on the candles. An RSI pane is underneath. A dashed line and a green circle sit on the fill. An accent diamond sits on the earlier signal bar. The readout shows the fill, the signal session, the reason, and the conditions.
- Press Escape. The dialog closes and focus returns to that row.
- Click Entry on a round trip. The dialog opens on that buy. Close with the Close button.
- Click Exit. The circle is red. The readout shows a reason that starts with `held` and no conditions table.
- A fill row with no decision id, if one is on screen, is not focusable as a button.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/analysis.ts frontend/src/components/tables.tsx frontend/src/components/TradeChartView.tsx frontend/src/components/TradeChartDialog.tsx frontend/src/app/report/page.tsx frontend/src/app/globals.css
git commit -m "$(cat <<'EOF'
Open a trade chart from a buy or sell on the report.

EOF
)"
```
