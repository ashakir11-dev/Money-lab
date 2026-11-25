# Paper trading (forward testing)

Paper mode runs a frozen, saved strategy on market sessions that arrive after it was
frozen, with exactly the historical engine's decision and accounting logic
(`moneylab.engine.simulator.Account.step`, unchanged). Code: `src/moneylab/paper/`.
Tests: `tests/test_paper.py`, `tests/test_api_paper.py`, and the Paper trading page in
`e2e/gates.test.cjs`.

## Lifecycle

1. **Freeze.** `Lab.start_paper(run_id, start_date=None, name="")` (CLI
   `moneylab paper start RUN_ID --start 2023-03-31`, or the dashboard's *Paper trading*
   page, `POST /api/paper`) takes a completed run and stores:
   * its resolved settings, after `check_versions` confirms every component and the
     engine still have the versions the run used (otherwise it refuses);
   * the **history**: the run's snapshot from the run's own first session to the start
     session (the last XNYS session on or before `start_date`; default: the snapshot's
     last session), saved as a content-addressed snapshot under
     `<home>/paper/history/`. Indicators warm up on exactly the bars the historical run
     used;
   * the **initial state**: a fresh `Account` with the run's starting cash whose
     `last_index` is the start session, so it only ever steps later sessions.
2. **Deliver.** Each incoming exchange session is a *session event*: a frame in the
   [import format](file-import-format.md) (`date,symbol,open,high,low,close,volume`) for
   one date. `Lab.paper_ingest(paper_id, frame, received_at=None)` records it;
   `Lab.paper_ingest_sessions` (CLI `moneylab paper ingest PAPER_ID FILE.csv`) splits a
   multi-day file into one event per date, in date order.
3. **Process.** After every delivery, every accepted session that directly follows the
   last processed one is processed, in calendar order.
4. **Inspect.** `Lab.paper_status(paper_id, as_of=None)` (CLI `moneylab paper status`):
   latest processed session, next expected session, cash, equity, positions, pending
   orders, missing / waiting / conflicting / rejected deliveries, and whether processing
   is blocked. `Lab.paper_ledgers(paper_id)` returns decisions, fills and daily equity.
5. **Replay.** `Lab.paper_replay(paper_id)` (CLI `moneylab paper replay`, exits 1 on a
   mismatch) rebuilds a fresh account from the same initial state, feeds it the
   recorded events, and compares decisions, fills and equity with the live record row
   by row. It reports `match`, the first differing row and, for decisions, the first
   differing trading decision (`engine.reporting.first_difference`).

## What is stored (`<home>/paper.sqlite`)

| Table | Contents |
| --- | --- |
| `paper_accounts` | frozen resolved settings, run and snapshot ids, history snapshot id, first and start sessions, initial state |
| `paper_events` | **every** delivery: event id, session, status, detail, received and recorded time, canonical CSV content |
| `paper_sessions` | per processed session: index, event id, input hash, input recipe, account state after the session |
| `paper_decisions`, `paper_fills`, `paper_equity` | the engine's ledger rows, one row each, keyed by decision id, fill number and session |

The **event id** is the snapshot content hash (`data.snapshot.content_id`) of the
normalised bars for the account's symbols (universe and benchmark; other symbols in a
delivery are dropped before hashing and storing).

The **input snapshot** for each processed session is recorded as a recipe (history
snapshot id plus the ordered accepted event ids) and the content hash of the full bar
set the decision saw. `PaperDesk.session_input(paper_id, session)` rebuilds the bars and
refuses if the hash no longer matches; `PaperDesk.recompute_session` re-runs that
session from the state before it and gives the same decisions, fills and state.

## Event outcomes

| Status | When | Effect |
| --- | --- | --- |
| `accepted` | first delivery for a session after the start | stored; processed now if it is the next session, otherwise it waits |
| `duplicate` | same session, same content as the accepted event | recorded, nothing else |
| `conflict` | same session, different content | recorded with a field-by-field difference, **rejected**: the accepted version stands and nothing is reprocessed |
| `rejected` | not an XNYS session, on or before the start session (part of the history), more than one date, missing columns, invalid bars, no bars for the account's symbols | recorded, nothing else |

## Idempotency rules

* At most one accepted event per (account, session): enforced by a unique index.
* A session is processed only if it is the session right after the last processed one,
  so it can never be processed twice; `Account.step` also refuses out-of-order indices.
* A processed session's state, decisions, fills and equity row are written in one
  `BEGIN IMMEDIATE` transaction that first checks no other process advanced the
  account. A crash leaves all of them or none; the stored event is then processed on
  the next delivery or `PaperDesk.process` call.
* State is read from disk on every step, so a restarted process (or a second one)
  resumes from the last committed session. Re-delivering everything adds zero
  decisions, orders or fills (`test_restart_and_duplicate_delivery_add_zero_orders_or_fills`).

## Missing and conflicting data

* **Missing session.** An XNYS session after the last processed one with no accepted
  event, up to the latest session anything was delivered for (or `as_of`). Processing
  **waits**: later sessions are stored as `waiting` and processed, in order, as soon
  as the gap is filled. The result equals in-order delivery. Paper mode never skips a
  session, because a skipped session would change every later decision index.
* **Missing symbol within a session.** Accepted; the detail lists the absent symbols and
  the engine's normal missing-bar rule applies (no trade, orders wait for a bar).
* **Conflict.** Rejected and flagged in the status. Corrections never rewrite processed
  history: to use corrected data, import it as a new snapshot, save a new run and start
  a new paper account. The earlier account, its recorded inputs and its replay stay
  reproducible.
* **Version change.** If a component or the engine version changes after the account
  was frozen, deliveries are still recorded but processing is blocked (`blocked` in the
  status) until the original versions are back.

## Feeding a real session

Preferred after-close path (Tiingo, needs `TIINGO_API_KEY`):

```bash
uv run moneylab demo                                    # or any saved run
uv run moneylab paper start RUN_ID                      # history to the snapshot's last session
uv run moneylab paper advance PAPER_ID                  # fetch + ingest sessions after the frontier
uv run moneylab paper status PAPER_ID
uv run moneylab paper replay PAPER_ID
```

`Lab.paper_advance` / `moneylab paper advance` calls the provider for sessions strictly
after the latest processed session (or the start session), ingests them in date order,
and refuses if corporate actions appear in the window (start a new run and paper
account instead). Re-running is safe: already-accepted sessions come back as
`duplicate`.

Any source that can produce the import-format frame for one session also works:

```bash
uv run moneylab paper ingest PAPER_ID today.csv         # bars for the next XNYS session(s)
```

From code: `lab.paper_ingest(paper_id, frame, received_at=fetched_at)` or
`lab.paper_advance(paper_id)`. Deliver only completed sessions (after the close) and
prices consistent with the history snapshot's adjustment policy; a later dividend or
split re-adjusts history, which for paper mode means a new run and a new paper account.
