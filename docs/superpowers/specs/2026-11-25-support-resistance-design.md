# Support, resistance, and straightness

Initial building blocks for tested price areas and for how straight a recent move was. Approved as a first version on 2026-11-25. The chart review after it ships is what decides whether the noise setting should change. This spec does not retune that setting in advance.

The blocks count bars. They do not take a timeframe. On a daily snapshot a bar is one session. On an hourly snapshot a bar is one hour. That matches the bar clock in the Equibles design: the strategy freezes the snapshot's timeframe, and these indicators run on the bars they are given.

`highest_high` and `lowest_low` stay as they are. They are the edges of a window. This spec does not replace them.

## What a reaction is

A reaction is a turn large enough to clear ordinary noise. Noise is measured in the stock's usual bar size, the Wilder average true range already implemented by `atr`. The same formula is recomputed inside these blocks, with the same `atr_length`, so the usual bar matches `atr(length=atr_length)` on the same bars.

Walk one unbroken run of bars. A bar is missing when high, low, or close is missing. A missing bar ends the run. The next present bar starts a new run, the average true range warms up again, and turns from the earlier run are forgotten. No gap is filled.

Inside a run, the average true range is undefined until its usual warm-up, and it is unusable when it is zero. Turns start at the first bar where the average true range is a positive number. From that bar, keep two candidates: the highest high so far and the lowest low so far. The candidate bar is the first bar that printed that price. A later bar that only ties the price does not move the candidate.

On each later bar `t`, with `thresh = min_move * ATR[t]`:

- While both a high candidate and a low candidate are open, the high is confirmed when `candidate_high - low[t] >= thresh` and its bar is strictly before `t`. The low is confirmed when `high[t] - candidate_low >= thresh` and its bar is strictly before `t`. When both are true, confirm the one whose bar is earlier. When both candidates sit on the same bar, confirm neither and keep extending.
- After a high is confirmed, drop the high candidate. The new low candidate is the lowest low from the confirmed bar through `t`, on the first bar of that low. Then apply the low rule once more on this same bar. A low confirmation is the mirror, and the follow-up check is the high rule. A bar therefore confirms at most two turns.

A turn stores the extreme price (the high or the low), the bar of that extreme, and the confirmation bar `t`. It may be used on bar `t` and later. It is not used before `t`.

## What an area is

On bar `t`, the remembered turns are those confirmed on a bar `c` with `t - lookback < c <= t`. Memory is `lookback` bars long, counting the confirmation bar. The extreme itself can be a few bars older than that, because the turn is only known once the reversal has happened.

Let `width = zone * ATR[t]`. If the average true range is missing or zero, every level on this bar is blank.

Sort the remembered turns by price, and break ties by the extreme bar (earlier first). Walk that list. A turn joins the open group while `price - group_lowest_price <= width`. Otherwise the group closes and the turn starts a new group. A group is therefore at most `width` tall. One turn is a complete group.

The area's price is the median of its turn prices. With an even count, the median is the average of the two middle prices. The area's test count is the number of turns in the group.

`zone` must be strictly less than `min_move`. Consecutive turns are separated by at least `min_move` usual bars at the moment they are confirmed, and the default zone is narrower than that, so a peak and the trough that reversed it do not become one area when the usual bar is stable. If the usual bar later widens a lot, an old peak and an old trough can fall inside one width. That is accepted in this version. The chart review is where we see whether it happens on real prices.

## The six level series

All six use the same parameters and the same turns. A strategy should give them the same `min_move`, `zone`, `lookback`, and `atr_length`. The lab does not force that, just as it does not force two moving averages to share a length.

The previous close is `close[t-1]` inside the same unbroken run. The first bar of a run has no previous close, so all six series are blank there.

- **Support** is the highest area price that is at or below the previous close. When two areas share that price, use the one with more tests, then the one whose latest turn is more recent. It is in price units, so the close can cross it.
- **Resistance** is the lowest area price that is at or above the previous close. The same tie-break applies. It is in price units.
- An area that equals the previous close is both support and resistance for that bar.
- **Support tests** and **resistance tests** are the test counts of those two areas. They are blank when the area is blank.
- **Distance above support** is `(close[t] - support[t]) / ATR[t]`. Zero means the close is on support. A negative number means the close is through it.
- **Distance below resistance** is `(resistance[t] - close[t]) / ATR[t]`. Zero means the close is on resistance. A negative number means the close is through it.
- A distance is blank when its area is blank, or when the average true range is missing or zero.

When a side has no area, that side's price, test count, and distance are blank. A condition that reads a blank value does not pass.

A cross uses the level published on each bar. "Close crosses above resistance" means the previous close was at or below the previous resistance, and this close is above this bar's resistance. If a new turn moves the resistance down, a smaller rally can register as a cross. That is the published rule for this version, not a special case to smooth away.

