# Stack and execution decisions

The spec leaves the stack open. These are the defaults chosen.

| Area | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12, managed by uv | best ecosystem for numerical research; uv gives one-command, locked installs |
| Storage | SQLite (run records) + Parquet (data snapshots and ledgers) | local, zero-ops, persistent; Parquet keeps 250 x 5 years compact and fast |
| App / API | FastAPI (`moneylab serve`) | one local process serves the research API and background runs; OpenAPI drives typed Next.js clients |
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
  SMA, EMA, highest_high, lowest_low). Crossing RSI, MACD, breadth, ROC, %B or average
  volume is rejected at validation, and the dashboard only offers price-unit
  indicators for crossings.
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
* **Price-level exits** (`components/exits.py`, each 1.0.0 with one `pct` fraction,
  0.001 to 0.9): `stop_loss` (default 0.08) fires when close <= entry price x (1 - pct);
  `take_profit` (default 0.15) when close >= entry price x (1 + pct); `trailing_stop`
  (default 0.10) when close <= peak close x (1 - pct). The entry price is the fill price
  and the peak close is the highest close from the fill session through this one. The
  level is rounded to 10 decimals so a close exactly at it always fires. Checked at the
  close and sold at the next open like every decision, so a gap down sells at that lower
  open; a session with no bar never fires.
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

### Sizing rules and ATR added in engine/0.3.0

Two sizing rules and one indicator, each a new file in `components/` (no engine change).
All quantities are whole shares worked out at the decision close, with the equity at
that close; a quantity of 0 is recorded as `skip` / `zero_quantity`.

* **`atr`** (indicator, `length` 1-200, default 14; not in price-cross units). True range
  TR = max(high - low, |high - previous close|, |low - previous close|), and
  TR = high - low on the first session of a run of bars. The first ATR is the mean of the
  first `length` true ranges; later ATR = (previous ATR x (length - 1) + TR) / length
  (Wilder). A missing bar is never filled: the next bar starts a new run and the warm-up
  restarts, as for SMA and RSI.
* **`fixed_dollar`** (`amount` >= 1, default 10,000): qty = floor(amount / close).
* **`volatility`** (`risk_fraction` 0.0001-0.2, default 0.01; `atr_length`, default 14;
  `atr_multiple` 0.1-20, default 2): qty = floor(risk_fraction x equity /
  (atr_multiple x ATR(atr_length))), then capped at the target allocation:
  qty = min(qty, floor(target_allocation x equity / close)). While ATR is undefined
  (warm-up, or just after a missing bar) or 0 the quantity is 0. It declares ATR as a
  requirement, so the resolved settings carry
  `sizing.requires = [{"id": "atr", "version": "1.0.0", "params": {"length": atr_length}}]`.
* Tests: `tests/test_sizing.py` (hand-calculated ATR, quantities and cash; validation;
  the API; paper trading equal to the historical engine).

### Indicators added in engine/0.3.0

Each is one new file in `src/moneylab/components/`, version 1.0.0. Lookbacks count
exchange sessions; a missing bar is never filled.

* **`ema(length=20)`**: seeded with the mean of the first `length` closes (first value
  at index length-1), then `ema[t] = ema[t-1] + 2/(length+1) * (close[t] - ema[t-1])`.
  A missing bar restarts the warm-up, as for RSI and MACD. Price units.
* **`bollinger_pctb(length=20, stdevs=2.0)`**: `(close - lower) / (upper - lower)` with
  middle = SMA(length) and bands = middle +/- stdevs x population std (ddof=0) of the
  last `length` closes. Undefined when the bands coincide (flat window) or a gap is in
  the window. Not price units (0 = lower band, 1 = upper band).
* **`roc(length=20)`**: `close[t] / close[t-length] - 1` as a fraction (0.05 = +5%).
  Undefined if either bar is missing. Not price units.
* **`highest_high(length=20)`, `lowest_low(length=20)`**: max high / min low of the
  `length` sessions before the current one, excluding today, so "close crosses above
  highest_high(50)" is a real breakout. Undefined unless all `length` prior sessions have
  a bar. Price units.
