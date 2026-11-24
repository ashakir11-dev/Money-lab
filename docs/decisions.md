# Stack and execution decisions

The spec leaves the stack open. These are the defaults chosen.

| Area | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12, managed by uv | best ecosystem for numerical research; uv gives one-command, locked installs |
| Storage | SQLite (run records) + Parquet (data snapshots and ledgers) | local, zero-ops, persistent; Parquet keeps 250 x 5 years compact and fast |
| Dashboard | Streamlit | controls can be generated from component declarations at runtime; headless `AppTest` lets tests drive the real UI |
| Calendar | `exchange-calendars` (XNYS) | lookbacks and holding periods count exchange sessions |
| Live provider | Tiingo End-of-Day (`httpx`) | free tier with raw daily bars plus dividends and splits; key goes in `TIINGO_API_KEY`; see [providers](providers.md) |
| Tooling | ruff, mypy (strict), pytest, GitHub Actions | |

No web API layer yet: the dashboard calls `moneylab.service.Lab` in-process. Each layer
(data, components, engine, reporting, store, dashboard) is tested on its own.

## Execution rules implemented

* Signals are evaluated after a completed close; orders fill at the next session with a
  bar for that symbol, at its open.
* Entry size: `floor(target_allocation * equity_at_decision_close / decision_close)`.
* Exits are filled before entries. Competing entries are processed in ascending symbol
  order (documented tie-break).
* Costs: `cost_bps_per_side` of gross notional, rounded half-up to the cent, on every fill.
  Cash and equity are also kept to the cent, rounded half-up.
* Cash can never go negative. If an opening gap makes an entry unaffordable, quantity
  is reduced to the largest affordable whole number and the fill carries a note; zero
  shares records an `insufficient_cash_at_open` skip.
* Every candidate is recorded: `enter`, `exit`, `skip` (with reason: `max_positions`,
  `insufficient_cash`, `already_held`, `zero_quantity`, `insufficient_cash_at_open`), and
  diagnostics `no_signal` (some but not all entry conditions passed) and `ineligible`
  (`warmup` or `missing_bar`). Comparisons match trading decisions (`enter`, `exit`,
  `skip`) and show the diagnostic row for the other side.
* Holding-period exit: signalled at the close of the Nth session after the fill.
* The benchmark is bought and held with the same starting cash over the same sessions.
* Metrics are computed only after a run completes, from its ledgers.
* Corporate actions: raw prices plus an actions file (splits, cash dividends) are
  adjusted once at import (`moneylab.data.actions`); the snapshot records the policy.
  The Tiingo adapter turns `splitFactor`/`divCash` into the same actions frame and
  uses the same function, so both adapters give the same snapshot for the same data.
* Watchlists are named, labelled and versioned in SQLite; a run on a watchlist records
  the version and members it used.
  Ledger quantities are in adjusted shares of the snapshot used.
* Reproduction: `Lab.duplicate` re-runs the stored resolved settings on the stored
  snapshot and refuses if any component or the engine version changed.
* The simulator is an `Account` stepped one session at a time over a read-only
  `MarketView`; its state serialises to JSON so paper mode can resume after a restart.
* Components are auto-discovered: every module in `moneylab/components/` is imported,
  so a new indicator is one new file calling `register_indicator`.
* Paper mode freezes a completed run and steps the same `Account` one delivered session
  at a time; deliveries are idempotent, conflicts are rejected, a missing session makes
  processing wait. See [paper-trading.md](paper-trading.md).

## Batches of variants (gate 2)

* `Lab.run_batch(configs, snapshot_id, workers=..., parent_ids=None)` runs several
  variants and returns their run ids in order. `workers` defaults to
  `MONEYLAB_WORKERS` (0 = one per CPU); 1 runs sequentially in-process.
* Immutable inputs are shared: market data is built once per date range in the parent
  and inherited copy-on-write by `fork`ed workers (`moneylab.engine.batch`). Where
  `fork` is unavailable each worker gets one pickled copy at start-up.
* Mutable state is isolated: each variant has its own `Account`, so its symbols compete
  only for its own cash and position slots.
* Workers write their run's Parquet ledgers into a fresh run directory; only the parent
  writes SQLite, in one transaction per batch.
* Finished results are never reused: every submitted variant is simulated.
* The simulator precomputes, per account and view, which symbols can record anything at
  each session (whole-array condition checks) and builds the detailed condition rows
  only for those. Decisions, fills, skip reasons and equity are unchanged.
* Benchmark: `uv run python scripts/benchmark_capacity.py` writes
  [benchmarks/capacity.md](benchmarks/capacity.md).
