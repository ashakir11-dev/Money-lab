# Money-lab

A local, single-user trading research lab for US stocks and ETFs, built to the
[Trading Research Lab MVP spec](docs/spec-status.md). Today it runs one complete path
end to end: build an SMA-cross strategy in the dashboard, add an RSI filter, duplicate
the run, change the RSI threshold from 30 to 35, rerun, and see exactly which trade
changed and why. Batches of variants run concurrently on a worker pool
(`Lab.run_batch`). Acceptance gate status is tracked in
[docs/acceptance-report.md](docs/acceptance-report.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```bash
uv sync                      # create .venv and install everything, including dev tools
cp .env.example .env         # optional: change data folder, defaults, provider key
uv run moneylab demo         # import the offline fixture and save the RSI 30 and 35 runs
uv run moneylab dashboard    # open http://localhost:8501
```

## Common commands

| Command | What it does |
| --- | --- |
| `make check` | lint, format check, type check and all tests |
| `uv run pytest` | all tests (about 10 seconds) |
| `uv run moneylab acceptance` | runs the acceptance checks and prints each gate's status |
| `uv run moneylab import PATH` | imports a CSV in the [file format](docs/file-import-format.md) as a new snapshot |
<<<<<<< HEAD
| `uv run moneylab paper start RUN_ID` | freezes a saved run for [paper trading](docs/paper-trading.md); then `paper ingest`, `paper status`, `paper replay` |
=======
| `uv run moneylab fetch SPY QQQ --start 2020-01-02 --end 2024-12-31` | imports daily history from Tiingo (needs `TIINGO_API_KEY`, see [providers](docs/providers.md)) |
| `uv run moneylab new-sessions SNAPSHOT_ID` | prints sessions Tiingo has after a snapshot, in the import format |
| `uv run moneylab watchlist NAME SYM... --label TEXT` | saves a labelled watchlist (a new version if it changed); without symbols shows it |
| `uv run pytest -m network -q tests/test_tiingo_live.py` | live Tiingo check (deselected by default; needs the key) |
| `uv run python scripts/make_tiingo_fixtures.py` | regenerates the recorded-style Tiingo responses |
>>>>>>> origin/claude/project-thread-7z8tbd-tiingo
| `uv run python scripts/make_fixture.py` | regenerates the deterministic fixture |
| `uv run python scripts/export_demo_ledgers.py OUT` | writes both demo ledgers and the comparison |
| `uv run python scripts/make_capacity_dataset.py` | writes the 250 stocks + SPY x 5 years capacity CSV under `var/` |
| `uv run python scripts/benchmark_capacity.py` | gate 2 benchmark: three timed 10-variant batches, writes `docs/benchmarks/capacity.md` |

## The RSI threshold demo in the dashboard

1. **Data**: press *Import as new snapshot* (the path defaults to the fixture).
2. **Strategy builder**: pick AAA, BBB, CCC, DDD. The entry already has *close crosses
   above SMA(20)*. Press *Add entry condition*: it adds *RSI(14) below 30*. Run.
3. **Runs**: open that run, inspect *Resolved settings*, press *Duplicate this run*.
4. In the duplicate change only the threshold 30 to 35. Run. The dashboard opens a
   comparison with the parent on the Compare page.
5. **Compare**: one config difference (`entry[1].params.value`: 30 to 35) and the first
   changed decision: **AAA on 2023-04-04**, observed RSI 31.68, failing `below 30` and
   passing `below 35`, bought 134 shares at 74.91 the next open.

`tests/test_dashboard_rsi_path.py` drives these exact steps through the real dashboard
script, and `tests/test_rsi_divergence.py` checks both ledgers against hand calculations.

## Layout

```
src/moneylab/
  components/   declared, versioned indicators and conditions (SMA, RSI, crossings, thresholds)
  data/         file-import adapter, immutable content-addressed snapshots, session alignment
  data/providers/  live provider adapters (Tiingo) sharing the file adapter's adjustment
  engine/       config resolution, the daily simulator, reporting and comparison
  paper/        paper forward testing: frozen runs stepped on delivered sessions, replay
  store.py      run history (SQLite records + Parquet ledgers)
  service.py    the one entry point used by the dashboard, CLI and tests
  dashboard/    Streamlit app; controls are generated from component declarations
fixtures/data/  deterministic offline dataset
fixtures/tiingo/ recorded-style Tiingo responses (split + dividend variant)
docs/           decisions, file format, providers, acceptance report
```

Runtime state (snapshots, database, ledgers) goes to `MONEYLAB_HOME` (default `./var`,
git-ignored).