* **`avg_volume(length=20)`**: mean volume of the last `length` sessions including today.
  Undefined while any bar in the window is missing. Not price units.

### Support, resistance, and straightness

Seven indicators, version 1.0.0, in `components/levels.py` and `components/straightness.py`.
No engine change. They count bars, so a daily bar and an hourly bar use the same rules.
`highest_high` and `lowest_low` are unchanged: they are the edges of a window.

* A turn is the first bar of an extreme. It is confirmed only after price travels
  `min_move` usual bars away from it (default 2). The usual bar is Wilder ATR of
  `atr_length` (default 14), the same series as `atr`. A missing high, low, or close
  ends the run and forgets its turns.
* Remembered turns are those confirmed within the last `lookback` bars (default 120,
  bounds 20 to 2000). Walking from the lowest price, a turn joins the open area while
  it is within `zone` usual bars of that area's lowest price (default zone 1, and
  `zone` must be less than `min_move`). The area price is the median. One turn is enough.
* **`support`** is the highest area at or below the previous close. **`resistance`** is
  the lowest area at or above it. The same price uses the area with more turns, then
  the more recent turn. An area equal to the previous close is both. Both are price units.
* **`support_tests`** and **`resistance_tests`** are the turn counts. **`support_distance`**
  is `(close - support) / ATR` and **`resistance_distance`** is `(resistance - close) / ATR`.
  A usual bar of zero blanks the bar.
* **`straightness(length=20)`**: `(close[t] - close[t-length])` divided by the sum of the
  absolute close-to-close moves. +1 is a straight rise, -1 a straight drop, 0 is a flat
  stretch. A missing close in the span leaves it blank.
* `scripts/plot_levels.py` writes one SVG per symbol (close, support, resistance) from a
  snapshot. It is for judging whether the default move is noise. It is not a pass/fail test.
* Tests: `tests/test_levels.py`, `tests/test_straightness.py`.

### Swing points, trend blocks, bars per year and the Builder's bar size

* **`swing_high` / `swing_low`** (`left`, `right`, default 5/5): the last confirmed
  swing point. Bar k is a swing high when its high is above the `left` highs before it
  and at least the `right` highs after it; it is only known at bar k + `right`. Price units.
* **`market_structure`** (same `left`/`right`): +1 when the last two confirmed swing highs
  and lows both rise, -1 when both fall, 0 when mixed.
* **`adx`** (`length` 14, `output` adx | plus_di | minus_di): Wilder's directional
  movement. ADX is trend strength (0-100, above about 25 is the usual "trending" cut-off);
  `+DI above -DI` (same indicator, two outputs) gives direction.
* **`linreg_trend`** (`length` 50, `output` slope_pct_year | tstat | r2): least-squares slope
  of ln(close). The %/year figure uses bars per year of the data's bar size.
* **`straightness`** 1.1.0 gains `output=unsigned` (Kaufman's efficiency ratio, the absolute
  value) instead of a separate efficiency block.
* **Bars per year**: 252 daily; 252 x grid bars per session intraday (1h = 7, so 1764;
  5min = 78; 1min = 390). Sharpe is annualised by it, so hourly runs are no longer scaled
  as if daily. Daily results, and the golden ledgers, are unchanged.
* **Bar size of a run**: a strategy that names no `timeframe` takes its snapshot's bar
  size (1d for snapshots saved before bar sizes existed). The Builder shows a Bar size
  picker that follows the chosen snapshot; the report shows it next to the snapshot.
  Previously the Builder always sent 1h, which made every daily snapshot refuse to run.
* **Sources**: Equibles (any bar size) and Tiingo (daily only; other sizes are refused
  with a message) both stay available.
* **No look-ahead**: `tests/test_no_lookahead.py` checks every registered indicator, at
  defaults and at short lookbacks: truncating after bar t, or rewriting every later bar,
  never changes a value at or before t. A new indicator is covered when it is registered.
