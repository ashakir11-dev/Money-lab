# Backtest metrics: what we report, what is missing, what to build next

Written 2026-11-26 against `main` at `bf17b0c`. Analysis only: no code was changed.
This is a plan to approve before anything is built.

## The short version

Money-lab already reports the headline numbers well: return, SPY return, the gap to SPY,
max drawdown, Sharpe, costs, exposure and a per-trade table. Sharpe is already annualised
with the right number of bars per year for each timeframe (252 for daily, 1,764 for hourly,
and so on), so that item on the wish list is done.

What is missing is the layer that tells you **why** a strategy wins or loses and **whether
the result is real**. Our two RSI studies needed a separate script to answer the questions
that mattered (edge per trade versus SPY, t-statistic, results by year, how positions
closed), and the main finding (being in cash 85% of the time explains almost the whole gap
to SPY) is something the dashboard shows only indirectly.

The recommended order is:

1. **Trade quality basics**: win rate, average win and loss, expectancy, profit factor,
   annualised return, and a warning when there are too few trades to trust.
2. **The SPY fair-comparison round** (already planned): park idle cash in SPY, plus edge per
   trade versus SPY, a t-statistic and a by-year table.
3. **Diagnostics that point at a fix**: how positions closed, best and worst move during each
   trade (MAE/MFE), drawdown length, per-symbol results, Sortino and Calmar.
4. **Costs, market sensitivity and regimes**: turnover and cost drag, beta to SPY, results in
   up and down markets, losing streaks, monthly returns.

Steps 1 and 3 need no engine change and no new engine version: metrics are computed after a
run from its saved ledgers, so they can be recomputed for every existing run.

## 1. What we report today

"Engine" means `src/moneylab/engine/reporting.py` (stored with the run). "Browser" means
the number is worked out in the dashboard from the ledgers each time the page opens
(`frontend/src/lib/analysis.ts` or the page itself) and is not stored.

