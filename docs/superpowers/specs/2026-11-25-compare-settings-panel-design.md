# Compare settings panel

The comparison page replaces the chip strip of individual setting differences with one collapsed Settings panel. Inside, each builder section shows what both runs used. A value that differs is shaded. The raw JSON dumps in Detailed mode stay.

## Placement

The panel sits where the chips sit now, above the two run summaries, on both Basic and Detailed. It starts collapsed.

The summary reads `Settings · N differences`, where N is the number of shaded rows. When nothing differs it reads `Settings match`.

The existing notice that the two runs used different price data stays above the panel, so that mismatch is visible while the panel is closed.

The panel root keeps `data-testid="config-diff"`.

## Sections

Opened, the panel is one table per section, in this order: Setup, Signal, Entry, Exit, Size, Positions, Capital. Columns are Setting, A, and B. A row is shaded when that setting differs.

Each signal or exit condition is one row, written as a sentence (for example `RSI(period=14) < 30`), not as separate parameter fields. A condition the other run does not have shows `none` on that side and the row is shaded. Percents stored as fractions at or below 1 display as percents (`0.02` as `2.00%`). An unreadable value shows `(not set)`.

Fixed flags that are always the same — long only, whole shares, one position per symbol — are omitted unless they differ. Component versions and the engine version stay in the Detailed JSON dump.

| Section | Rows |
| --- | --- |
| Setup | Name, symbols, benchmark, timeframe, first session, last session. Watchlist when either run has one. |
| Signal | Combine rule, then each entry condition. |
| Entry | Fill rule, then only the percent fields that rule uses. |
| Exit | Each price exit, each exit condition, and one hold-cap row. |
| Size | Method, its parameters, and the equity cap (`target_allocation`). |
| Positions | Max positions, and ranking. |
| Capital | Starting cash, and cost per side in basis points (`5 bps`). |

Name is shown and shaded when the names differ. The old chip strip ignored name.

Symbols stay one row, as a comma-separated list. The combine rule reads `All conditions must pass` when `entry_combine` is `all` or missing.

Entry percent rows depend on the fill. Next open has no percent rows. A dip adds one dip-percent row. In range adds below-percent and above-percent rows. When the two runs use different fills, a percent row that only one side uses shows `none` on the other side and is shaded.

A holding-period exit is the hold-cap row (`Hold 10 sessions`) and does not also appear as a price exit. A signal exit lists each condition, then the hold cap (`Hold 10 sessions`, or `No hold cap` when `max_sessions` is absent). Each other exit rule is one row, such as `Stop 8.00%`.

Ranking with no `rank_by` reads `By symbol, A to Z`. A ranked run reads the indicator, its parameters, and `Lowest first` or `Highest first`.

## How a row is built

No API change. The panel is built in the browser from the two runs the compare response already returns: each run's resolved settings.

A pure function turns those two resolved objects into the seven sections. A row is shaded when its underlying value differs. Older runs use the same fallbacks the comparison already uses: a missing entry fill is next open, a missing exits list is the single exit rule, and a missing sizing rule is percent of equity.

A value that cannot be turned into a sentence falls back to a short JSON snippet. The page still renders.

## What goes away

The chip strip goes away, including the line `Settings are identical`. The Detailed JSON dumps of each run's resolved settings stay.

## Verification

There is no frontend test runner. Check this in the browser on the Compare page:

- The panel starts closed, and the summary count matches the shaded rows.
- Opening it shows the seven sections with A and B filled in.
- A known difference is shaded, and a matching setting is not.
- A condition present on only one side shows `none` on the other and is shaded.
- Detailed mode still shows both JSON dumps.
