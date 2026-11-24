"""Command line: ``uv run moneylab --help``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)
ROOT = Path(__file__).resolve().parents[2]


@app.command()
def dashboard(port: int = 8501) -> None:
    """Start the dashboard at http://localhost:PORT."""
    target = Path(__file__).parent / "dashboard" / "app.py"
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "streamlit", "run", str(target), "--server.port", str(port)]
        )
    )


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
    symbols: list[str],
    start: str = typer.Option(..., help="first date, YYYY-MM-DD"),
    end: str = typer.Option(..., help="last date, YYYY-MM-DD"),
    label: str = "",
    refresh: bool = typer.Option(False, help="ignore the response cache"),
) -> None:
    """Fetch daily history from Tiingo (needs TIINGO_API_KEY) as an immutable snapshot."""
    from datetime import date

    from moneylab.data.providers import ProviderError
    from moneylab.service import Lab

    try:
        snap = Lab.open().import_provider(
            symbols, date.fromisoformat(start), date.fromisoformat(end), label, refresh=refresh
        )
    except (ProviderError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    p = snap.meta.get("provenance", {})
    typer.echo(
        f"snapshot {snap.id}: {len(snap.meta['symbols'])} symbols, {snap.meta['rows']} rows, "
        f"{snap.meta['first_date']}..{snap.meta['last_date']} "
        f"(source {snap.meta['source']}, adjustment {snap.meta['adjustment']}, "
        f"http requests {p.get('http_requests', '?')}, cache hits {p.get('cache_hits', '?')})"
    )
    spread = p.get("tiingo_adj_ratio_spread")
    if spread:
        typer.echo(f"Tiingo adjClose vs lab adjustment, relative spread: {spread}")


@app.command("new-sessions")
def new_sessions_cmd(snapshot_id: str) -> None:
    """Show sessions Tiingo has that are newer than SNAPSHOT_ID (nothing is stored)."""
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


@app.command()
def acceptance() -> None:
    """Run the acceptance checks and print each gate's status."""
    from moneylab.acceptance import main

    raise SystemExit(main())


if __name__ == "__main__":
    app()