## Straightness

Straightness is the signed efficiency of the last `length` close-to-close moves:

`(close[t] - close[t-length]) / (|close[t-length+1] - close[t-length]| + … + |close[t] - close[t-1]|)`

The first value is at bar index `length`. Any missing close in that span makes the value blank. When the denominator is zero, the price did not move, and the value is 0. A straight rise is +1. A straight drop is −1. The series is not in price units.

## Parameters

| Block | Name | Default | Bounds | Meaning |
| --- | --- | --- | --- | --- |
| Levels | `min_move` | 2 | 0.5 to 10 | Usual bars of travel required before a turn counts |
| Levels | `zone` | 1 | 0.1 to 5, and strictly below `min_move` | Turns within this many usual bars are one area |
| Levels | `lookback` | 120 | 20 to 2000 | Bars a confirmed turn stays in memory |
| Levels | `atr_length` | 14 | 1 to 200 | Length of the usual bar, same limits as `atr` |
| Straightness | `length` | 20 | 2 to 400 | Close-to-close moves in the score |

The lookback maximum is wider than the older daily lookbacks because an hourly bar count has to cover more bars to span the same calendar time. The default stays 120. On hourly bars that is about three weeks. The charts are what tell us whether to raise it.

## How a strategy uses them

A breakout is the close crossing above resistance. A bounce is distance above support greater than 0, that same distance under a cutoff chosen on the strategy, and, when the strategy wants a repeated level, support tests above 1. Straightness above a cutoff means the recent closes were a clean rise. Those cutoffs are strategy settings. They are not baked into the blocks.

## Components

Each block is one registered indicator, version `1.0.0`, in its own computation of one series per bar. No engine change and no engine-version bump. A strategy that does not name the new ids keeps the same decisions, fills, and equity.

| id | Label | Price units | Inputs |
| --- | --- | --- | --- |
| `support` | Support | yes | high, low, close |
| `resistance` | Resistance | yes | high, low, close |
| `support_tests` | Support tests | no | high, low, close |
| `resistance_tests` | Resistance tests | no | high, low, close |
| `support_distance` | Distance above support | no | high, low, close |
| `resistance_distance` | Distance below resistance | no | high, low, close |
| `straightness` | Trend straightness | no | close |

`src/moneylab/components/levels.py` holds the six level indicators and the shared turn and area calculation. `src/moneylab/components/straightness.py` holds straightness. The builder already draws controls from declarations, so these appear without a dashboard change.

`docs/decisions.md` gains a short entry that states the reaction rule, the area rule, the six series, and straightness, in the same style as the engine/0.3.0 indicators.

## Chart check

The dashboard has no price chart. `scripts/plot_levels.py` writes one SVG per symbol: the close, support, and resistance, using the registered indicators and the parameters given on the command line. It reads a snapshot already in the lab. It adds no dependency. It is the tool for judging whether turns are noise. It is not part of the pass/fail suite. Changing `min_move`, `zone`, or `lookback` waits until those pictures have been looked at.

## Tests

Hand-calculated series, in the style of `tests/test_new_indicators.py`.

Levels:

- A rise and a fall smaller than `min_move` usual bars produce no turn.
- A reversal of at least `min_move` usual bars confirms the earlier extreme on the bar where the threshold is first met, and not before.
- The candidate stays on the first bar of a flat extreme.
- Two turns whose prices lie within `zone` usual bars form one area at their median, with a test count of 2. A turn further away is its own area.
- Support is the nearest area at or below the previous close. Resistance is the nearest area at or above it. An area equal to the previous close is both. Two areas at the same price resolve to more tests, then the more recent turn.
- Distance is the gap divided by the usual bar, signed as above. A zero usual bar leaves the distance blank.
- Before the first confirmed turn, and on the first bar of a run, the six series are blank.
- A missing bar starts a new run. Turns from before the gap do not appear after it.
- `zone >= min_move` is rejected.
- A close crossing above resistance enters on a tiny fixture. A support-tests threshold of 1 keeps a two-turn area and rejects a one-turn area.

Straightness:

- The worked path of net progress 10 over travel 20 scores 0.5.
- A straight rise scores 1. A straight drop scores −1. A flat stretch scores 0.
- A missing close inside the window leaves that bar blank.

Existing golden ledgers stay unchanged, because they do not use the new ids.

## Out of scope

Fetching or resampling bars. A timeframe parameter on these indicators. Mixing two timeframes in one strategy. A price chart in the dashboard. Replacing `highest_high` or `lowest_low`. Choosing the final noise setting from the chart review. Baking a test-count minimum into the level so that a one-turn area is hidden.
