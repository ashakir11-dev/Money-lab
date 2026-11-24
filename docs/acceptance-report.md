# Acceptance report

Run `uv run moneylab acceptance` to re-run the evidence and print this status.

Gates 2 and 6 pass. Gates 1, 3, 4 and 5 are pending, each with the evidence so far and what it
still depends on.

| Gate | Status | Evidence so far | Still depends on |
| --- | --- | --- | --- |
| 1. No-code configuration and extensibility | PENDING | RSI condition added to a saved strategy entirely through the UI (`tests/test_dashboard_rsi_path.py`); UI controls generated from declarations | MACD registered with zero dashboard, simulator or ledger edits; paper mode |
| 2. Capacity and speed | PASS | ten distinct variants x 250 stocks + SPY x five years (1,258 XNYS sessions, 313,736 rows, deterministic synthetic data from `scripts/make_capacity_dataset.py`), finished-result reuse not implemented: three consecutive batches from submission to all runs saved and variant 1 compared with the other nine in 15.76 s, 15.68 s and 15.73 s (limit 120 s) on 4 workers / 4 CPUs (Xeon 2.30 GHz, 15.7 GiB RAM, machine shared with other test runs); sequential and concurrent ledgers identical for 10/10 variants; CPU, RAM, data sizes, worker count and per-phase timings in [`docs/benchmarks/capacity.md`](benchmarks/capacity.md) (`scripts/benchmark_capacity.py`). Concurrent equals sequential per variant, each variant has its own account and cash, and symbols within an account compete for it (`tests/test_batch.py`) | none |
| 3. Research usability | PENDING | duplicate, change setting, compare, and the first differing decision shown on opening the comparison (`tests/test_dashboard_rsi_path.py`) | universe change in the same flow; timed five-minute walkthrough |
| 4. Correctness and forward replay | PENDING | historical part done: hand-calculated orders, skips, quantities and cash to the cent for simultaneous signals, tie-break, max positions, costs, insufficient cash, opening gap, unaffordable open, missing bar, split and dividend (`tests/test_engine_fixtures.py`, `tests/test_rsi_divergence.py`); future prices do not change earlier decisions; account state survives a restart (`tests/test_account_state.py`) | paper mode, replay, duplicate delivery; one real observed market session |
| 5. Universe and source flexibility | PENDING | none | live provider adapter (Tiingo); identical results through both adapters; second watchlist |
| 6. Historical inspection and reproducibility | PASS | reopen after changed defaults shows the original resolved settings; an unchanged duplicate reproduces the ledger exactly; duplicating refuses when a component version changed; a data correction is a new snapshot and old runs still reproduce; one-threshold edit yields exactly one setting difference and the first decision difference (`tests/test_reproducibility.py`, `tests/test_rsi_divergence.py`) | none |
