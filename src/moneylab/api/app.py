"""FastAPI application: the research API plus the built dashboard, on one address.

Start it with ``uv run moneylab serve``. ``create_app(lab)`` builds an app around a
``Lab``; tests pass their own. Every number the dashboard shows comes from here.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from moneylab import ENGINE_VERSION
from moneylab.api.jobs import Job, JobRunner
from moneylab.components.registry import list_components
from moneylab.data.snapshot import list_snapshots, read_import_file, snapshot_name
from moneylab.describe import data_source, strategy_summary
from moneylab.engine.chart import ChartRejected, assemble_chart
from moneylab.engine.config import StrategyConfig, VersionMismatch, check_versions
from moneylab.jsonable import jsonable
from moneylab.report_export import report_zip, summary_document
from moneylab.service import Lab

API_VERSION = "1"
#: Where ``npm run build`` in ``frontend/`` writes the static dashboard.
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[3] / "frontend" / "out"


# JSON helpers --------------------------------------------------------------------------
def records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = json.loads(df.to_json(orient="records", date_format="iso"))
    return out


# Response and request models (they drive the generated TypeScript types) -------------
class Health(BaseModel):
    status: Literal["ok"] = "ok"
    api_version: str = API_VERSION
    engine_version: str = ENGINE_VERSION


class ParamOut(BaseModel):
    name: str
    type: str
    default: Any
    label: str
    min: float | None = None
    max: float | None = None
    choices: list[str] = Field(default_factory=list)
    help: str = ""


class ComponentOut(BaseModel):
    id: str
    version: str
    label: str
    description: str
    uses: list[str]
    params: list[ParamOut]
    price_scale: bool
    universe: bool = Field(description="cross-sectional: computed over the whole universe")


class Defaults(BaseModel):
    starting_cash: float
    target_allocation: float
    max_positions: int
    cost_bps_per_side: float
    benchmark: str = "SPY"
    exit_sessions: int = 10


class SnapshotOut(BaseModel):
    id: str
    source: str
    source_kind: Literal["real", "demo", "file"]
    source_label: str
    label: str
    name: str = Field(description="typed label, then source, timeframe, universe and dates")
    timeframe: str = Field(default="1d", description="bar size: 1min, 5min, 15min, 30min, 1h or 1d")
    adjustment: str
    created_at: str
    symbols: list[str]
    first_date: str
    last_date: str
    rows: int


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1)
    symbols: list[str] = Field(min_length=1)
    label: str = ""


class WatchlistOut(BaseModel):
    name: str
    version: int
    label: str
    members: list[str]
    created_at: str


class TiingoImport(BaseModel):
    """Daily bars from Tiingo. A watchlist's current members are included too; ``symbols``
    are added on top (use that for the benchmark, usually SPY). Tiingo has no intraday bars."""

    symbols: list[str] = Field(default_factory=list)
    watchlist: str | None = None
    start: date
    end: date
    label: str = ""

    @model_validator(mode="after")
    def _has_a_feed(self) -> TiingoImport:
        named = bool(self.watchlist and self.watchlist.strip())
        typed = any(s.strip() for s in self.symbols)
        if not named and not typed:
            raise ValueError("name at least one symbol or a watchlist")
        return self


class EquiblesImport(BaseModel):
    """Symbols to fetch. A watchlist's current members are included too; ``symbols``
    are added on top (use that for the benchmark, usually SPY)."""

    symbols: list[str] = Field(default_factory=list)
    watchlist: str | None = None
    start: date
    end: date
    label: str = ""
    timeframe: Literal["1min", "5min", "15min", "30min", "1h", "1d"] = "1h"

    @model_validator(mode="after")
    def _has_a_feed(self) -> EquiblesImport:
        named = bool(self.watchlist and self.watchlist.strip())
        typed = any(s.strip() for s in self.symbols)
        if not named and not typed:
            raise ValueError("name at least one symbol or a watchlist")
        return self


class CsvImport(BaseModel):
    csv: str = Field(min_length=1, description="date,symbol,open,high,low,close,volume")
    filename: str = "upload.csv"
    label: str = ""


class RunSubmit(BaseModel):
    config: StrategyConfig
    snapshot_id: str
    parent_id: str | None = None
    strategy_id: str | None = None
    watchlist: str | None = None


class StrategySave(BaseModel):
    config: StrategyConfig
    strategy_id: str | None = None


class StrategyRunOut(BaseModel):
    id: str
    name: str
    created_at: str | None = None
    snapshot_id: str
    start: str | None = None
    end: str | None = None
    universe: list[str]
    gap: float | None = None
    total_return: float | None = None
    benchmark_return: float | None = None
    benchmark_symbol: str | None = None


class StrategyVersionOut(BaseModel):
    id: str
    parent_id: str | None
    name: str
    created_at: str
    best_gap: float | None
    best_run_id: str | None
    run_count: int
    runs: list[StrategyRunOut]
    rules: dict[str, Any]


class StrategyFamilyOut(BaseModel):
    id: str
    parent_id: str | None
    name: str
    created_at: str
    best_gap: float | None
    best_run_id: str | None
    version_count: int
    run_count: int
    versions: list[StrategyVersionOut]


class BatchSubmit(BaseModel):
    configs: list[StrategyConfig] = Field(min_length=1)
    snapshot_id: str
    parent_ids: list[str | None] | None = None


class JobOut(BaseModel):
    id: str
    kind: str
    status: Literal["queued", "running", "complete", "failed"]
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    run_ids: list[str]
    error: str | None


# The research analysis of a run (engine/analytics.py). Every field may be null: a run
# with no trades has no win rate, and a run with no benchmark has no beta.
class Always(BaseModel):
    """Output models whose fields are always present in the JSON (null when unknown), so
    the generated TypeScript types make them required."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class TradeStatsOut(Always):
    count: int = 0
    low_sample: bool = True
    wins: int | None = None
    losses: int | None = None
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    avg_win_pct: float | None = None
    avg_loss_pct: float | None = None
    payoff_ratio: float | None = None
    expectancy: float | None = None
    expectancy_pct: float | None = None
    profit_factor: float | None = None
    gross_profit: float | None = None
    gross_loss: float | None = None
    largest_win: float | None = None
    largest_loss: float | None = None
    t_stat: float | None = None
    avg_hold: float | None = None
    median_hold: float | None = None
    avg_hold_win: float | None = None
    avg_hold_loss: float | None = None
    max_consecutive_losses: int | None = None
    max_consecutive_wins: int | None = None
    avg_mae: float | None = None
    avg_mfe: float | None = None
    avg_mae_winners: float | None = None
    avg_mfe_losers: float | None = None


