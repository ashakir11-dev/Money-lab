"""Command line: ``uv run moneylab --help``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)
ROOT = Path(__file__).resolve().parents[2]


@app.command()
def serve(
    port: int = 8000,
    host: str = "127.0.0.1",
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = False,
) -> None:
    """Start the lab at http://HOST:PORT: the research API and the built dashboard.

    Build the dashboard once with ``npm --prefix frontend run build`` (``make web``).
    Runs keep going in the server even if the browser tab is closed.
    """
    import uvicorn

    from moneylab.api.app import DEFAULT_WEB_DIR, create_app

    if not DEFAULT_WEB_DIR.is_dir():
        typer.echo(
            f"note: no built dashboard at {DEFAULT_WEB_DIR}; serving the API only "
            "(run `make web` to build it)"
        )
    url = f"http://{host}:{port}"
    typer.echo(f"Money-lab at {url}  (API docs at {url}/docs)")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@app.command("import")
def import_file(
    path: Path,
    label: str = "",
    actions: Annotated[
        Path | None, typer.Option(help="actions CSV; PATH then holds raw prices")
    ] = None,
) -> None:
    """Import a CSV in the documented file format as an immutable snapshot."""
    from moneylab.service import Lab

    snap = Lab.open().import_file(path, label=label, actions_path=actions)
    typer.echo(f"snapshot {snap.id}: {len(snap.meta['symbols'])} symbols, {snap.meta['rows']} rows")


@app.command()
def fetch(
    symbols: Annotated[
        list[str] | None, typer.Argument(help="extra symbols; omit to fetch the watchlist only")
    ] = None,
    start: str = typer.Option(..., help="first date, YYYY-MM-DD"),
    end: str = typer.Option(..., help="last date, YYYY-MM-DD"),
    label: str = "",
    refresh: bool = typer.Option(False, help="ignore the response cache"),
    watchlist: Annotated[
        str,
        typer.Option(help="fetch this watchlist's current members; SYMBOLS are added"),
    ] = "",
    timeframe: str = typer.Option("1h", help="1min, 5min, 15min, 30min, 1h, or 1d"),
) -> None:
    """Fetch history from Equibles (needs EQUIBLES_API_KEY) as an immutable snapshot.

    1d is resampled from hourly bars. Any other timeframe is stored as those bars.
    """
    from datetime import date

    from moneylab.data.providers import ProviderError
    from moneylab.data.snapshot import snapshot_name
    from moneylab.service import Lab

    try:
        snap = Lab.open().import_provider(
            symbols or [],
            date.fromisoformat(start),
            date.fromisoformat(end),
            label,
            refresh=refresh,
            watchlist=watchlist or None,
            timeframe=timeframe,
        )
    except (ProviderError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    p = snap.meta.get("provenance", {})
    typer.echo(
        f"{snapshot_name(snap.meta)}\n"
        f"snapshot {snap.id}: {len(snap.meta['symbols'])} symbols, {snap.meta['rows']} rows, "
        f"{snap.meta['first_date']}..{snap.meta['last_date']} "
        f"(source {snap.meta['source']}, adjustment {snap.meta['adjustment']}, "
        f"timeframe {snap.meta.get('timeframe', '1d')}, "
        f"http requests {p.get('http_requests', '?')}, cache hits {p.get('cache_hits', '?')})"
    )


@app.command("new-sessions")
def new_sessions_cmd(snapshot_id: str) -> None:
    """Show sessions Equibles has that are newer than SNAPSHOT_ID (nothing is stored)."""
    from moneylab.data.providers import ProviderError
    from moneylab.service import Lab

    try:
        new = Lab.open().fetch_new_sessions(snapshot_id)
    except (ProviderError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(new.bars.to_csv(index=False, date_format="%Y-%m-%d"), nl=False)
    if new.basis_changed:
        typer.echo("corporate actions in the window:", err=True)
        typer.echo(new.actions.to_csv(index=False, date_format="%Y-%m-%d"), err=True, nl=False)


@app.command()
def watchlist(
    name: str,
    symbols: Annotated[
        list[str] | None, typer.Argument(help="members; omit to show the watchlist")
    ] = None,
    label: str = "",
) -> None:
    """Save NAME with SYMBOLS (a new version if it changed), or show it."""
    from moneylab.service import Lab

    lab = Lab.open()
    wl = lab.save_watchlist(name, symbols, label) if symbols else lab.watchlist(name)
    typer.echo(
        f"{wl['name']} v{wl['version']} ({wl['label'] or 'no label'}, {wl['created_at']}): "
        + " ".join(wl["members"])
    )


@app.command()
def demo() -> None:
    """Import the fixture and save the RSI<30 run and its RSI<35 duplicate."""
    from moneylab.demo import run_rsi_demo

    out = run_rsi_demo()
    typer.echo(f"original run {out['original']}, changed run {out['changed']}")


paper_app = typer.Typer(
    no_args_is_help=True, help="Paper forward testing of a frozen run (docs/paper-trading.md)."
)
app.add_typer(paper_app, name="paper")


@paper_app.command("start")
def paper_start(
    run_id: str,
    start: str = typer.Option("", help="last history session (default: the snapshot's last)"),
    name: str = "",
) -> None:
    """Freeze a completed run and open a paper account for the sessions after START."""
    from moneylab.service import Lab

    lab = Lab.open()
    pid = lab.start_paper(run_id, start or None, name)
    st = lab.paper_status(pid)
    typer.echo(f"paper account {pid}: history to {st['start_session']}, next {st['next_expected']}")


@paper_app.command("list")
def paper_list() -> None:
    """List paper accounts with their latest processed session."""
    from moneylab.service import Lab

    for a in Lab.open().paper_accounts():
        typer.echo(
            f"{a['id']}  {a['name']}  run {a['run_id']}  start {a['start_session']}  "
            f"latest {a['latest_session'] or '-'}  ({a['processed_sessions']} sessions)"
        )


@paper_app.command("ingest")
def paper_ingest(paper_id: str, path: Path) -> None:
    """Deliver the sessions in a CSV (import format), one event per session, in date order."""
    import pandas as pd

    from moneylab.service import Lab

    for r in Lab.open().paper_ingest_sessions(paper_id, pd.read_csv(path)):
        note = f": {r['detail']}" if r["detail"] != r["status"] else ""
        done = f"; processed {', '.join(r['processed'])}" if r["processed"] else ""
        typer.echo(f"{r['session']} {r['status']}{note}{done}")


@paper_app.command("status")
def paper_status(
    paper_id: str, as_of: str = typer.Option("", help="report gaps up to here")
) -> None:
    """Latest processed session, missing and conflicting data, positions and cash."""
    import json

    from moneylab.service import Lab

    typer.echo(json.dumps(Lab.open().paper_status(paper_id, as_of or None), indent=2))


@paper_app.command("replay")
def paper_replay(paper_id: str) -> None:
    """Replay the recorded inputs from the initial state; exit 1 unless it matches exactly."""
    import json

    from moneylab.service import Lab

    out = Lab.open().paper_replay(paper_id)
    typer.echo(json.dumps(out, indent=2, default=str))
    raise SystemExit(0 if out["match"] else 1)


@paper_app.command("advance")
def paper_advance(
    paper_id: str,
    end: str = typer.Option("", help="last session to fetch (default: today)"),
) -> None:
    """Fetch completed sessions after the account's frontier from Equibles and ingest them."""
    from datetime import date

    from moneylab.data.providers import ProviderError
    from moneylab.service import Lab

    try:
        out = Lab.open().paper_advance(paper_id, end=date.fromisoformat(end) if end else None)
    except (ProviderError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    if not out["sessions"]:
        typer.echo(f"no new sessions after {out['after']}")
    for r in out["ingested"]:
        note = f": {r['detail']}" if r["detail"] != r["status"] else ""
        done = f"; processed {', '.join(r['processed'])}" if r["processed"] else ""
        typer.echo(f"{r['session']} {r['status']}{note}{done}")
    st = out["status"]
    typer.echo(
        f"latest {st['latest_processed'] or '-'}  next {st['next_expected']}  equity {st['equity']}"
    )


@app.command()
def acceptance() -> None:
    """Run the acceptance checks and print each gate's status."""
    from moneylab.acceptance import main

    raise SystemExit(main())


if __name__ == "__main__":
    app()
