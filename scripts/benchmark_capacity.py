"""Acceptance gate 2 benchmark: ten variants x (250 stocks + SPY) x five years.

Steps:

1. Build the deterministic dataset (``scripts/make_capacity_dataset.py``) if it is not
   there yet and import it as a snapshot into a fresh benchmark home under ``var/``.
2. Run three consecutive concurrent batches of the ten variants. Each batch uses a new
   ``Lab`` (no in-memory data cache carried over) and finished results are never
   reused. A batch is timed from submission until all ten runs are saved and the
   comparisons of variant 1 with each other variant are available.
3. Run the same ten variants sequentially (one worker) and check that every
   per-variant ledger (decisions, fills, equity) equals the concurrent one.
4. Write ``docs/benchmarks/capacity.md``.

Run: ``uv run python scripts/benchmark_capacity.py [--workers N]``.
"""

from __future__ import annotations

import argparse
import os
import platform
import resource
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_capacity_dataset as dataset

from moneylab.engine.batch import resolve_workers
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
)
from moneylab.service import Lab

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "var" / "capacity" / "bench-home"
REPORT = ROOT / "docs" / "benchmarks" / "capacity.md"
LIMIT_S = 120.0
BATCHES = 3


def cross(length: int, direction: str = "above") -> Condition:
    return Condition(
        kind="price_cross",
        indicator=IndicatorRef(id="sma", params={"length": length}),
        params={"direction": direction},
    )


def rsi(op: str, value: float, length: int = 14) -> Condition:
    return Condition(
        kind="threshold",
        indicator=IndicatorRef(id="rsi", params={"length": length}),
        params={"op": op, "value": value},
    )


def hold(n: int) -> ExitRule:
    return ExitRule(kind="holding_period", params={"sessions": n})


def signal(*conds: Condition) -> ExitRule:
    return ExitRule(kind="signal", conditions=list(conds))


def variants(universe: list[str]) -> list[StrategyConfig]:
    specs: list[tuple[str, list[Condition], ExitRule, dict[str, Any]]] = [
        ("V01 SMA20 cross, hold 10", [cross(20)], hold(10), {}),
        ("V02 SMA20 cross + RSI<40, hold 10", [cross(20), rsi("below", 40)], hold(10), {}),
        ("V03 SMA20 cross + RSI<50, hold 10", [cross(20), rsi("below", 50)], hold(10), {}),
        ("V04 SMA50 cross, exit SMA50 cross below", [cross(50)], signal(cross(50, "below")), {}),
        (
            "V05 SMA10 cross + RSI<60, hold 5, max 20",
            [cross(10), rsi("below", 60)],
            hold(5),
            {"max_positions": 20, "target_allocation": 0.05},
        ),
        ("V06 RSI<30, hold 5", [rsi("below", 30)], hold(5), {}),
        ("V07 RSI<25, exit RSI>55", [rsi("below", 25)], signal(rsi("above", 55)), {}),
        (
            "V08 SMA20 cross + RSI>55, exit SMA20 cross below, max 5",
            [cross(20), rsi("above", 55)],
            signal(cross(20, "below")),
            {"max_positions": 5, "target_allocation": 0.2},
        ),
        ("V09 SMA100 cross, hold 20, max 15", [cross(100)], hold(20), {"max_positions": 15}),
        (
            "V10 SMA20 cross + RSI<35, hold 20, max 3",
            [cross(20), rsi("below", 35)],
            hold(20),
            {"max_positions": 3, "target_allocation": 0.3},
        ),
    ]
    return [
        StrategyConfig(
            name=name,
            universe=universe,
            benchmark="SPY",
            entry=entry,
            exit=ex,
            portfolio=PortfolioSettings(**port),
        )
        for name, entry, ex, port in specs
    ]


def timed_batch(
    sid: str, configs: list[StrategyConfig], workers: int, compare: bool
) -> tuple[list[str], dict[str, float]]:
    lab = Lab.open(HOME)  # fresh Lab: no market data carried over in memory
    t0 = time.perf_counter()
    phases: dict[str, float] = {}
    ids = lab.run_batch(configs, sid, workers=workers, timings=phases)
    t_saved = time.perf_counter()
    if compare:
        for other in ids[1:]:
            lab.compare(ids[0], other)
        phases["compare"] = time.perf_counter() - t_saved
    phases["total"] = time.perf_counter() - t0
    return ids, phases


def ledgers_equal(lab: Lab, a: str, b: str) -> bool:
    la, lb = lab.store.ledgers(a), lab.store.ledgers(b)
    try:
        pd.testing.assert_frame_equal(la.decisions, lb.decisions)
        pd.testing.assert_frame_equal(la.fills, lb.fills)
        pd.testing.assert_frame_equal(la.equity, lb.equity)
    except AssertionError:
        return False
    return True