class EdgeStatsOut(Always):
    count: int = 0
    mean: float | None = None
    median: float | None = None
    t_stat: float | None = None
    beat_rate: float | None = None
    low_sample: bool | None = None


class ReturnStatsOut(Always):
    cagr: float | None = None
    volatility: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    max_drawdown: float | None = None
    longest_underwater: int | None = None
    max_dd_length: int | None = None


class DrawdownStatsOut(Always):
    max_drawdown: float | None = None
    peak_session: str | None = None
    trough_session: str | None = None
    recovery_session: str | None = None
    max_dd_length: int | None = None
    longest_underwater: int | None = None
    longest_underwater_start: str | None = None
    current_drawdown: float | None = None
    drawdowns_over_10pct: int | None = None
    time_underwater: float | None = None


class MarketStatsOut(Always):
    beta: float | None = None
    correlation: float | None = None
    alpha: float | None = None


class CostStatsOut(Always):
    total: float | None = None
    return_before_costs: float | None = None
    cost_drag: float | None = None
    share_of_gross_profit: float | None = None
    turnover: float | None = None
    traded_notional: float | None = None


class FunnelOut(Always):
    signals: int | None = None
    entered: int | None = None
    filled: int | None = None
    taken_rate: float | None = None
    fill_rate: float | None = None
    skipped: dict[str, int] = Field(default_factory=dict)
    not_filled: dict[str, int] = Field(default_factory=dict)
    near_misses: int | None = None


class YearRowOut(Always):
    year: str
    ret: float | None = None
    bench_ret: float | None = None
    trades: int = 0
    win_rate: float | None = None
    pnl: float | None = None
    avg_edge: float | None = None


class MonthRowOut(Always):
    month: str
    ret: float | None = None
    bench_ret: float | None = None


class StreaksOut(Always):
    max_consecutive_losing_trades: int | None = None
    max_consecutive_losing_months: int | None = None
    losing_months: int | None = None
    months: int | None = None


class GroupRowOut(Always):
    key: str
    trades: int
    win_rate: float | None = None
    pnl: float | None = None
    avg_ret: float | None = None
    avg_edge: float | None = None
    avg_hold: float | None = None


class RegimeRowOut(Always):
    regime: Literal["up", "down", "warmup"]
    sessions: int
    ret: float | None = None
    bench_ret: float | None = None
    trades: int = 0
    win_rate: float | None = None
    avg_ret: float | None = None
    avg_edge: float | None = None


class AnalysisOut(Always):
    trades: TradeStatsOut = Field(default_factory=TradeStatsOut)
    edge: EdgeStatsOut = Field(default_factory=EdgeStatsOut)
    returns: ReturnStatsOut = Field(default_factory=ReturnStatsOut)
    drawdown: DrawdownStatsOut = Field(default_factory=DrawdownStatsOut)
    benchmark: ReturnStatsOut | None = None
    market: MarketStatsOut | None = None
    costs: CostStatsOut = Field(default_factory=CostStatsOut)
    funnel: FunnelOut = Field(default_factory=FunnelOut)
    by_year: list[YearRowOut] = Field(default_factory=list)
    monthly: list[MonthRowOut] = Field(default_factory=list)
    streaks: StreaksOut = Field(default_factory=StreaksOut)
    by_symbol: list[GroupRowOut] = Field(default_factory=list)
    by_exit: list[GroupRowOut] = Field(default_factory=list)
    by_regime: list[RegimeRowOut] = Field(default_factory=list)


