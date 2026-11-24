# Gate 3 walkthrough (time yourself)

Goal: starting from a saved run, change the universe and an entry setting, run the
comparison, and find the first changed trade and its reason, in under five minutes,
entirely in the dashboard. The automated version is `tests/test_gate3_usability.py`.

## Setup (not timed)

1. `make setup`, then `uv run moneylab demo` (imports `fixtures/data/rsi_divergence.csv`
   and saves "SMA20 cross + RSI < 30" on AAA, BBB, CCC, DDD).
2. `uv run moneylab dashboard` and open the URL it prints.

## Timed part (start the clock)

1. **Runs** page: pick "SMA20 cross + RSI14 < 30" in "Open run" and click **Duplicate this run**. The
   Strategy builder opens with a copy; the original is not changed.
2. In **Universe (watchlist)**, remove `DDD`.
3. In the RSI entry condition, change the threshold from `30` to `35`.
4. Click **Validate, run and save**.
5. Click **Compare with the original run**.

The Compare page opens with A = original, B = your copy. Without any more clicks it shows:

- **Config differences**: `entry[1].params.value` 30 -> 35 and `universe`.
- **First differing decision**: DDD on 2023-03-07. Run A entered (all conditions passed,
  RSI 24.89), and run B has none because DDD is not in its universe. Below it: the fill
  (135 @ 73.71 on 2023-03-08) and the cash impact on the next session.

Stop the clock. Variation: remove `CCC` instead of `DDD` in step 2 and the first difference
is AAA on 2023-04-04, where RSI 31.68 is below 35 but not below 30.

Record your time in `docs/acceptance-report.md` (gate 3) to mark the gate passing.