def machine() -> dict[str, Any]:
    cpu = platform.processor() or "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    return {
        "cpu": cpu,
        "cpus": os.cpu_count(),
        "ram_gb": ram_gb,
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "load": os.getloadavg(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=None, help="default: settings.workers")
    ap.add_argument("--report", type=Path, default=REPORT)
    args = ap.parse_args()

    csv = dataset.DEFAULT_OUT
    if not csv.exists():
        dataset.write(csv)
    if HOME.exists():
        shutil.rmtree(HOME)
    lab = Lab.open(HOME)
    t = time.perf_counter()
    snap = lab.import_file(csv, label="capacity 250x5y")
    import_s = time.perf_counter() - t
    parquet = lab.settings.snapshot_dir / f"{snap.id}.parquet"
    universe = dataset.symbols()
    configs = variants(universe)
    requested = lab.settings.workers if args.workers is None else args.workers
    workers = resolve_workers(requested, len(configs))
    mach = machine()
    sessions = len(lab.market_data(snap.id, [*universe, "SPY"], None, None).sessions)

    batches = []
    for n in range(1, BATCHES + 1):
        ids, ph = timed_batch(snap.id, configs, workers, compare=True)
        batches.append((ids, ph))
        print(f"batch {n}: {ph['total']:.2f}s  " + "  ".join(f"{k}={v:.2f}" for k, v in ph.items()))
    seq_ids, seq_ph = timed_batch(snap.id, configs, 1, compare=False)
    print(f"sequential: {seq_ph['total']:.2f}s")

    check = Lab.open(HOME)
    equal = [ledgers_equal(check, a, b) for a, b in zip(batches[0][0], seq_ids, strict=True)]
    repeat_equal = all(
        ledgers_equal(check, a, b)
        for ids, _ in batches[1:]
        for a, b in zip(batches[0][0], ids, strict=True)
    )
    stats = []
    for cfg, rid in zip(configs, batches[0][0], strict=True):
        led = check.store.ledgers(rid)
        acts = led.decisions["action"].value_counts()
        stats.append(
            (
                cfg.name,
                len(led.decisions),
                int(acts.get("enter", 0)),
                int(acts.get("skip", 0)),
                len(led.fills),
                float(led.equity["equity"].iloc[-1]),
            )
        )
    rss_self = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    rss_child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    runs_mb = sum(p.stat().st_size for p in (HOME / "runs").rglob("*.parquet")) / 1e6
    all_under = all(ph["total"] < LIMIT_S for _, ph in batches)
    passed = all_under and all(equal) and repeat_equal

    lines = [
        "# Capacity benchmark (acceptance gate 2)",
        "",
        f"Generated by `uv run python scripts/benchmark_capacity.py` on "
        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        f"**Result: {'PASS' if passed else 'FAIL'}** - "
        f"{'all' if all_under else 'not all'} three batches under {LIMIT_S:.0f} s; "
        f"sequential and concurrent ledgers {'identical' if all(equal) else 'DIFFERENT'} "
        f"for {sum(equal)}/{len(equal)} variants.",
        "",
        "## Machine",
        "",
        "| Item | Value |",
        "| --- | --- |",
        f"| CPU | {mach['cpu']} |",
        f"| CPUs | {mach['cpus']} |",
        f"| RAM | {mach['ram_gb']:.1f} GiB |",
        f"| OS / Python | {mach['os']} / {mach['python']} |",
        f"| Load average at start (1/5/15 min) | {' / '.join(f'{x:.2f}' for x in mach['load'])} |",
        f"| Workers | {workers} (settings.workers={requested}; 0 = one per CPU) |",
        f"| Peak RSS | parent {rss_self:.0f} MB, largest worker {rss_child:.0f} MB |",
        "",
        "The machine was shared with other agents running test suites at the same time, so "
        "timings include some noise from that load.",
        "",
        "## Dataset",
        "",
        "| Item | Value |",
        "| --- | --- |",
        f"| Symbols | {len(universe)} stocks + SPY |",
        f"| Sessions | {sessions} XNYS sessions, {dataset.START} to {dataset.END} |",
        f"| Rows | {snap.meta['rows']:,} (a few missing bars and five late listings) |",
        f"| Import CSV | {csv.stat().st_size / 1e6:.1f} MB |",
        f"| Snapshot Parquet | {parquet.stat().st_size / 1e6:.1f} MB |",
        f"| CSV import (not part of a batch) | {import_s:.2f} s |",
        f"| Ledgers written by all batches | {runs_mb:.1f} MB Parquet |",
        "",
        "## Batches",
        "",
        "Each batch: submit ten variants with `Lab.run_batch` (finished-result reuse is "
        "not implemented, every variant is simulated), wait until all ten runs are saved, "
        "then `Lab.compare` variant 1 with each of the other nine. A new `Lab` per batch "
        "means the snapshot is re-read and the market data re-aligned every time.",
        "",
        "| Batch | resolve | load data | simulate + write ledgers | save records | "
        "compare x9 | total | < 120 s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for n, (_, ph) in enumerate(batches, 1):
        lines.append(
            f"| {n} | {ph['resolve']:.2f} s | {ph['load_data']:.2f} s | "
            f"{ph['simulate_and_write_ledgers']:.2f} s | {ph['save_records']:.2f} s | "
            f"{ph['compare']:.2f} s | **{ph['total']:.2f} s** | "
            f"{'yes' if ph['total'] < LIMIT_S else 'NO'} |"
        )
    lines += [
        "",
        f"Sequential run of the same ten variants (1 worker, no comparisons): "
        f"{seq_ph['total']:.2f} s (simulate + write ledgers "
        f"{seq_ph['simulate_and_write_ledgers']:.2f} s).",
        "",
        "## Sequential vs concurrent ledgers",
        "",
        "Decisions, fills and equity of each variant from the sequential run compared with "
        "batch 1 (`pandas.testing.assert_frame_equal`, exact): "
        f"**{sum(equal)}/{len(equal)} identical**. Batches 2 and 3 also match batch 1: "
        f"**{'yes' if repeat_equal else 'NO'}**.",
        "",
        "| Variant | Decision rows | Entries | Skips | Fills | Final equity | Seq = conc |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (name, rows, ent, sk, fills, final), ok in zip(stats, equal, strict=True):
        lines.append(
            f"| {name} | {rows:,} | {ent} | {sk} | {fills} | {final:,.2f} | "
            f"{'yes' if ok else 'NO'} |"
        )
    lines.append("")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines))
    print(f"wrote {args.report}: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