class TradeRow(Always):
    """One closed round trip. Returns are fractions; hold is in sessions (bars)."""

    symbol: str
    entry_session: str
    exit_session: str
    entry_decision_id: int | None = None
    exit_decision_id: int | None = None
    qty: float
    entry_price: float
    exit_price: float
    costs: float
    pnl: float
    ret: float | None = None
    hold_sessions: int | None = None
    exit_kind: str
    exit_reason: str | None = None
    bench_ret: float | None = None
    edge: float | None = None
    mae: float | None = None
    mfe: float | None = None
    regime: str | None = None


class RunSummary(BaseModel):
    id: str
    parent_id: str | None
    strategy_id: str | None = None
    name: str
    created_at: str
    status: str
    snapshot_id: str
    snapshot_name: str
    source_kind: Literal["real", "demo", "file"]
    source_label: str
    summary: str
    total_return: float | None
    benchmark_return: float | None
    benchmark_symbol: str | None
    max_drawdown: float | None
    sharpe: float | None
    trades: int | None
    open_positions: int | None
    win_rate: float | None = None
    profit_factor: float | None = None
    cagr: float | None = None
    edge_mean: float | None = Field(
        default=None, description="average edge per trade vs the benchmark"
    )
    edge_t: float | None = Field(default=None, description="t-statistic of that edge")
    low_sample: bool | None = Field(default=None, description="fewer than 30 closed trades")


class RunDetail(RunSummary):
    engine_version: str
    config_hash: str
    finished_at: str | None
    resolved: dict[str, Any]
    metrics: dict[str, Any]
    analysis: AnalysisOut | None = Field(
        default=None, description="research analysis; null when it cannot be computed"
    )


class EquityRow(BaseModel):
    session: str
    cash: float
    positions_value: float
    equity: float
    positions: int
    benchmark_equity: float | None = None
    sweep_value: float | None = Field(
        default=None, description="value of idle cash held in the benchmark (idle_cash setting)"
    )


class FillRow(BaseModel):
    session: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    gross: float | None = None
    cost: float
    cash_after: float
    decision_id: int | None = None
    sessions_waited: int | None = None
    note: str | None = None


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


class ChartLegOut(BaseModel):
    fill: ChartFillOut
    signal: ChartSignalOut


class TradeChartOut(BaseModel):
    symbol: str
    timeframe: str
    sessions: list[str]
    candles: list[CandleOut | None]
    series: list[IndicatorSeriesOut]
    fill: ChartFillOut
    signal: ChartSignalOut
    legs: list[ChartLegOut] = Field(
        description="The clicked fill, or the buy and the sell when a round trip is opened."
    )


class DecisionPage(BaseModel):
    total: int
    offset: int
    limit: int
    rows: list[dict[str, Any]]


class SettingDiff(BaseModel):
    setting: str
    a: Any
    b: Any


class CompareOut(BaseModel):
    a: RunDetail
    b: RunDetail
    is_parent: bool = Field(description="run A is run B's parent")
    config_diff: list[SettingDiff]
    data_differs: bool
    first_difference: dict[str, Any] | None


class PaperStart(BaseModel):
    run_id: str
    start: date | None = Field(
        default=None, description="last history session (default: the snapshot's last)"
    )
    name: str = ""


class PaperDelivery(BaseModel):
    csv: str = Field(description="sessions in the import format: date,symbol,open,...")


class PaperAccountOut(BaseModel):
    id: str
    name: str
    run_id: str
    start_session: str
    created_at: str
    latest_session: str | None = None
    processed_sessions: int = 0


class OverviewBest(BaseModel):
    run_id: str
    name: str
    total_return: float | None
    pnl: float | None
    benchmark_symbol: str | None
    benchmark_return: float | None


class OverviewPaper(BaseModel):
    paper_id: str
    name: str
    status: Literal["blocked", "waiting", "caught_up"]
    detail: str


class Overview(BaseModel):
    snapshots: int
    strategies: int
    best: OverviewBest | None
    paper: OverviewPaper | None


class PaperDetail(BaseModel):
    status: dict[str, Any]
    decisions: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    equity: list[dict[str, Any]]


class GateOut(BaseModel):
    number: int
    title: str
    passing: bool
    evidence: list[str]
    depends_on: str


# Builders ------------------------------------------------------------------------------
def _job_out(job: Job) -> JobOut:
    return JobOut(**vars(job))


def _snapshot_out(meta: dict[str, Any]) -> SnapshotOut:
    ds = data_source(meta.get("source"), meta.get("label", ""))
    return SnapshotOut(
        id=meta["id"],
        source=meta.get("source", ""),
        source_kind=ds.kind,
        source_label=ds.badge,
        label=meta.get("label", ""),
        name=snapshot_name(meta),
        timeframe=str(meta.get("timeframe") or "1d"),
        adjustment=meta.get("adjustment", ""),
        created_at=meta.get("created_at", ""),
        symbols=meta.get("symbols", []),
        first_date=meta.get("first_date", ""),
        last_date=meta.get("last_date", ""),
        rows=int(meta.get("rows", 0)),
    )


def _snapshot_name_for(lab: Lab, snapshot_id: str) -> str:
    try:
        return snapshot_name(lab.snapshot(snapshot_id).meta)
    except (OSError, ValueError, KeyError):
        return snapshot_id


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and math.isfinite(v) else None


