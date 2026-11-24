# Stack and execution decisions

The spec leaves the stack open. These are the defaults chosen.

| Area | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12, managed by uv | best ecosystem for numerical research; uv gives one-command, locked installs |
| Storage | SQLite (run records) + Parquet (data snapshots and ledgers) | local, zero-ops, persistent; Parquet keeps 250 x 5 years compact and fast |
| Dashboard | Streamlit | controls can be generated from component declarations at runtime; headless `AppTest` lets tests drive the real UI |
| Calendar | `exchange-calendars` (XNYS) | lookbacks and holding periods count exchange sessions |
| Live provider | Tiingo (planned, pending) | free tier with adjusted daily bars and corporate actions; key goes in `TIINGO_API_KEY` |
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
