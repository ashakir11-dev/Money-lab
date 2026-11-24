"""Run the RSI demo in a throwaway lab and write both ledgers plus the comparison.

Usage: uv run python scripts/export_demo_ledgers.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from moneylab.demo import run_rsi_demo
from moneylab.service import Lab


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as home:
        d = run_rsi_demo(Lab.open(Path(home)))
        lab = d["lab"]
        for tag in ("original", "changed"):
            led = lab.store.ledgers(d[tag])
            led.fills.to_csv(out / f"{tag}_fills.csv", index=False)
            dec = led.decisions[led.decisions["action"].isin(["enter", "exit", "skip"])]
            dec = dec[["session", "symbol", "action", "reason", "qty"]]
            dec.to_csv(out / f"{tag}_decisions.csv", index=False)
            led.equity.to_csv(out / f"{tag}_equity.csv", index=False)
        cmp = lab.compare(d["original"], d["changed"])
        summary = {
            "original_run": d["original"],
            "changed_run": d["changed"],
            "snapshot": d["snapshot"],
            "config_diff": cmp["config_diff"],
            "first_difference": cmp["first_difference"],
            "metrics": {"original": cmp["a"]["metrics"], "changed": cmp["b"]["metrics"]},
        }
        (out / "comparison.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"wrote ledgers to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "var/demo-export"))
