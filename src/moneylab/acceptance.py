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
        [
            "tests/test_dashboard_rsi_path.py",
            "tests/test_macd.py",
            "tests/test_dashboard_macd.py",
        ],
        "paper mode check once paper trading lands",
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
        [
            "tests/test_engine_fixtures.py",
            "tests/test_rsi_divergence.py",
            "tests/test_account_state.py",
        ],
        "paper mode, replay, duplicate delivery, and a real observed market session",
    ),
    (
        5,
        "Universe and source flexibility",
        ["tests/test_source_flexibility.py", "tests/test_tiingo_provider.py"],
        "live Tiingo check in a session that has TIINGO_API_KEY "
        "(uv run pytest -m network -q tests/test_tiingo_live.py)",
    ),
    (
        6,
        "Historical inspection and reproducibility",
        ["tests/test_reproducibility.py", "tests/test_rsi_divergence.py"],
        "",
    ),
]


def main() -> int:
    tests = sorted({t for _, _, ts, _ in GATES for t in ts})
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", *tests], cwd=ROOT)
    print("\nAcceptance gates")
    for n, title, ts, missing in GATES:
        status = "FAIL" if code != 0 and ts else ("PENDING" if missing else "PASS")
        evidence = ", ".join(ts) or "none yet"
        print(f"  Gate {n} [{status}] {title}")
        print(f"      evidence: {evidence}\n      depends on: {missing or 'nothing'}")
    return code
