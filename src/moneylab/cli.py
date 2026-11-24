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


@app.command()
def acceptance() -> None:
    """Run the acceptance checks and print each gate's status."""
    from moneylab.acceptance import main

    raise SystemExit(main())


if __name__ == "__main__":
    app()
