# Research 2: do stops, profit targets or volatility sizing rescue RSI < 30?

Run on 2026-11-25 with engine/0.3.0 on Tiingo data (snapshot `71d3c0c302419517`, data from
2017-01 so every indicator is warm on 2018-01-02). Reproduce:
`uv run python scripts/research_rsi_stops.py` (needs `TIINGO_API_KEY`).

Same setup as [research 1](rsi30-spy-top20.md): SPY's 20 largest holdings at the end of
2017, 2018-01-02 to 2024-12-31, $100,000, 10% of equity per position, at most 10
positions, 5 bps per side. Buying and holding SPY made **143.9%**.

## Answer

No. None of the new exits or sizing makes the RSI < 30 strategy competitive with holding
SPY: the best variant (a 10% trailing stop with a 20-session hold) made 32.0% against
SPY's 143.9%. Stops and targets change which trades close and when, but the strategy is
still mostly in cash (15% to 25% invested), and that is what decides the result. The
per-trade edge over SPY gets smaller, not larger, once stops are added.

The trend rule (buy on SMA50 crossing above SMA200, sell on the cross below) is the only
variant that is invested most of the time (66%). It made 93.4%, still well behind SPY.

## Results

| Variant | Total return | Sharpe | Max drawdown | Avg invested | Round trips | Edge per trade vs SPY | t | How positions closed |
|---|---|---|---|---|---|---|---|---|
| RSI < 30, hold 10 (research 1) | 25.3% | 0.32 | -27.8% | 15% | 258 | +0.42 pp | 1.24 | 258 held |
| + stop-loss 8% | 22.2% | 0.32 | -25.4% | 15% | 272 | +0.32 pp | 0.99 | 232 held, 40 stopped |
| + stop 8%, take-profit 15%, hold 20 | 21.7% | 0.30 | -27.6% | 24% | 249 | +0.06 pp | 0.16 | 173 held, 59 stopped, 17 target |
| + trailing stop 10%, hold 20 | **32.0%** | **0.40** | **-24.6%** | 25% | 248 | +0.10 pp | 0.25 | 186 held, 62 trailing |
| + volatility sizing (1% risk, 2 ATR) | 26.1% | 0.34 | -26.0% | 15% | 258 | +0.44 pp | 1.32 | 258 held |
| Golden cross trend (SMA50 x SMA200) | 93.4% | 0.66 | -30.8% | 66% | 41 | -5.06 pp | -1.28 | 41 signal |
| Hold SPY | 143.9% | 0.75 | -33.7% | 100% | | | | |

"Edge per trade" is each closed round trip's return after costs minus SPY's open-to-open
return over the same dates. For the trend rule, trades last months, so the per-trade
comparison is noisy.

## What it shows

- **A stop-loss cuts drawdown a little and return a little.** The 8% stop closed 40 trades
  early. Those were mostly oversold stocks that kept falling, but enough of them would
  have recovered within the 10 sessions that the total ended slightly lower.
- **The trailing stop is the best of the RSI variants**, mostly because the 20-session
  hold keeps money invested longer (25% vs 15%), not because the trades got better (edge
  +0.10 pp, t = 0.25).
- **Volatility sizing barely matters here.** The 10% allocation cap binds for most of
  these large, calm stocks, so quantities are close to the default.
- **Exposure is the whole story.** Return rises with average invested across every
  variant. Being in the market beats being clever about exits in a strong bull market.

## Caveats

- One universe and one period (2018-2024, a strong market). The universe is from memory
  of SPY's end-2017 weights, not a dated holdings file.
- Stops are checked at the close and sold at the next open (the lab has daily bars only),
  so a stop can fill below its level after a gap down.

## Next

The fair-comparison work (keep idle cash in SPY) is what can actually test whether
switching into oversold stocks adds anything over the index.
