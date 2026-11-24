"""One command for the acceptance checks: ``uv run moneylab acceptance``.

Runs the test suite and prints the status of each of the six MVP gates. A gate is
PASS only when every check it needs exists and passes. Gates without complete
evidence are PENDING and name what they still depend on.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# gate -> (title, tests that provide partial or full evidence, still-missing dependency)
GATES: list[tuple[int, str, list[str], str]] = [
    (
        1,
        "No-code configuration and extensibility",
        ["tests/test_dashboard_rsi_path.py"],
        "MACD registered with zero dashboard/simulator/ledger edits; paper mode",
    ),
    (
        2,
        "Capacity and speed",
        [],
        "250-symbol x 5-year dataset, parallel worker pool, timed 10-variant batches",
    ),
    (
        3,
        "Research usability",
        ["tests/test_dashboard_rsi_path.py"],
        "universe change in the same flow; timed five-minute walkthrough",
    ),
    (
        4,
        "Correctness and forward replay",
        ["tests/test_rsi_divergence.py"],
        "fixtures for gaps, insufficient cash, simultaneous signals, corporate action; paper mode "
        "and a real observed market session",
    ),
    (
        5,
        "Universe and source flexibility",
        [],
        "live provider adapter (Tiingo) and a second watchlist run",
    ),
    (
        6,
        "Historical inspection and reproducibility",
        ["tests/test_rsi_divergence.py"],
        "changed-default reopen check and unchanged-duplicate ledger reproduction test",
    ),
]


def main() -> int:
    tests = sorted({t for _, _, ts, _ in GATES for t in ts})
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", *tests], cwd=ROOT)
    print("\nAcceptance gates")
    for n, title, ts, missing in GATES:
        # No gate has complete evidence yet, so the best status today is PENDING.
        status = "FAIL" if code != 0 and ts else "PENDING"
        evidence = ", ".join(ts) or "none yet"
        print(f"  Gate {n} [{status}] {title}")
        print(f"      evidence: {evidence}\n      depends on: {missing}")
    return code
