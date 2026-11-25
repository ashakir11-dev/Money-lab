# Gate 3 walkthrough (time yourself)

Goal: starting from a saved run, change the universe and an entry setting, run the
comparison, and find the first changed trade and its reason, in under five minutes,
entirely in the browser dashboard. The automated version of these exact steps is the
"gate 3 walkthrough" test in `e2e/gates.test.cjs` (`make e2e`).

## Setup (not timed)

Needs Python 3.12 with `uv`, and Node.js 22.

1. `git pull` on `main`, then `uv sync`.
2. `make web` (builds the dashboard once).
3. `uv run moneylab demo` (imports `fixtures/data/rsi_divergence.csv` and saves
   "SMA20 cross + RSI14 < 30" on AAA, BBB, CCC, DDD).
4. `uv run moneylab serve --open` (opens http://127.0.0.1:8000 in your browser).

## Timed part (start the clock)

1. On the **Runs** page, click the run "SMA20 cross + RSI14 < 30".
2. Click **Duplicate and edit**. The builder opens with a copy; the original is not changed.
3. In **Universe**, change `AAA, BBB, CCC, DDD` to `AAA, BBB, CCC` (remove DDD).
4. In the RSI entry condition (the second condition), change the value from `30` to `35`.
5. Click **Run backtest** and wait for the status to say complete (a few seconds).
6. Click **Compare with the original run**.

The Compare page opens with A = original, B = your copy. Without any more clicks it shows:

- **Config differences**: `entry[1].params.value` 30 -> 35 and `universe`.
- **First difference**: DDD on 2023-03-07. Run A entered (all conditions passed,
  RSI 24.89); run B has no decision because DDD is not in this run's universe.
  Click **Inspect** for the fill (135 @ 73.71 on 2023-03-08) and the cash impact.

Stop the clock. Variation: remove `CCC` instead of `DDD` in step 3 and the first difference
is AAA on 2023-04-04, where RSI 31.68 is below 35 but not below 30.

Record your time in `docs/acceptance-report.md` (gate 3) to mark the gate passing.