def _symbol(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _summary(row: dict[str, Any], full: dict[str, Any], snapshot_name: str) -> dict[str, Any]:
    m = full.get("metrics") or {}
    res = full.get("resolved") or {}
    ds = data_source((res.get("data") or {}).get("source"))
    bench = m.get("benchmark") or {}
    return {
        "id": row["id"],
        "parent_id": row.get("parent_id"),
        "strategy_id": row.get("strategy_id"),
        "name": row["name"],
        "created_at": row["created_at"],
        "status": row["status"],
        "snapshot_id": row["snapshot_id"],
        "snapshot_name": snapshot_name,
        "source_kind": ds.kind,
        "source_label": ds.badge,
        "summary": strategy_summary(res),
        "total_return": _num(m.get("total_return")),
        "benchmark_return": _num(bench.get("total_return")),
        "benchmark_symbol": _symbol(bench.get("symbol") or res.get("benchmark")),
        "max_drawdown": _num(m.get("max_drawdown")),
        "sharpe": _num(m.get("sharpe")),
        "trades": m.get("trades"),
        "open_positions": m.get("open_positions"),
        "win_rate": _num(m.get("win_rate")),
        "profit_factor": _num(m.get("profit_factor")),
        "cagr": _num(m.get("cagr")),
        "edge_mean": _num(m.get("edge_mean")),
        "edge_t": _num(m.get("edge_t")),
        "low_sample": m.get("low_sample"),
    }


def _ensure_analysis(lab: Lab, run_id: str, full: dict[str, Any]) -> dict[str, Any]:
    """Runs saved before the analysis existed get it computed once, on first open, and
    stored. If their snapshot is gone the run is shown without it."""
    if "analysis" in full["metrics"]:
        return full
    try:
        if lab.recompute_metrics(run_id):
            return lab.store.get(run_id)
    except Exception:  # never let a missing or odd snapshot break opening a run
        pass
    return full


def _paper_line(status: dict[str, Any]) -> dict[str, Any]:
    """Blocked, waiting, or caught up, with one short line for the home tile."""
    blocked = status.get("blocked")
    missing = list(status.get("missing") or [])
    waiting = list(status.get("waiting") or [])
    if blocked:
        kind, detail = "blocked", str(blocked)
    elif missing or waiting:
        n = len(missing) + len(waiting)
        kind = "waiting"
        detail = f"Waiting on {n} session" if n == 1 else f"Waiting on {n} sessions"
    else:
        kind = "caught_up"
        latest = status.get("latest_processed")
        detail = f"Caught up through {latest}" if latest else "Caught up"
    return {
        "paper_id": status["paper_id"],
        "name": status.get("name") or status["paper_id"],
        "status": kind,
        "detail": detail,
    }


def _overview(lab: Lab) -> dict[str, Any]:
    """Counts, the highest-return completed run, and the newest readable paper account."""
    completed: list[tuple[dict[str, Any], dict[str, Any], float | None]] = []
    for row in lab.store.list_runs():
        if row.get("status") != "completed":
            continue
        full = lab.store.get(row["id"])
        metrics = full.get("metrics") or {}
        completed.append((row, full, _num(metrics.get("total_return"))))

    best: dict[str, Any] | None = None
    ranked = [item for item in completed if item[2] is not None]
    if ranked:
        row, full, total = max(
            ranked, key=lambda item: (item[2], item[0]["created_at"], item[0]["id"])
        )
        metrics = full.get("metrics") or {}
        resolved = full.get("resolved") or {}
        bench = metrics.get("benchmark") or {}
        final = _num(metrics.get("final_equity"))
        start = _num((resolved.get("portfolio") or {}).get("starting_cash"))
        symbol = str(bench.get("symbol") or resolved.get("benchmark") or "") or None
        best = {
            "run_id": row["id"],
            "name": row["name"],
            "total_return": total,
            "pnl": (final - start) if final is not None and start is not None else None,
            "benchmark_symbol": symbol,
            "benchmark_return": _num(bench.get("total_return")),
        }

    paper: dict[str, Any] | None = None
    for acct in lab.paper_accounts():
        try:
            status = lab.paper_status(acct["id"], as_of=date.today())
        except Exception:
            continue
        paper = _paper_line(status)
        break

    return {
        "snapshots": len(list_snapshots(lab.settings.snapshot_dir)),
        "strategies": len(completed),
        "best": best,
        "paper": paper,
    }


def _detail(lab: Lab, run_id: str) -> RunDetail:
    try:
        full = lab.store.get(run_id)
    except KeyError:
        raise HTTPException(404, f"run {run_id} not found") from None
    full = _ensure_analysis(lab, run_id, full)
    analysis = full["metrics"].get("analysis")
    metrics = {k: v for k, v in full["metrics"].items() if k != "analysis"}
    return RunDetail(
        **_summary(full, full, _snapshot_name_for(lab, full["snapshot_id"])),
        engine_version=full["engine_version"],
        config_hash=full["config_hash"],
        finished_at=full.get("finished_at"),
        resolved=jsonable(full["resolved"]),
        metrics=jsonable(metrics),
        analysis=AnalysisOut(**analysis) if analysis else None,
    )


def _bad_request(exc: Exception) -> HTTPException:
    if isinstance(exc, ValidationError):
        return HTTPException(422, jsonable(exc.errors(include_url=False)))
    return HTTPException(422, str(exc))


# App -----------------------------------------------------------------------------------
def create_app(lab: Lab | None = None, web_dir: Path | None = None) -> FastAPI:
    lab = lab or Lab.open()
    jobs = JobRunner()

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        jobs.shutdown()

    app = FastAPI(
        title="Money-lab research API",
        version=API_VERSION,
        lifespan=lifespan,
        description="Build strategies, run backtests in the background, compare runs.",
    )
    app.state.lab = lab
    app.state.jobs = jobs
    # Only the Next.js dev server (``npm run dev``) is a different origin; the built
    # dashboard is served from this app's own address.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=Health, tags=["meta"])
    def health() -> Health:
        return Health()

    @app.get("/api/overview", response_model=Overview, tags=["meta"])
    def overview() -> dict[str, Any]:
        """Home-page counts, the best completed run, and the newest paper account."""
        return _overview(lab)

    # Registry ------------------------------------------------------------------------
    @app.get("/api/components", response_model=list[ComponentOut], tags=["registry"])
    def components() -> list[ComponentOut]:
        """Every registered indicator with its versioned parameter schema. The builder
        draws its controls from this, so a new indicator file needs no UI change."""
        return [
            ComponentOut(
                id=d.id,
                version=d.version,
                label=d.label,
                description=d.description,
                uses=list(d.uses),
                params=[
                    ParamOut(
                        name=p.name,
                        type=p.type,
                        default=p.default,
                        label=p.label or p.name,
                        min=p.min,
                        max=p.max,
                        choices=list(p.choices),
                        help=p.help,
                    )
                    for p in d.params
                ],
                price_scale=d.price_scale,
                universe=d.compute_universe is not None,
            )
            for d in list_components()
        ]

    @app.get("/api/defaults", response_model=Defaults, tags=["registry"])
    def defaults() -> Defaults:
        s = lab.settings
        return Defaults(
            starting_cash=s.starting_cash,
            target_allocation=s.target_allocation,
            max_positions=s.max_positions,
            cost_bps_per_side=s.cost_bps_per_side,
        )

    @app.post("/api/validate", response_model=dict[str, Any], tags=["registry"])
    def validate(body: RunSubmit) -> dict[str, Any]:
        """Resolve a strategy with today's defaults without running it."""
        from moneylab.engine.config import resolve

        try:
            config = body.config
            if body.snapshot_id:
                # An unknown snapshot is reported when the run is submitted.
                with contextlib.suppress(KeyError, OSError):
                    config = lab.with_snapshot_timeframe(config, body.snapshot_id)
            resolved = resolve(config, lab.settings)
        except (ValueError, ValidationError) as exc:
            raise _bad_request(exc) from exc
        return {"resolved": jsonable(resolved), "summary": strategy_summary(resolved)}

    # Data ----------------------------------------------------------------------------
    @app.get("/api/snapshots", response_model=list[SnapshotOut], tags=["data"])
    def snapshots() -> list[SnapshotOut]:
        return [_snapshot_out(m) for m in list_snapshots(lab.settings.snapshot_dir)]

    @app.post("/api/snapshots/tiingo", response_model=SnapshotOut, tags=["data"])
    def import_tiingo(body: TiingoImport) -> SnapshotOut:
        """Fetch daily bars, splits and dividends from Tiingo. The key stays on the server."""
        try:
            snap = lab.import_provider(
                body.symbols,
                body.start,
                body.end,
                label=body.label,
                watchlist=body.watchlist,
                timeframe="1d",
                source="tiingo",
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:  # provider errors are shown to the person
            raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
        return _snapshot_out(snap.meta)

    @app.post("/api/snapshots/equibles", response_model=SnapshotOut, tags=["data"])
    def import_equibles(body: EquiblesImport) -> SnapshotOut:
        """Fetch raw bars and splits from Equibles. The key stays on the server."""
        try:
            snap = lab.import_provider(
                body.symbols,
                body.start,
                body.end,
                label=body.label,
                watchlist=body.watchlist,
                timeframe=body.timeframe,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:  # provider errors are shown to the person
            raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
        return _snapshot_out(snap.meta)

    @app.post("/api/snapshots/csv", response_model=SnapshotOut, tags=["data"])
    def import_csv(body: CsvImport) -> SnapshotOut:
        try:
            bars = read_import_file(io.StringIO(body.csv))  # type: ignore[arg-type]
            snap = lab.import_frame(bars, source=f"file:{body.filename}", label=body.label)
        except Exception as exc:
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
        return _snapshot_out(snap.meta)

    @app.get("/api/watchlists", response_model=list[WatchlistOut], tags=["data"])
    def watchlists() -> list[dict[str, Any]]:
        return [jsonable(w) for w in lab.watchlists()]

    @app.post("/api/watchlists", response_model=WatchlistOut, tags=["data"])
    def save_watchlist(body: WatchlistIn) -> dict[str, Any]:
        saved: dict[str, Any] = jsonable(lab.save_watchlist(body.name, body.symbols, body.label))
        return saved

    # Strategies ----------------------------------------------------------------------
    @app.get("/api/strategies", response_model=list[StrategyFamilyOut], tags=["strategies"])
    def strategies() -> list[dict[str, Any]]:
        """Root strategies ranked by the best run's gap versus that run's own benchmark."""
        return [jsonable(row) for row in lab.strategy_families()]

    @app.get(
        "/api/strategies/{strategy_id}",
        response_model=StrategyVersionOut,
        tags=["strategies"],
    )
    def strategy_detail(strategy_id: str) -> dict[str, Any]:
        for family in lab.strategy_families():
            for version in family["versions"]:
                if version["id"] == strategy_id:
                    return jsonable(version)
        raise HTTPException(404, f"strategy {strategy_id} not found")

    @app.post("/api/strategies", response_model=StrategyVersionOut, tags=["strategies"])
    def save_strategy(body: StrategySave) -> dict[str, Any]:
        """Save a version without running it. Unchanged rules keep the same id."""
        if body.strategy_id is not None:
            try:
                lab.strategy(body.strategy_id)
            except KeyError:
                raise HTTPException(404, f"strategy {body.strategy_id} not found") from None
        try:
            saved = lab.save_strategy(body.config, body.strategy_id)
        except ValidationError as exc:
            raise _bad_request(exc) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        for family in lab.strategy_families():
            for version in family["versions"]:
                if version["id"] == saved:
                    return jsonable(version)
        raise HTTPException(500, "saved strategy was not readable")

    # Runs ----------------------------------------------------------------------------
    @app.get("/api/runs", response_model=list[RunSummary], tags=["runs"])
    def runs() -> list[dict[str, Any]]:
        names: dict[str, str] = {}
        out = []
        for row in lab.store.list_runs():
            full = lab.store.get(row["id"])
            sid = full["snapshot_id"]
            names.setdefault(sid, _snapshot_name_for(lab, sid))
            out.append(_summary(row, full, names[sid]))
        return out

    @app.post("/api/runs", response_model=JobOut, status_code=202, tags=["runs"])
    def submit_run(body: RunSubmit) -> JobOut:
        """Queue one run. Returns a job at once; poll ``/api/jobs/{id}`` for its run id."""
        try:
            lab.snapshot(body.snapshot_id)
        except FileNotFoundError:
            raise HTTPException(404, f"snapshot {body.snapshot_id} not found") from None
        if body.parent_id is not None:
            _detail(lab, body.parent_id)
        if body.strategy_id is not None:
            try:
                lab.strategy(body.strategy_id)
            except KeyError:
                raise HTTPException(404, f"strategy {body.strategy_id} not found") from None
        validate(body)
        job = jobs.submit(
            "run",
            lambda: [
                lab.run(
                    body.config,
                    body.snapshot_id,
                    body.parent_id,
                    watchlist=body.watchlist,
                    strategy_id=body.strategy_id,
                )
            ],
        )
        return _job_out(job)

    @app.post("/api/batches", response_model=JobOut, status_code=202, tags=["runs"])
    def submit_batch(body: BatchSubmit) -> JobOut:
        """Queue several variants; each runs on its own account across worker processes."""
        for c in body.configs:
            validate(RunSubmit(config=c, snapshot_id=body.snapshot_id))
        job = jobs.submit(
            "batch",
            lambda: lab.run_batch(body.configs, body.snapshot_id, parent_ids=body.parent_ids),
        )
        return _job_out(job)

    @app.post("/api/runs/{run_id}/duplicate", response_model=JobOut, status_code=202, tags=["runs"])
    def duplicate(run_id: str) -> JobOut:
        """Re-run exactly as saved (same settings, data and versions)."""
        detail = _detail(lab, run_id)
        try:
            check_versions({k: v for k, v in detail.resolved.items() if k != "data"})
        except VersionMismatch as exc:
            raise HTTPException(409, str(exc)) from exc
        return _job_out(jobs.submit("duplicate", lambda: [lab.duplicate(run_id)]))

    @app.get("/api/jobs", response_model=list[JobOut], tags=["runs"])
    def list_jobs() -> list[JobOut]:
        return [_job_out(j) for j in jobs.list()]

    @app.get("/api/jobs/{job_id}", response_model=JobOut, tags=["runs"])
    def get_job(job_id: str) -> JobOut:
        try:
            return _job_out(jobs.get(job_id))
        except KeyError:
            raise HTTPException(404, f"job {job_id} not found") from None

    @app.get("/api/runs/{run_id}", response_model=RunDetail, tags=["runs"])
    def run_detail(run_id: str) -> RunDetail:
        return _detail(lab, run_id)

    @app.get("/api/runs/{run_id}/equity", response_model=list[EquityRow], tags=["runs"])
    def equity(run_id: str) -> list[dict[str, Any]]:
        _detail(lab, run_id)
        return records(lab.store.ledgers(run_id).equity)

    def _closed_trades(run_id: str) -> list[dict[str, Any]]:
        """Closed round trips. ``_detail`` first so older runs grow a trade list."""
        _detail(lab, run_id)
        t = lab.store.trades(run_id)
        out: list[dict[str, Any]] = jsonable(records(t)) if t is not None else []
        return out

    @app.get("/api/runs/{run_id}/trades", response_model=list[TradeRow], tags=["runs"])
    def trades(run_id: str) -> list[dict[str, Any]]:
        """Closed round trips with return, edge versus the benchmark, MAE/MFE, how each
        closed and the true holding length. Empty when the analysis cannot be computed."""
        return _closed_trades(run_id)

    @app.get(
        "/api/runs/{run_id}/export",
        tags=["runs"],
        response_class=Response,
        response_model=None,
        responses={
            200: {
                "description": "Zip of summary.json (report summary) and trades.csv.",
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    def export_run(run_id: str) -> Response:
        """Download a finished report: the summary as JSON and closed trades as CSV."""
        detail = _detail(lab, run_id)
        if detail.status != "completed":
            raise HTTPException(
                409,
                f"run {run_id} is {detail.status}; only a finished report can be exported",
            )
        payload = report_zip(
            summary_document(detail.model_dump(mode="json")),
            _closed_trades(run_id),
        )
        filename = f"moneylab-report-{run_id}.zip"
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/runs/{run_id}/fills", response_model=list[FillRow], tags=["runs"])
    def fills(run_id: str) -> list[dict[str, Any]]:
        _detail(lab, run_id)
        return records(lab.store.ledgers(run_id).fills)

    def _filled_decision(led: Any, decision_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        decisions = led.decisions
        if "decision_id" not in decisions.columns or decisions.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        matched = decisions[decisions["decision_id"] == decision_id]
        if matched.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        fills_frame = led.fills
        if "decision_id" not in fills_frame.columns or fills_frame.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        matched_fills = fills_frame[fills_frame["decision_id"] == decision_id]
        if matched_fills.empty:
            raise HTTPException(404, f"decision {decision_id} not found")
        if len(matched_fills) > 1:
            raise HTTPException(422, f"decision {decision_id} has more than one fill")
        decision = {str(k): v for k, v in matched.iloc[0].to_dict().items()}
        fill = {str(k): v for k, v in matched_fills.iloc[0].to_dict().items()}
        return decision, fill

    @app.get("/api/runs/{run_id}/chart", response_model=TradeChartOut, tags=["runs"])
    def trade_chart(
        run_id: str,
        decision_id: Annotated[int, Query()],
        paired_decision_id: Annotated[int | None, Query()] = None,
    ) -> Any:
        """Candles around a fill. A paired id adds the other leg of that round trip."""
        try:
            full = lab.store.get(run_id)
        except KeyError:
            raise HTTPException(404, f"run {run_id} not found") from None
        led = lab.store.ledgers(run_id)
        decision, fill = _filled_decision(led, decision_id)
        paired = None
        if paired_decision_id is not None and paired_decision_id != decision_id:
            other_decision, other_fill = _filled_decision(led, paired_decision_id)
            if str(other_fill["symbol"]) != str(fill["symbol"]):
                other_symbol = other_fill["symbol"]
                raise HTTPException(
                    422,
                    f"decision {paired_decision_id} is {other_symbol}, not {fill['symbol']}",
                )
            paired = (other_decision, other_fill)
        resolved = full["resolved"]
        sid = full["snapshot_id"]
        if not (lab.settings.snapshot_dir / f"{sid}.parquet").is_file():
            raise HTTPException(404, f"snapshot {sid} not found")
        try:
            data = lab.market_data(
                sid,
                [*list(resolved["universe"]), resolved["benchmark"]],
                resolved["start"],
                resolved["end"],
            )
        except FileNotFoundError:
            raise HTTPException(404, f"snapshot {sid} not found") from None
        try:
            payload = assemble_chart(resolved, data, decision, fill, paired=paired)
        except ChartRejected as exc:
            raise HTTPException(422, str(exc)) from exc
        return payload

    @app.get("/api/runs/{run_id}/decisions", response_model=DecisionPage, tags=["runs"])
    def decisions(
        run_id: str,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        action: Annotated[list[str] | None, Query()] = None,
        symbol: str | None = None,
        session: str | None = None,
    ) -> DecisionPage:
        """Decisions in engine order. Filter by action (enter, exit, skip, no_signal,
        ineligible), symbol or session."""
        _detail(lab, run_id)
        led = lab.store.ledgers(run_id)
        d = led.decisions
        if action:
            d = d[d["action"].isin(action)]
        if symbol:
            d = d[d["symbol"] == symbol.upper()]
        if session:
            d = d[d["session"] == session]
        page = jsonable(records(d.iloc[offset : offset + limit]))
        # Each decision that led to a fill carries it (joined on decision_id).
        f = led.fills
        by_decision = (
            {int(r["decision_id"]): r for r in records(f) if r["decision_id"] is not None}
            if "decision_id" in f
            else {}
        )
        for row in page:
            fill = by_decision.get(row.get("decision_id"))
            row["fill"] = (
                {k: fill.get(k) for k in ("session", "qty", "price", "cost", "cash_after")}
                if fill
                else None
            )
        return DecisionPage(total=len(d), offset=offset, limit=limit, rows=page)

    @app.get("/api/compare", response_model=CompareOut, tags=["runs"])
    def compare(a: str, b: str) -> CompareOut:
        """What changed between two runs: settings, data, and the first differing
        trading decision with the values behind it."""
        da, db = _detail(lab, a), _detail(lab, b)
        cmp = lab.compare(a, b)
        return CompareOut(
            a=da,
            b=db,
            is_parent=db.parent_id == a,
            config_diff=[SettingDiff(**jsonable(r)) for r in cmp["config_diff"]],
            data_differs=da.snapshot_id != db.snapshot_id,
            first_difference=jsonable(cmp["first_difference"]),
        )

    # Paper trading ---------------------------------------------------------------------
    def _paper_detail(paper_id: str) -> PaperDetail:
        try:
            status = lab.paper_status(paper_id, as_of=date.today())
        except KeyError:
            raise HTTPException(404, f"paper account {paper_id} not found") from None
        led = lab.paper_ledgers(paper_id)
        return PaperDetail(
            status=jsonable(status),
            decisions=records(led.decisions),
            fills=records(led.fills),
            equity=records(led.equity),
        )

    @app.get("/api/paper", response_model=list[PaperAccountOut], tags=["paper"])
    def paper_accounts() -> list[dict[str, Any]]:
        return lab.paper_accounts()

    @app.post("/api/paper", response_model=PaperAccountOut, tags=["paper"])
    def paper_start(body: PaperStart) -> dict[str, Any]:
        """Freeze a completed run and open a paper account for the sessions after START."""
        try:
            pid = lab.start_paper(body.run_id, body.start, body.name)
        except Exception as exc:  # refused (version change, bad date): shown, not started
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
        return next(a for a in lab.paper_accounts() if a["id"] == pid)

    @app.get("/api/paper/{paper_id}", response_model=PaperDetail, tags=["paper"])
    def paper_detail(paper_id: str) -> PaperDetail:
        """Status (cash, positions, missing and conflicting data) and the ledgers."""
        return _paper_detail(paper_id)

    @app.post("/api/paper/{paper_id}/sessions", response_model=list[dict[str, Any]], tags=["paper"])
    def paper_deliver(paper_id: str, body: PaperDelivery) -> list[dict[str, Any]]:
        """Deliver sessions from import-format CSV text, one event per date."""
        try:
            frame = read_import_file(io.StringIO(body.csv))  # type: ignore[arg-type]
            out: list[dict[str, Any]] = jsonable(lab.paper_ingest_sessions(paper_id, frame))
        except KeyError:
            raise HTTPException(404, f"paper account {paper_id} not found") from None
        except Exception as exc:
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
        return out

    @app.post("/api/paper/{paper_id}/fetch", response_model=list[dict[str, Any]], tags=["paper"])
    def paper_fetch(paper_id: str) -> list[dict[str, Any]]:
        """Fetch completed bars after the account's frontier from Equibles and deliver them."""
        try:
            lab.paper.store.account(paper_id)
        except KeyError:
            raise HTTPException(404, f"paper account {paper_id} not found") from None
        try:
            out = lab.paper_advance(paper_id)
        except Exception as exc:  # provider errors are shown to the person
            raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
        ingested: list[dict[str, Any]] = out["ingested"]
        return ingested

    @app.post("/api/paper/{paper_id}/replay", response_model=dict[str, Any], tags=["paper"])
    def paper_replay(paper_id: str) -> dict[str, Any]:
        """Rebuild the account from its recorded inputs and compare with the live record."""
        try:
            out: dict[str, Any] = jsonable(lab.paper_replay(paper_id))
        except KeyError:
            raise HTTPException(404, f"paper account {paper_id} not found") from None
        return out

    @app.get("/api/gates", response_model=list[GateOut], tags=["meta"])
    def gates() -> list[GateOut]:
        """The MVP acceptance gates as recorded in ``moneylab.acceptance``."""
        from moneylab.acceptance import GATES, REPORTS

        return [
            GateOut(
                number=n,
                title=title,
                passing=not missing,
                evidence=[*tests, *([REPORTS[n]] if n in REPORTS else [])],
                depends_on=missing,
            )
            for n, title, tests, missing in GATES
        ]

    # Dashboard -----------------------------------------------------------------------
    web = web_dir or Path(os.environ.get("MONEYLAB_WEB_DIR", DEFAULT_WEB_DIR))
    if web.is_dir():
        app.mount("/_next", StaticFiles(directory=web / "_next"), name="next-assets")

        @app.get("/{path:path}", include_in_schema=False)
        def page(path: str) -> FileResponse:
            """Serve the exported pages: /compare -> compare.html, / -> index.html."""
            if path.startswith("api/"):
                raise HTTPException(404)
            target = (web / path).resolve()
            if not str(target).startswith(str(web.resolve())):
                raise HTTPException(404)
            for cand in (target, target.with_suffix(".html"), target / "index.html"):
                if cand.is_file():
                    return FileResponse(cand)
            raise HTTPException(404)

    return app
