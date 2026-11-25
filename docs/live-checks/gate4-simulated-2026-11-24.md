# Gate 4: simulated forward day (2026-11-24)

Run on 2026-11-25 at the owner's request, as a rehearsal before a genuinely new session.
**This is a simulated forward day, not a live one**: the paper account was frozen with
history to Monday 2026-11-23, and Tuesday 2026-11-24, which had already closed, was
delivered as if it were "today's close". Gate 4 still asks for one genuinely new session.

## Setup

- Data: Tiingo, SPY's end-2017 top 20 plus SPY, 2026-05-08 to 2026-11-24 (snapshot
  `28ef56ccfd2f74f3`). Engine 0.2.0.
- Strategy: RSI14 below the threshold, buy at the next open, hold 10 sessions, 10% of
  equity per position, at most 10 positions, 5 bps per side (`scripts/research_rsi_spy20.py`).
- Two paper accounts started flat with $100,000, history to 2026-11-23:
  RSI < 30 (the research strategy) and RSI < 35 (so the day produces an order).
- Delivery: the 21 bars for 2026-11-24 taken from the snapshot, delivered once, then the
  same delivery again.

## Result

| Check | RSI < 30 | RSI < 35 |
| --- | --- | --- |
| 2026-11-24 accepted and processed | yes | yes |
| Second delivery of the same day | duplicate, nothing reprocessed | duplicate, nothing reprocessed |
| Decisions | none (no stock below 30) | UNH enter, 29 shares |
| Pending order for 2026-11-25 open | none | buy 29 UNH |
| Cash / equity after the day | 100,000.00 / 100,000.00 | 100,000.00 / 100,000.00 |
| Next expected session | 2026-11-25 | 2026-11-25 |
| Replay of recorded inputs | matches exactly | matches exactly |

## Hand checks

- **RSI14 on 2026-11-24**, recomputed independently with Wilder smoothing from the
  closes, equals the engine's value for all 20 stocks to 2 decimals. The lowest are
  UNH 32.23, INTC 36.17, XOM 36.37, JPM 37.30. None is below 30, so the RSI < 30 account
  correctly makes no decision; UNH is the only stock below 35.
- **UNH order size**: 10% of $100,000 = $10,000; floor(10,000 / 341.88 close) = 29
  shares; 29 x 341.88 = 9,914.52; with 5 bps estimated cost 9,914.52 x 1.0005 = 9,919.48,
  matching the recorded estimate.
- **Cash**: an order decided at a close fills at the next open, so cash and equity stay at
  100,000.00 after 2026-11-24 in both accounts.
- The historical run on the same data shows UNH as `already_held` on 2026-11-24 because it
  had been holding UNH; the paper account starts flat, so it enters. That difference is
  the starting state, not the decision logic.
