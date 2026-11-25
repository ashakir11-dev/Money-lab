# Stack and execution decisions

The spec leaves the stack open. These are the defaults chosen.

| Area | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12, managed by uv | best ecosystem for numerical research; uv gives one-command, locked installs |
| Storage | SQLite (run records) + Parquet (data snapshots and ledgers) | local, zero-ops, persistent; Parquet keeps 250 x 5 years compact and fast |
| Dashboard | Next.js (static export) served by FastAPI | controls are generated from component declarations at runtime; TypeScript types come from the API's OpenAPI schema; Playwright drives the real UI in tests. Replaced the first Streamlit dashboard on 2026-11-25 |
| Calendar | `exchange-calendars` (XNYS) | lookbacks and holding periods count exchange sessions |
| Live provider | Tiingo End-of-Day (`httpx`) | free tier with raw daily bars plus dividends and splits; key goes in `TIINGO_API_KEY`; see [providers](providers.md) |
| Tooling | ruff, mypy (strict), pytest, GitHub Actions | |

The dashboard talks only to the HTTP API (`moneylab.api`), which calls
`moneylab.service.Lab`; the browser never calculates a trade. Each layer (data,
components, engine, reporting, store, API, dashboard) is tested on its own.

## Execution rules implemented

* Signals are evaluated after a completed close; orders fill at the next session with a
  bar for that symbol, at its open.
* Entry size: the strategy's sizing rule (default `percent_of_equity`:
  `floor(target_allocation * equity_at_decision_close / decision_close)`).
* Exits are filled before entries. Competing entries are processed in ascending symbol
  order (documented tie-break), or by the strategy's `rank_by` indicator when it has one
  (missing values last, ties in symbol order).
* Costs: `cost_bps_per_side` of gross notional, rounded half-up to the cent, on every fill.
  Cash and equity are also kept to the cent, rounded half-up.
* Cash can never go negative. If an opening gap makes the ordered quantity unaffordable
  (quantity x open x (1 + cost) > cash), the whole order is skipped and recorded as
  `insufficient_cash_at_open` with the ordered quantity, the open, the estimated cost and
  the cash available. A smaller quantity is never bought (engine/0.2.0).
* Every candidate is recorded: `enter`, `exit`, `skip` (with reason: `max_positions`,
  `insufficient_cash`, `already_held`, `zero_quantity`, `insufficient_cash_at_open`), and
  diagnostics `no_signal` (some but not all entry conditions passed) and `ineligible`
  (`warmup` or `missing_bar`). Comparisons match trading decisions (`enter`, `exit`,
  `skip`) and show the diagnostic row for the other side.
* Holding-period exit: the fill session counts as holding session one, so with N
  sessions the exit is signalled at the close of the Nth session held (the fill session
  plus N - 1) and filled at the next open (engine/0.2.0).
* The benchmark is bought once with the same starting cash: whole shares at the open of
  the first session with a bar, paying the same cost per side, the rest kept as cash,
  and valued at each close. Its purchase session, price, shares, cost and leftover cash
  are stored in the run's metrics and shown on the run page.
* Metrics: drawdown from the running closing-equity peak; Sharpe from daily returns,
  sample standard deviation, risk-free 0, annualised by sqrt(252), and undefined (shown
  as "undefined") when returns never vary; trades are completed round trips, with
  positions still open at the end counted separately.
* The Compare page opens on the newest run against its parent (the experimental
  control); a run without a parent is compared with the run before it.
* A run on a watchlist shows the watchlist version and the date it was selected, since a
  list chosen today describes the past performance of today's picks.
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

## Breadth filter and which indicators a price crossing accepts

- `breadth(length)` is the fraction of eligible universe symbols whose close is above
  their own SMA(length). Eligible means the symbol has a bar that session and its SMA is
  defined (listed long enough, no gap in the window). It is computed once per run over the
  run's universe (not the benchmark) and every symbol sees the same value, so it acts as a
  market-wide filter when used with a threshold, for example "breadth above 0.5". With no
  eligible symbol it is undefined and the condition does not pass (decision: warmup).
- Cross-sectional indicators register with `register_universe_indicator`; the simulator
  computes them over all universe frames at once. Per-symbol indicators are unchanged.
- "Close crosses X" is only allowed when X is in price units (declared `price_scale`;
  today only SMA). Crossing RSI, MACD or breadth is rejected at validation, and the
  dashboard only offers price-unit indicators for crossings.
- Switching a condition's indicator in the builder redraws its controls from the new
  indicator's declared defaults instead of carrying over a same-named value.

## Plug-in exits, sizing and ranking (engine/0.3.0)

Exit rules and sizing rules are registered components, like indicators: one function
each, declared with an id, version, typed parameters, defaults and limits
(`register_exit`, `register_sizing` in `components/registry.py`), and listed by
`GET /api/components` with `uses` `exit_rule` or `sizing`.

* **Exits.** A strategy has a list of exits (`exits`); at each close the first rule in
  the list that fires sells the whole position at the next open. `signal` (all its
  conditions pass) is built in; `holding_period` is a registered rule. An exit rule sees
  the position (quantity, entry price, sessions held with the fill session as one, the
  highest close since the fill) and the close; never a later price. The single `exit`
  field still works and means a list of one; the resolved settings keep `exit` (the
  first rule) next to `exits` so older readers are unaffected.
* **Sizing.** `sizing` names a registered rule (default `percent_of_equity`, exactly
  the 0.2.0 formula). A rule returns whole shares from the decision close, the equity at
  that close and any indicators it declares it needs.
* **Ranking.** `rank_by` (an indicator and `ascending` / `descending`) orders a
  session's entry candidates, so when signals outnumber open slots or cash the best
  ranked ones are taken first.
* **Two-indicator conditions.** `indicator_compare` (A above / below B) and
  `indicator_cross` (A crosses above / below B; previous A <= previous B and A > B). Both
  indicators must be in the same units: both in price units, or the same indicator with
  different settings (RSI 7 vs RSI 28).
* **Compatibility.** A strategy that uses none of the new options gives exactly the same
  decisions, fills and equity as on engine/0.2.0: thirteen strategies on 60 synthetic
  stocks over five years are fingerprinted in `tests/golden/ledger_hashes.json` and
  checked by `tests/test_golden_ledgers.py`. So engine/0.2.0 is listed in
  `COMPATIBLE_ENGINE_VERSIONS`: its runs can still be duplicated and its paper accounts
  keep processing. Comparisons put both runs in the 0.3.0 shape first, so an old run and
  a new one with the same settings show no exit, sizing or ranking difference.
