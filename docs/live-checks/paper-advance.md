# Live paper advance (acceptance gate 4)

Run on 2026-11-25 locally with `TIINGO_API_KEY` set. Evidence home:
`MONEYLAB_HOME=./var/gate4-live` (git-ignored).

**Result: PASS** — two genuinely new market sessions fetched from Tiingo, ingested into
a frozen paper account, processed, and replayed exactly.

## What was run

1. `moneylab fetch SPY AAPL --start 2024-01-02 --end 2026-11-20` → snapshot
   `a668a9f95b966d08` (source tiingo, split- and dividend-adjusted from raw).
2. Saved run `20261125-181102-2b906c`: SMA20 cross on AAPL, hold 10, benchmark SPY.
3. `Lab.start_paper` → `paper-20261125-181102-694439`, history through 2026-11-20.
4. `Lab.paper_advance(..., end=2026-11-24)` accepted and processed **2026-11-23** and
   **2026-11-24** (XNYS sessions after the freeze that Tiingo had completed).
5. `Lab.paper_replay` → `match: true`.

Today (2026-11-25) was still an open session at run time, so it was not requested.

## Checks

| Check | Result |
| --- | --- |
| Live Tiingo bars delivered after the paper frontier | pass |
| Both sessions `accepted` and processed in calendar order | pass |
| Replay of recorded inputs matches decisions, fills and equity | pass |
| Processing not blocked by version mismatch | pass |
