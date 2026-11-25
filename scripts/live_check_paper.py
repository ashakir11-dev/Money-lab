"""Gate 4 live check: one genuinely new market session processed by a paper account.

    uv run python scripts/live_check_paper.py start 2026-11-24   # history to that session
    uv run python scripts/live_check_paper.py ingest              # fetch and process new sessions

Needs TIINGO_API_KEY. Strategy: RSI14 < 30 on SPY's end-2017 top 20, 10-session hold
(scripts/research_rsi_spy20.py). Prints the status, ledgers and a replay check as JSON.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from typing import Any

from moneylab.service import Lab

sys.path.insert(0, "scripts")
from research_rsi_spy20 import UNIVERSE, config

NAME = "gate 4 live check"


def account(lab: Lab) -> dict[str, Any]:
    pid = next(a["id"] for a in lab.paper_accounts() if a["name"] == NAME)
    return lab.paper.store.account(pid)


def start(last: date) -> None:
    lab = Lab.open()
    snap = lab.import_provider(
        [*UNIVERSE, "SPY"], last - timedelta(days=200), last, label=f"gate 4 history to {last}"
    )
    cfg = config(30, 10).model_copy(update={"start": None, "end": None})
    run_id = lab.run(cfg, snap.id)
    pid = lab.start_paper(run_id, last, NAME)
    print(json.dumps({"snapshot": snap.id, "run": run_id, "paper": pid,
                      "status": lab.paper_status(pid)}, indent=1, default=str))  # fmt: skip


def ingest() -> None:
    lab = Lab.open()
    acct = account(lab)
    pid = acct["id"]
    new = lab.fetch_new_sessions(acct["snapshot_id"])
    sessions = sorted(new.bars["date"].astype(str).unique())
    print("new sessions:", sessions, "actions:", len(new.actions))
    res = lab.paper_ingest_sessions(pid, new.bars) if sessions else []
    led = lab.paper_ledgers(pid)
    out = {
        "ingest": res,
        "status": lab.paper_status(pid),
        "decisions": led.decisions.astype(str).to_dict("records"),
        "fills": led.fills.astype(str).to_dict("records"),
        "equity": led.equity.astype(str).to_dict("records"),
        "replay_match": lab.paper_replay(pid).get("match"),
    }
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    {"start": lambda: start(date.fromisoformat(sys.argv[2])), "ingest": ingest}[sys.argv[1]]()
