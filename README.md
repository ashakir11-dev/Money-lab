# Money-lab

A local, single-user trading research lab for US stocks and ETFs, built to the
[Trading Research Lab MVP spec](docs/spec-status.md). Today it runs one complete path
end to end: build an SMA-cross strategy in the dashboard, add an RSI filter, duplicate
the run, change the RSI threshold from 30 to 35, rerun, and see exactly which trade
changed and why. Batches of variants run concurrently on a worker pool
(`Lab.run_batch`). A plain-language tour with diagrams is in [docs/system-overview.md](docs/system-overview.md). Acceptance gate status is tracked in
[docs/acceptance-report.md](docs/acceptance-report.md).

## Try it in the cloud (no install)

On GitHub, click **Code > Codespaces > Create codespace on main**. The codespace installs
everything, loads the demo data and starts the dashboard; a browser tab opens on it
(or open the **Ports** tab and click port 8501). Then follow
[docs/walkthrough.md](docs/walkthrough.md). Live Tiingo data needs `TIINGO_API_KEY`
added as a Codespaces secret; the demo does not.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```bash
uv sync                      # create .venv and install everything, including dev tools
cp .env.example .env         # optional: change data folder, defaults, provider key
uv run moneylab demo         # import the offline fixture and save the RSI 30 and 35 runs
make web                     # build the dashboard once (needs Node.js 22)
uv run moneylab serve --open # the lab at http://127.0.0.1:8000 (API docs at /docs)
```

`moneylab serve` is the primary lab: FastAPI serves the research API and the built
Next.js dashboard (`frontend/`) from one address. Runs are submitted in the background
and keep going if the browser tab is closed. The browser never calculates a trade; every
number comes from the Python engine.

To work on the dashboard itself, run `uv run moneylab serve` and, in `frontend/`,
`NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run dev`. After changing the API, run
`make api-types` to refresh `frontend/openapi.json` and the generated TypeScript types
(CI fails if they are stale).

## Common commands

| Command | What it does |
| --- | --- |
| `make check` | lint, format check, type check and all tests |
| `make web` / `make e2e` | build the Next.js dashboard / run the browser checks (gates 1 and 3, paper trading) |
| `uv run python scripts/benchmark_browser.py` | gate 2 in the browser: submit to visible comparison, writes `docs/benchmarks/capacity-browser.md` |
| `uv run pytest` | all Python tests (about 25 seconds) |
| `uv run moneylab acceptance` | runs the acceptance checks and prints each gate's status |
| `uv run moneylab import PATH` | imports a CSV in the [file format](docs/file-import-format.md) as a new snapshot |
| `uv run moneylab paper start RUN_ID` | freezes a saved run for [paper trading](docs/paper-trading.md); then `paper advance` (Tiingo after-close), `paper ingest`, `paper status`, `paper replay` |
| `uv run moneylab fetch --watchlist "Benchmark 250" SPY --start 2020-01-02 --end 2024-12-31` | fetches that watchlist from Tiingo, plus any extra symbols, as a snapshot (needs `TIINGO_API_KEY`, see [providers](docs/providers.md)) |
| `uv run moneylab new-sessions SNAPSHOT_ID` | prints sessions Tiingo has after a snapshot, in the import format |
| `uv run moneylab watchlist NAME SYM... --label TEXT` | saves a labelled watchlist (a new version if it changed); without symbols shows it |
| `uv run pytest -m network -q tests/test_tiingo_live.py` | live Tiingo check (deselected by default; needs the key) |
| `uv run python scripts/make_tiingo_fixtures.py` | regenerates the recorded-style Tiingo responses |
| `uv run python scripts/make_fixture.py` | regenerates the deterministic fixture |
| `uv run python scripts/export_demo_ledgers.py OUT` | writes both demo ledgers and the comparison |
| `uv run python scripts/make_capacity_dataset.py` | writes the 250 stocks + SPY x 5 years capacity CSV under `var/` |
| `uv run python scripts/benchmark_capacity.py` | gate 2 benchmark: three timed 10-variant batches, writes `docs/benchmarks/capacity.md` |

## The RSI threshold demo in the dashboard

After `uv run moneylab demo` and `uv run moneylab serve --open`:

1. **Home**: open "SMA20 cross + RSI14 < 30" and press **Duplicate and edit**.
2. In the builder change only the RSI threshold from 30 to 35, then **Run backtest**.
3. Press **Compare with the original run**: one config difference
   (`entry[1].params.value`: 30 to 35) and the first changed decision: **AAA on
   2023-04-04**, observed RSI 31.68, failing `below 30` and passing `below 35`, bought
   134 shares at 74.91 the next open. **Inspect** shows both accounts that day.

`e2e/gates.test.cjs` drives these steps in a real browser (`make e2e`), and
`tests/test_rsi_divergence.py` checks both ledgers against hand calculations. The timed
version with a universe change is [docs/walkthrough.md](docs/walkthrough.md).

**Paper trading** freezes a saved run and steps it on new sessions: fetch them from
Tiingo or paste a CSV, then replay to check the record ([details](docs/paper-trading.md)).
**Gates** shows the acceptance status.

## Demo data or real data?

Every snapshot, run and comparison carries a label:

- **Demo data**: the hand-designed synthetic fixture in `fixtures/data` (AAA to DDD and a
  synthetic SPY). It exists to make known trades happen on known dates; it is not real.
- **Real data · Tiingo**: real end-of-day prices (split and dividend adjusted) fetched from
  Tiingo. Not intraday or streaming: the latest bar is the last completed session.
- **Imported file**: any other CSV you import.

To backtest on real prices: put `TIINGO_API_KEY=...` in a `.env` file in this folder (or
export it), start the lab, then on **Data** fill in *Real prices (Tiingo)*: symbols
(include your benchmark, e.g. SPY) and dates. The new snapshot is then offered in the
builder. Without a key the fetch is refused with a message saying so.

## Layout

```
src/moneylab/
  components/   declared, versioned indicators, conditions, exit rules and sizing rules
  data/         file-import adapter, immutable content-addressed snapshots, session alignment
  data/providers/  live provider adapters (Tiingo) sharing the file adapter's adjustment
  engine/       config resolution, the daily simulator, reporting and comparison
  paper/        paper forward testing: frozen runs stepped on delivered sessions, replay
  api/          FastAPI research API (background runs); served with the Next.js build
  store.py      run history (SQLite records + Parquet ledgers)
  service.py    the one entry point used by the API, CLI and tests
frontend/       Next.js dashboard (static export); controls are generated from component declarations
e2e/            browser checks against the real server
fixtures/data/  deterministic offline dataset
fixtures/tiingo/ recorded-style Tiingo responses (split + dividend variant)
docs/           decisions, file format, providers, acceptance report
```

Runtime state (snapshots, database, ledgers) goes to `MONEYLAB_HOME` (default `./var`,
git-ignored).
