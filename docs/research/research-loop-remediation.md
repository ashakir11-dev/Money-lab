# Research loop remediation, phase 1

Phase 1 adds a research contract beside the existing family loop. It does not rerun NVDA, and it does not change the historical kill. R09–R14 and the Support Recovery V2 run are named in `moneylab.decision.integrity.DEFERRED` and are not implemented.

`example_research_card()` is a bounded support-bounce card on the 5 bps path. `validate_card` accepts it. Nothing in that helper fetches prices or scores a window.

## Costs

`Settings.cost_bps_per_side` stays 0. An omitted `cost_model` still records `cost_bps_per_side` and charges nothing, which is what saved runs and the engine tests already do.

| Label | Basis points | `cost_model` | Charged |
| --- | ---: | --- | --- |
| `legacy_zero_cost` | 0 | `legacy_unapplied` | no |
| `research` | 5 | `research_bps` | yes |
| `stress_10` | 10 | `research_bps` | yes |
| `stress_20` | 20 | `research_bps` | yes |

`bind_cost` is the way onto those paths. A zero cost with any label other than `legacy_zero_cost` is rejected.

## Recipe contract

`validate_card` runs before a fetch. A support-bounce or support-recovery treatment needs a bounded support distance (a lower bound at or below zero and a finite upper bound) or a `support_distance` cross. "Above support" alone is `unbounded_support`.

Claims and prose that ask for a numeric cross, a cooldown, an inclusive bound, or a frozen stop are rejected when the recipe does not express them, unless an `ApprovedApproximation` names that capability, version, and statement. The approximation is returned on the contract so it stays visible.

`threshold` version stays `1.0.0`. New operators are `at_or_above`, `at_or_below`, `crosses_above`, and `crosses_below`. Above and below are unchanged. A version bump would make `check_versions` reject every saved threshold recipe.

`crosses_above` is previous value `<=` threshold and current value `>`. `crosses_below` is the mirror. A NaN on either side does not pass. Inclusive equality passes `at_or_*` and fails the strict operator.

`cooldown_bars` blocks a new entry for that many sessions after the exit fill, including the fill session when the count is 1. Omitted means there is no cooldown.

`freeze_entry_levels` stores the finite indicator values from the entry decision on the buy fill. Missing warmup values are left out, not stored as zero. Exit rules still read live indicators, so a frozen structural stop is `unsupported_frozen_stop` unless an approximation is approved.

`trade_start` lets earlier loaded bars warm indicators without fills or signals. `Lab.execute` still slices the snapshot to `start`/`end`, so this only applies when the caller passes the earlier bars.

## One axis

A child must declare `entry`, `exit`, `sizing`, `universe`, or `execution`. Filter changes count as entry. Capacity and starting cash count as sizing. Fill, costs, ranking, cooldown, and frozen levels count as execution. Two of those groups are `multiple_axes` and spend no trial. A cash-policy change is rejected unless the card approves it and it is the only change on the execution axis.

## Estimands

`active_excess_pp` is unchanged. It is the arithmetic mean, in percentage points, of strategy bar return minus benchmark-account bar return on bars that began with positive exposure. It is not a compounded total-return gap and it is not exposure-matched SPY. When no bar began invested, this historical series is 0.0.

`active_excess_pp_v2` is that same mean, and it is undefined (`v2_undefined_reason = zero_exposure`) when exposure is zero.

Exposure-matched SPY for bar `t` is `sum(w) * r_benchmark + (1 - sum(w)) * r_cash`, using weights at the start of the bar. Exposure-matched same-stock uses `sum(w * r_asset)` plus the cash residual. Cash earns zero unless a series is supplied. Both are compounded as `product(1 + r) - 1`. The equity ledger does not store per-asset weights, so those series are computed from weights the caller supplies. They are not substituted for `active_excess_pp`.

`portfolio_estimands` also reports account total return, CAGR, drawdown, Sharpe, average exposure, invested-time fraction, trade count, expectancy, profit factor, and the sum of recorded costs. Profit factor stays undefined when there is no losing round trip.

## Decisions

`judge_seed` is the legacy rule. A non-positive `active_excess_pp` with enough coverage is still `kill`, including the reported discovery value **-0.00346 percentage points**. That object is not rewritten.

`decide_research` is the new rule:

| State | When |
| --- | --- |
| INVALID | The card or the recipe contract failed. |
| PARK | The design is valid and the interval is missing, too wide to clear `delta`, or the sample is below the floor. A point estimate on one side of zero is not enough. |
| DROP | A child interval's upper end is at or below `delta`, with enough evidence. |
| KILL | The family falsifier holds for the whole interval, with enough evidence. |
| KEEP | The interval's lower end is above `delta`, with enough evidence. |
| FREEZE | A KEEP whose manifest is copied. A later different manifest is refused. A short sample cannot freeze. |

A block shorter than the outcome horizon is `assumption_violated` and parks. The bootstrap is a seeded circular moving-block bootstrap. It does not use the process-wide random generator. `judge_seed` still does not build intervals.

Holdout scoring, previews, and reports require a frozen manifest and a partition that is not already contaminated. The loader is not called on a refusal. A proposer read of a holdout is refused even after a freeze. `redact_for_proposer` drops holdout rows. `recovery_swing_partitions()` marks 2025 contaminated for that branch because the 27 Nov 2026 run already scored it.

## What this phase does not do

It does not claim the NVDA book is profitable or statistically supported. It does not re-simulate the Equibles snapshot. It does not promote a diagnostic bucket, store candidate tables, or let a model override a kill.
