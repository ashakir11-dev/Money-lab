# Research 1 again: RSI14 < 30 with idle cash held in SPY (2018 to 2024)

Run on 2026-11-26 with engine/0.4.0 on the same Tiingo data as research 1 (snapshot
`ced0a100871defb5`). Reproduce: `uv run python scripts/research_rsi_idle_spy.py`
(needs `TIINGO_API_KEY`). Setup, universe, dates and variants are identical to
[research 1](rsi30-spy-top20.md); the only change is the `idle_cash = "benchmark"` setting:
cash not in a position buys whole SPY shares at each open, and those shares are sold at
the open just before a buy that needs the cash (sweep fills are not trades).

## Answer

With the cash question removed, the strategy is level with SPY, not far behind it. The base
variant (RSI < 30, hold 10) made 161.1% against 143.9% for holding SPY (14.7% a year
against 13.6%), with a Sharpe of 0.77 against 0.75 and a deeper worst drop (-36.0% against
-33.7%). Research 1's 25.3% was almost entirely the account sitting in cash (15% invested).
The edge is still not distinguishable from luck: +0.35 pp per trade over SPY on the same
days, t = 1.06 over 258 trades, with beta 1.03 and alpha of only +0.7% a year. In other
words, the account is mostly SPY with a small, statistically weak tilt.

## Results

| Variant | Total return | Per year | SPY per year | Sharpe | Max drawdown | Trades | Win rate | Profit factor | Edge per trade | Edge t | Beta | Alpha |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **RSI < 30, hold 10** | **161.1%** | **14.7%** | 13.6% | **0.77** | **-36.0%** | **258** | **58.5%** | **1.49** | **+0.35 pp** | **1.06** | **1.03** | **+0.7%** |
| RSI < 25, hold 10 | 127.9% | 12.5% | 13.6% | 0.69 | -37.3% | 99 | 55.6% | 1.16 | -0.67 pp | -1.20 | 1.03 | -1.3% |
| RSI < 35, hold 10 | 171.7% | 15.4% | 13.6% | 0.79 | -34.2% | 509 | 59.3% | 1.56 | +0.30 pp | 1.39 | 1.03 | +1.3% |
| RSI < 30, hold 5 | 149.0% | 14.0% | 13.6% | 0.75 | -37.2% | 333 | 55.3% | 1.30 | +0.16 pp | 0.82 | 1.03 | -0.0% |
| RSI < 30, hold 20 | 135.4% | 13.0% | 13.6% | 0.70 | -37.6% | 214 | 58.9% | 1.48 | -0.03 pp | -0.08 | 1.03 | -0.7% |
| Hold SPY (all rows) | 143.9% | 13.6% | | 0.75 | -33.7% | | | | | | | |

Edge per trade is a trade's return after costs minus SPY's open-to-open return over the same
dates (the report's definition; research 1 used the same idea, so its +0.42 pp for the base
variant differs slightly from +0.35 pp here because the trade list now comes from the
engine's own first-in first-out matching and sessions).

Base variant, strategy against SPY by calendar year: 2018 +1.2% vs -5.2%, 2019 +34.6% vs
+31.2%, 2020 +12.7% vs +18.3%, 2021 +31.4% vs +28.7%, 2022 -13.1% vs -18.2%, 2023 +17.9% vs
+26.1%, 2024 +26.2% vs +24.9%. It beat SPY in five of seven years, but lost the two years
(2020, 2023) where research 1's edge also flipped sign.

## What this changes

- The headline gap in research 1 was cash drag, not bad picks: the same signals, with the
  idle cash earning SPY, finish within a few points of SPY in every variant.
- RSI < 30 and RSI < 35 end ahead of SPY; RSI < 25, hold 5 and hold 20 end behind. None of
  the per-trade edges has |t| above 1.4, so none is a result to build on.
- The strategy's market sensitivity (beta 1.03) is what SPY itself provides. To show a
  real signal edge it would need a clearly positive alpha with more trades than this.

## Caveats

- Same single period and universe as research 1; the top-20 list is from memory.
- Whole-share sweeps leave a few dollars uninvested and each sweep trade pays the 5 bps cost,
  so SPY is bought and sold more often than the plain benchmark (costs in the base run:
  $7.8k).
- Sweep sales are sized from the next open's order sizes using the engine's own estimate, so
  an entry is never skipped for cash that the SPY holding could have covered.
