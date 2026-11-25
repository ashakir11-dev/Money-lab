# Research 1: buying SPY's top 20 when RSI14 < 30 vs holding SPY (2018 to 2024)

Run on 2026-11-25 with engine/0.2.0 on Tiingo data (snapshot `ced0a100871defb5`).
Reproduce: `uv run python scripts/research_rsi_spy20.py` (needs `TIINGO_API_KEY`).

## Answer

No. Over 2018 to 2024 the strategy made 25.3% while buying and holding SPY made 143.9%.
Most of that gap is cash: with ten 10% slots and a signal that fires rarely, the account was
on average only 15% invested. Trade by trade there is a small edge (each trade beat SPY over
the same ten sessions by 0.42 percentage points on average), but it is not reliable: it is
not statistically distinguishable from zero (t = 1.24 over 258 trades), it flips sign in
2020 and 2023, it is negative at RSI < 25, and it disappears with a 20-session hold.

## Setup

- **Universe**: SPY's 20 largest holdings at the end of 2017 (chosen before the test
  period, so the list does not know which companies would grow): AAPL, MSFT, AMZN, META,
  BRK-B, JNJ, JPM, XOM, GOOGL, GOOG, BAC, WFC, CVX, INTC, T, PG, HD, UNH, V, PFE.
- **Entry**: RSI14 below 30 at the close; buy at the next open.
- **Exit**: after 10 sessions (the fill session counts as one).
- **Portfolio**: $100,000, 10% of equity per position, at most 10 positions, 5 bps per side.
- **Benchmark**: SPY bought with whole shares at the first open (417 shares at 239.16),
  same costs.
- **Period**: 2018-01-02 to 2024-12-31; data from 2017-09 so RSI is warm on day one.

## Results

| Variant | Total return | Sharpe | Max drawdown | Avg invested | Round trips | Avg edge per trade vs SPY | t | Beat SPY |
|---|---|---|---|---|---|---|---|---|
| **RSI < 30, hold 10** | **25.3%** | **0.32** | **-27.8%** | **15%** | **258** | **+0.42 pp** | **1.24** | **57%** |
| RSI < 25, hold 10 | 0.4% | 0.05 | -22.3% | 6% | 99 | -0.67 pp | -1.20 | 47% |
| RSI < 35, hold 10 | 66.6% | 0.58 | -25.3% | 29% | 513 | +0.36 pp | 1.70 | 53% |
| RSI < 30, hold 5 | 12.6% | 0.22 | -24.3% | 9% | 334 | +0.16 pp | 0.85 | 53% |
| RSI < 30, hold 20 | 25.8% | 0.32 | -30.1% | 25% | 216 | -0.03 pp | -0.07 | 45% |
| Hold SPY | 143.9% | 0.75 | -33.7% | 100% | | | | |

"Edge per trade" is each round trip's return after costs minus SPY's open-to-open return
over the same dates.

Base variant, edge by entry year: 2018 +1.25 pp, 2019 +1.41, 2020 -1.28, 2021 +0.99,
2022 +1.42, 2023 -1.66, 2024 +0.34. By symbol it was best on GOOGL, V, GOOG and AMZN and
worst on CVX, XOM, INTC and BRK-B.

Of 753 skipped signals in the base run, 728 were the stock already being held, 23 hit the
ten-position limit and 2 lacked cash at the open.

## Caveats

- The top 20 list is from memory of SPY's end-2017 weights, not from a dated holdings file.
- Prices are split and dividend adjusted at import, so trade returns include dividends.
- One period and one universe. The edge is small enough that a different window could
  remove it.

## What might be worth trying next

The low exposure is the main reason it trails SPY. A fairer version would hold SPY when no
signal is active and switch into the oversold stock, which the engine does not support today.
