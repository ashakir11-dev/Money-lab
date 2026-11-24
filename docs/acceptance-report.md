# Acceptance report

Run `uv run moneylab acceptance` to re-run the evidence and print this status.

Gate 6 passes. Gates 1 to 5 are pending, each with the evidence so far and what it
still depends on.

| Gate | Status | Evidence so far | Still depends on |
| --- | --- | --- | --- |
| 1. No-code configuration and extensibility | PENDING | RSI condition added to a saved strategy entirely through the UI (`tests/test_dashboard_rsi_path.py`); UI controls generated from declarations | MACD registered with zero dashboard, simulator or ledger edits; paper mode |
| 2. Capacity and speed | PENDING | none | 250 symbols x 5 years dataset, parallel worker pool, three timed 10-variant batches, sequential vs concurrent ledger equality, resource report |
| 3. Research usability | PENDING | duplicate, change setting, compare, and the first differing decision shown on opening the comparison (`tests/test_dashboard_rsi_path.py`) | universe change in the same flow; timed five-minute walkthrough |
| 4. Correctness and forward replay | PENDING | historical part done: hand-calculated orders, skips, quantities and cash to the cent for simultaneous signals, tie-break, max positions, costs, insufficient cash, opening gap, unaffordable open, missing bar, split and dividend (`tests/test_engine_fixtures.py`, `tests/test_rsi_divergence.py`); future prices do not change earlier decisions; account state survives a restart (`tests/test_account_state.py`) | paper mode, replay, duplicate delivery; one real observed market session |
| 5. Universe and source flexibility | PENDING | none | live provider adapter (Tiingo); identical results through both adapters; second watchlist |
| 6. Historical inspection and reproducibility | PASS | reopen after changed defaults shows the original resolved settings; an unchanged duplicate reproduces the ledger exactly; duplicating refuses when a component version changed; a data correction is a new snapshot and old runs still reproduce; one-threshold edit yields exactly one setting difference and the first decision difference (`tests/test_reproducibility.py`, `tests/test_rsi_divergence.py`) | none |
