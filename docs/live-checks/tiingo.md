# Live Tiingo check (acceptance gate 5)

Run with `uv run pytest -m network -v tests/test_tiingo_live.py` on 2026-11-24 19:54 UTC,
on `main` at 38f598a (after PR #7), in a session with `TIINGO_API_KEY` set. About 5
Tiingo requests. The test is deselected from normal runs; re-run it the same way to refresh
this record.

**Result: PASS** (1 passed in 2.30 s). The same check also passed earlier that day on 66d26ba.

## What it checked

| Check | Result |
| --- | --- |
| AAPL + SPY history 2020-08-03 to 2020-09-30 imported from Tiingo in 2 HTTP requests, `source = tiingo` | pass |
| AAPL 4-for-1 split on 2020-08-31 read from `splitFactor` | pass |
| AAPL $0.82 dividend on 2020-08-07 read from `divCash` | pass |
| Our adjustment from raw prices matches Tiingo's `adjClose` up to a constant (ratio spread < 1e-3) | pass |
| API key not present in any file under the lab home (snapshots, cache, run records) | pass |
| Re-import served from the response cache: same snapshot id | pass |
| Unknown ticker raises `ProviderNotFound` (Tiingo's 404 body) | pass |
| New sessions after a recent AAPL snapshot come back in the import format, all after its last date | pass |

## Output

```
platform linux -- Python 3.12.3, pytest-9.2.0, pluggy-1.7.0
collected 1 item

tests/test_tiingo_live.py::test_live_history_split_dividend_cache_and_new_sessions PASSED [100%]

============================== 1 passed in 2.30s ===============================
```
