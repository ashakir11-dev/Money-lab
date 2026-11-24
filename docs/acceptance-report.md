# Acceptance report

Run `uv run moneylab acceptance` to re-run the evidence and print this status.

No gate is complete yet. The first end-to-end path (RSI threshold change in the
dashboard changes a known trade) is done and provides partial evidence for gates 1, 3,
4 and 6.

| Gate | Status | Evidence so far | Still depends on |
| --- | --- | --- | --- |
| 1. No-code configuration and extensibility | PENDING | RSI condition added to a saved strategy entirely through the UI (`tests/test_dashboard_rsi_path.py`); UI controls generated from declarations | MACD registered with zero dashboard, simulator or ledger edits; paper mode |
| 2. Capacity and speed | PENDING | none | 250 symbols x 5 years dataset, parallel worker pool, three timed 10-variant batches, sequential vs concurrent ledger equality, resource report |
| 3. Research usability | PENDING | duplicate, change setting, compare, and the first differing decision shown on opening the comparison (`tests/test_dashboard_rsi_path.py`) | universe change in the same flow; timed five-minute walkthrough |
| 4. Correctness and forward replay | PENDING | hand-calculated orders, quantities and cash to the cent for the RSI fixture; future prices do not change earlier decisions (`tests/test_rsi_divergence.py`) | fixtures for gaps, insufficient cash, simultaneous signals and a corporate action; paper mode, replay, restart and duplicate delivery; one real observed market session |
| 5. Universe and source flexibility | PENDING | none | live provider adapter (Tiingo); identical results through both adapters; second watchlist |
| 6. Historical inspection and reproducibility | PENDING | resolved settings stored and shown on reopen; one-threshold edit yields exactly one setting difference and names the first decision difference; an identical rerun reports no difference (`tests/test_rsi_divergence.py`) | reopen after a changed default; component-version pinning on reproduction |
