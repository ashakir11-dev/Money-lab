"""A finished report as one download: summary JSON and closed trades as CSV."""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from typing import Any

from moneylab.engine.analytics import TRADE_COLUMNS

# The report page's headline and the numbers behind it. Settings stay on the run;
# this file is the summary a person would keep.
SUMMARY_FIELDS = (
    "id",
    "name",
    "status",
    "created_at",
    "finished_at",
    "parent_id",
    "strategy_id",
    "summary",
    "snapshot_id",
    "snapshot_name",
    "source_kind",
    "source_label",
    "engine_version",
    "total_return",
    "benchmark_return",
    "benchmark_symbol",
    "max_drawdown",
    "sharpe",
    "trades",
    "open_positions",
    "win_rate",
    "profit_factor",
    "cagr",
    "edge_mean",
    "edge_t",
    "low_sample",
    "metrics",
    "analysis",
)


def summary_document(detail: dict[str, Any]) -> dict[str, Any]:
    """The report summary, without the raw resolved settings."""
    return {key: detail[key] for key in SUMMARY_FIELDS}


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return json.dumps(value)
    return str(value)


def trades_csv(rows: list[dict[str, Any]]) -> str:
    """Closed round trips in the same column order as the trades API."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=list(TRADE_COLUMNS),
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in TRADE_COLUMNS})
    return buf.getvalue()


def report_zip(
    summary: dict[str, Any],
    trades: list[dict[str, Any]],
    evidence: dict[str, str] | None = None,
) -> bytes:
    """Zip the summary, closed trades, and a stored evidence drill-down when present."""
    files = evidence or {}
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("summary.json", json.dumps(summary, indent=2) + "\n")
        archive.writestr("trades.csv", trades_csv(trades))
        for name in sorted(files):
            archive.writestr(f"evidence/{name}", files[name])
    return raw.getvalue()


def batch_report_zip(runs: list[tuple[str, bytes]]) -> bytes:
    """One zip of finished reports, each run in its own folder.

    ``runs`` is ``(run id, that run's report zip)``. The files in each folder are
    the same files ``report_zip`` would download for that run alone.
    """
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as outer:
        for run_id, payload in runs:
            with zipfile.ZipFile(io.BytesIO(payload)) as inner:
                for name in inner.namelist():
                    outer.writestr(f"{run_id}/{name}", inner.read(name))
    return raw.getvalue()
