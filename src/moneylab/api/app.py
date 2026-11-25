"""FastAPI application: the research API plus the built dashboard, on one address.

Start it with ``uv run moneylab serve``. ``create_app(lab)`` builds an app around a
``Lab``; tests pass their own. Every number the dashboard shows comes from here.
"""

from __future__ import annotations

import io
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from moneylab import ENGINE_VERSION
from moneylab.api.jobs import Job, JobRunner
from moneylab.components.registry import list_components
from moneylab.data.snapshot import list_snapshots, read_import_file
from moneylab.describe import data_source, strategy_summary
from moneylab.engine.config import StrategyConfig, VersionMismatch, check_versions
from moneylab.service import Lab

API_VERSION = "1"
#: Where ``npm run build`` in ``frontend/`` writes the static dashboard.
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[3] / "frontend" / "out"


# JSON helpers --------------------------------------------------------------------------
def jsonable(obj: Any) -> Any:
    """Plain JSON values: NaN/inf become null, numpy and pandas scalars become Python."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [jsonable(v) for v in obj.tolist()]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, pd.Timestamp | date):
        return obj.isoformat()[:10]
    if obj is pd.NaT:
        return None
    return obj


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
    symbols: list[str] = Field(min_length=1)
    start: date
    end: date
    label: str = ""


class CsvImport(BaseModel):
    csv: str = Field(min_length=1, description="date,symbol,open,high,low,close,volume")
    filename: str = "upload.csv"
    label: str = ""


class RunSubmit(BaseModel):
    config: StrategyConfig
    snapshot_id: str
    parent_id: str | None = None
    watchlist: str | None = None


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


class RunSummary(BaseModel):
    id: str
    parent_id: str | None
    name: str
    created_at: str
    status: str
    snapshot_id: str
    source_kind: Literal["real", "demo", "file"]
    source_label: str
    summary: str
    total_return: float | None
    benchmark_return: float | None
    max_drawdown: float | None
    sharpe: float | None
    trades: int | None
    open_positions: int | None


class RunDetail(RunSummary):
    engine_version: str
    config_hash: str
    finished_at: str | None
    resolved: dict[str, Any]
    metrics: dict[str, Any]


class EquityRow(BaseModel):
    session: str
    cash: float
    positions_value: float
    equity: float
    positions: int
    benchmark_equity: float | None = None


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
        adjustment=meta.get("adjustment", ""),
        created_at=meta.get("created_at", ""),
        symbols=meta.get("symbols", []),
        first_date=meta.get("first_date", ""),
        last_date=meta.get("last_date", ""),
        rows=int(meta.get("rows", 0)),
    )


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and math.isfinite(v) else None


def _summary(row: dict[str, Any], full: dict[str, Any]) -> dict[str, Any]:
    m = full.get("metrics") or {}
    res = full.get("resolved") or {}
    ds = data_source((res.get("data") or {}).get("source"))
    bench = m.get("benchmark") or {}
    return {
        "id": row["id"],
        "parent_id": row.get("parent_id"),
        "name": row["name"],
        "created_at": row["created_at"],
        "status": row["status"],
        "snapshot_id": row["snapshot_id"],
        "source_kind": ds.kind,
        "source_label": ds.badge,
        "summary": strategy_summary(res),
        "total_return": _num(m.get("total_return")),
        "benchmark_return": _num(bench.get("total_return")),
        "max_drawdown": _num(m.get("max_drawdown")),
        "sharpe": _num(m.get("sharpe")),
        "trades": m.get("trades"),
        "open_positions": m.get("open_positions"),
    }


def _detail(lab: Lab, run_id: str) -> RunDetail:
    try:
        full = lab.store.get(run_id)
    except KeyError:
        raise HTTPException(404, f"run {run_id} not found") from None
    return RunDetail(
        **_summary(full, full),
        engine_version=full["engine_version"],
        config_hash=full["config_hash"],
        finished_at=full.get("finished_at"),
        resolved=jsonable(full["resolved"]),
        metrics=jsonable(full["metrics"]),
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
            resolved = resolve(body.config, lab.settings)
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
                [s.strip().upper() for s in body.symbols if s.strip()],
                body.start,
                body.end,
                label=body.label,
            )
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

    # Runs ----------------------------------------------------------------------------
    @app.get("/api/runs", response_model=list[RunSummary], tags=["runs"])
    def runs() -> list[dict[str, Any]]:
        return [_summary(r, lab.store.get(r["id"])) for r in lab.store.list_runs()]

    @app.post("/api/runs", response_model=JobOut, status_code=202, tags=["runs"])
    def submit_run(body: RunSubmit) -> JobOut:
        """Queue one run. Returns a job at once; poll ``/api/jobs/{id}`` for its run id."""
        try:
            lab.snapshot(body.snapshot_id)
        except FileNotFoundError:
            raise HTTPException(404, f"snapshot {body.snapshot_id} not found") from None
        if body.parent_id is not None:
            _detail(lab, body.parent_id)
        validate(body)
        job = jobs.submit(
            "run",
            lambda: [
                lab.run(body.config, body.snapshot_id, body.parent_id, watchlist=body.watchlist)
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

    @app.get("/api/runs/{run_id}/fills", response_model=list[FillRow], tags=["runs"])
    def fills(run_id: str) -> list[dict[str, Any]]:
        _detail(lab, run_id)
        return records(lab.store.ledgers(run_id).fills)

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
        d = lab.store.ledgers(run_id).decisions
        if action:
            d = d[d["action"].isin(action)]
        if symbol:
            d = d[d["symbol"] == symbol.upper()]
        if session:
            d = d[d["session"] == session]
        page = d.iloc[offset : offset + limit]
        return DecisionPage(total=len(d), offset=offset, limit=limit, rows=jsonable(records(page)))

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
