"""Command line: ``uv run moneylab --help``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
def import_file(path: Path, label: str = "") -> None:
    """Import a CSV in the documented file format as an immutable snapshot."""
    from moneylab.service import Lab

    snap = Lab.open().import_file(path, label=label)
    typer.echo(f"snapshot {snap.id}: {len(snap.meta['symbols'])} symbols, {snap.meta['rows']} rows")


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