| Metric | Computed in | Shown on |
|---|---|---|
| Total return | Engine | Run list, report, compare |
| SPY (benchmark) return, buy-and-hold with the same cash and costs | Engine (`simulator._benchmark`, then `metrics` on that path) | Run list, report, compare |
| Gap to SPY, in percentage points and in dollars | Browser | Report, compare |
| Max drawdown | Engine | Run list, report, compare |
| SPY max drawdown | Engine | Report only |
| Drawdown curve against SPY | Browser | Report (detailed view) |
| Sharpe (risk-free rate 0, annualised by bars per year of the run's timeframe) | Engine | Run list, report, compare |
| SPY Sharpe | Engine | Stored but not shown anywhere |
| Profit and loss in dollars, realized and unrealized | Browser | Report, compare |
| Trading costs | Engine | Report, compare |
| Final equity | Engine | Report, compare |
| Round trips (closed trades) and open positions | Engine | Run list, report, compare |
| Average exposure (share of equity in stocks) | Engine | Report, compare (detailed) |
| Time in market (share of sessions holding anything) | Browser | Report only |
| Exposure curve | Browser | Report (detailed) |
| Per-trade table: entry, exit, shares, P&L, hold length, costs | Browser (matches buys to sells first-in first-out) | Report (detailed) |
| Buys, sells, symbols traded, notional, still open | Browser | Report, compare |
| Missed entries: near-miss and skip counts by reason | Browser, from the decision ledger | Report (detailed) |
| Settings difference and the first decision that differs, with its cash and final-equity impact | Engine | Compare |
| Paper account: cash, equity, open positions | Engine (paper desk) | Paper page |

### Only in research scripts, not in the app

`scripts/research_rsi_spy20.py` and `scripts/research_rsi_stops.py` computed the numbers that
actually answered the research questions:

- edge per trade versus SPY over the same dates
- t-statistic of that edge, and the share of trades that beat SPY
- edge by entry year and by symbol
- how positions closed (held to the end, stopped, trailing stop, profit target, signal)

### Three things worth knowing about the current setup

- **Trade statistics live in the browser.** Because round trips are rebuilt on each page
  load and not stored, nothing about trade quality can be sorted, filtered or compared in
  the run list or across a batch.
- **No annualised figures.** Total return over two years and over seven years sit side by
  side with no common scale.
- **No sense of how much to trust a number.** A run with 6 trades and a run with 500 look
  equally confident.

## 2. What is missing, ranked

Priority means how much the metric changes a decision about a strategy. "Backend" means
engine or API only. "Needs UI" means the dashboard has to show it (the UI is owned by the
design work, so those parts are handed over rather than built here).

### Tier 1: is the strategy real and fairly judged?

| # | Metric | How it helps you improve a strategy | Backend or UI | Effort |
|---|---|---|---|---|
| 1 | **Park idle cash in SPY** (a strategy setting) and report the result against plain SPY | Our research showed exposure explains almost all of the gap. With idle cash in SPY, the question becomes fair: does switching into the signal's stocks beat simply staying in the index? Without it, every low-exposure strategy "loses" for the same boring reason. | Backend (engine, new engine version) + a UI toggle | Large |
| 2 | **Edge per trade versus SPY** over the same holding dates, with the share of trades that beat SPY | Separates "my entries are good" from "the market went up". A strategy can trail SPY overall and still have a real per-trade edge worth scaling up, or beat SPY only because it was invested in a bull market. | Backend + UI | Small |
| 3 | **Statistical confidence**: t-statistic of trade returns and of the edge, plus a warning under about 30 trades | Tells you when to stop tuning. RSI < 30 had t = 1.24, which means the edge could easily be luck. Without this, it is tempting to keep tweaking noise. | Backend + a UI badge | Small |
| 4 | **Win rate, average win, average loss, payoff ratio** (average win / average loss) | Shows which lever to pull. A low win rate with big wins wants looser exits; a high win rate with small wins and rare big losses wants a stop. | Backend + UI | Small |
| 5 | **Expectancy** (average P&L per trade, in dollars and percent) and **profit factor** (gross wins / gross losses) | The single best "is each trade worth taking" number. Profit factor under 1.0 after costs means the rules lose money no matter how they are sized. | Backend + UI | Small |
| 6 | **Annualised return (CAGR) and annualised volatility**, for the strategy and SPY | Lets runs over different periods be compared on one scale, and shows whether a smoother curve came from less risk or less return. | Backend + UI cell | Small |

### Tier 2: where exactly is it going wrong?

| # | Metric | How it helps you improve a strategy | Backend or UI | Effort |
|---|---|---|---|---|
| 7 | **Results by year**: return, SPY return, edge, trades, for each year | Shows whether the edge is steady or comes from one lucky year. RSI < 30 flipped sign in 2020 and 2023; a by-year table makes that visible at a glance. | Backend + UI table | Small |
| 8 | **Exit breakdown**: count, win rate and average P&L for each way a trade closed (hold, stop, trailing, target, signal) | Tells you which exit rule earns its keep. Research 2 found stops cut losers but also cut recoveries; this table shows that directly on every run. | Backend + UI table | Small |
| 9 | **MAE and MFE per trade** (the worst and best price move while the trade was open) | The most direct guide to placing stops and targets. If winners rarely dip more than 4% first, a 5% stop is safe. If trades often reach +8% then give it back, a target or trailing stop is leaving money on the table. | Backend (needs the snapshot's bars) + UI | Medium |
| 10 | **Drawdown length and recovery**: longest time under water, time to recover, number of drops over 10% | Depth alone hides pain. A −15% drop that takes two years to recover is worse to live with than a −25% drop that recovers in a month. | Backend + UI | Small |
| 11 | **Sortino and Calmar ratios**, and show **SPY's Sharpe** (already computed) | Sortino counts only downside swings, which suits strategies that sit in cash. Calmar is annual return divided by max drawdown. Showing SPY's Sharpe next to ours gives Sharpe a reference point. | Backend + UI cells | Small |
| 12 | **Results by symbol**: trades, win rate, P&L, edge per stock | Finds the stocks that drag results down (CVX, XOM, INTC in research 1) and warns when one stock carries the whole result. | Backend + UI table | Small |
| 13 | **Holding period**: average and median hold, for winners and for losers | If losers are held much longer than winners, the exit is too patient with bad trades. | Backend + UI | Small |

### Tier 3: costs, market sensitivity and polish

| # | Metric | How it helps you improve a strategy | Backend or UI | Effort |
|---|---|---|---|---|
| 14 | **Turnover and cost drag**: yearly turnover, costs as a share of gross profit, return before costs | Shows whether the strategy is fragile to costs. If costs eat half of gross profit, a slightly worse fill in real trading wipes it out. | Backend + UI | Small |
| 15 | **Signal funnel**: signals fired, taken, skipped by reason (already held, position limit, no cash) as rates | Shows whether the strategy is limited by capital. Research 1 skipped 753 signals, mostly "already held"; if many are "position limit", more slots or smaller positions may help. Counts exist today; rates and a per-run summary do not. | Backend + UI | Small |
| 16 | **Beta, correlation and alpha versus SPY** | Tells you how much of the return is just market exposure. A beta near 1 with no alpha means you rebuilt SPY expensively. | Backend + UI | Small |
| 17 | **Regime breakdown**: results when SPY is above or below its 200-day average (later: high or low volatility) | Shows whether a rule only works in bull markets. Mean-reversion buys often fail in falling markets; this tells you whether to add a market filter. | Backend + UI | Medium |
| 18 | **Streaks**: longest run of losing trades and of losing months | A practical check on whether you could live with it, and a sizing input for paper and live trading. | Backend + UI | Small |
| 19 | **Monthly returns grid** and **rolling one-year return versus SPY** | Visual: shows when the strategy works and when it does not, faster than any single number. | Mostly UI (data from backend) | Medium |

### Later, beyond per-run metrics

- **Out-of-sample and walk-forward checks** (tune on one period, test on the next) and a
  **parameter neighbourhood** view (does RSI 28 or 32 behave like RSI 30?). These are the
  real defence against overfitting, but they are a batch feature, not a run metric, and
  deserve their own plan.
- **The same metrics on paper accounts**, so paper results can be held up against the
  backtest that produced them.

## 3. Recommended build order

Effort is rough: Small is under half a day, Medium about a day, Large two days or more,
counting backend and tests. UI work is listed separately for the UI owner.

| Step | What | Backend effort | UI needed |
|---|---|---|---|
| 0 | **Move round trips into the engine.** Build the trade list in `reporting.py` (same first-in first-out matching the browser uses today), store it with the run, and add a command that recomputes metrics for existing runs from their saved ledgers. No engine version change, because metrics never feed decisions. | Small to medium | None at first; the report can keep its own table until it switches over |
| 1 | **Trade quality basics** (#3 to #6): win rate, average win and loss, payoff, expectancy, profit factor, CAGR, volatility, t-statistic and the low-trade warning. Add the key ones to the run list so runs can be sorted by them. | Small | A new "Trades" stat group on the report, a few rows on compare, two run-list columns |
| 2 | **SPY fair-comparison round** (#1, #2, #7): the "idle cash in SPY" setting with a new engine version, edge per trade versus SPY, beat-SPY rate, t-statistic of the edge, and the by-year table. Then rerun the RSI < 30 study with it. | Large (engine) plus Small (report) | A toggle in the builder, a "Versus SPY" section and by-year table on report and compare |
| 3 | **Diagnostics** (#8 to #13): exit breakdown, MAE/MFE, drawdown length, Sortino, Calmar, SPY Sharpe on screen, per-symbol and holding-period tables. | Medium | Tables on the report, MAE/MFE columns in the trade table, a scatter of MAE against final P&L if wanted |
| 4 | **Costs, market sensitivity, regimes** (#14 to #19). | Medium | Monthly grid and rolling chart are mainly UI |

After step 2 the lab can answer the research question we keep asking (does this beat
holding SPY, fairly, and is it more than luck?) without a script. After step 3 it can tell
you what to change.

## 4. Decisions for you

1. **Order.** Recommended: steps 0 and 1 first (quick, no engine change), then the SPY
   round. The alternative is to do the SPY round first because it is the bigger research
   unblocker; it is also the slowest.
2. **Low-trade warning threshold.** Recommended: warn under 30 closed trades, and show the
   t-statistic everywhere edge is shown.
3. **Idle cash in SPY: whole shares or fractional.** Recommended: whole shares, to match how
   every other position is filled today.
4. **UI hand-off.** Recommended: build the backend for each step, expose it in the API and
   OpenAPI schema, then hand the display work to the UI owner with this doc as the brief.

## Small fixes noticed on the way

- The Sharpe help text on the report says "each day's percent change". On hourly or minute
  runs it is each bar's change; the number is right, only the wording is daily. (UI text.)
- The compare page leaves the "B − A" cell empty for Sharpe and average exposure.
  (UI.)
