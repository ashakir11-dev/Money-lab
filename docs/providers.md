# Market-data providers

The lab loads daily bars from two adapters that share one adjustment path:

| Adapter | How | Code |
| --- | --- | --- |
| File | `uv run moneylab import PATH` (CSV in the [file format](file-import-format.md), optionally raw prices plus an actions file) | `Lab.import_file` |
| Tiingo (live) | `uv run moneylab fetch SYMBOLS... --start YYYY-MM-DD --end YYYY-MM-DD` | `Lab.import_provider`, `moneylab.data.providers.TiingoProvider` |

Both produce an immutable, content-addressed snapshot. Identical reference data gives
the same snapshot id whichever adapter loaded it, so the same strategy yields identical
decisions and ledgers (`tests/test_source_flexibility.py`).

## Credential configuration

The Tiingo key is read from the environment variable **`TIINGO_API_KEY`** (no
`MONEYLAB_` prefix), or from a local `.env` file.

* **Cloud sessions**: add `TIINGO_API_KEY` as an environment secret. Sessions started
  before the secret was added do not see it; start a new session.
* **Local machine**: `cp .env.example .env` and set `TIINGO_API_KEY=...` (`.env` is
  git-ignored). Get a free key at <https://www.tiingo.com/account/api/token>.

The key is held as a pydantic `SecretStr`, sent only in the `Authorization: Token ...`
request header (never in the URL), and is never logged, cached, or written to snapshot
metadata or run records. `tests/test_tiingo_provider.py` checks logs and every file
under the lab's home for it. A missing key fails with `TIINGO_API_KEY is not set`
before any request is made; a rejected key (HTTP 401/403) fails with `Tiingo rejected
the API key`.

## Requests and limits

Endpoint: `GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=&endDate=&format=json`.
Tickers with a class suffix use a dash (`BRK.B` is requested as `BRK-B`).

The free tier is small (approximately, not verified here: about 50 requests an hour,
1,000 a day and 500 distinct symbols a month; check your account page). The adapter
is built for that:

* **one request per symbol** for the whole date range;
* an **on-disk response cache** under `$MONEYLAB_HOME/cache/tiingo/`, one JSON file per
  (ticker, start, end). A range that ended at least two days before it was fetched is
  final and is reused forever, so re-importing costs no requests. A range that reaches
  the last two days is always re-fetched, so new sessions are never hidden.
  `moneylab fetch --refresh` (or `refresh=True`) bypasses the cache;
* **retries** on HTTP 429, 5xx and network errors, with exponential backoff (2, 4, 8,
  16 s) or the server's `Retry-After`; after four retries it raises
  `ProviderRateLimited` (429) or `ProviderError`;
* HTTP 404 raises `ProviderNotFound` (unknown ticker) and is not retried.

Each snapshot's metadata records `provenance`: provider, endpoint, symbols, start, end,
`fetched_at`, the number of HTTP requests and cache hits, and the number of actions.
Every import, including one that yields an existing snapshot, is appended to
`$MONEYLAB_HOME/snapshots/imports.jsonl`.

## Adjustment policy

The adapter does **not** use Tiingo's adjusted fields. It reads raw
`open/high/low/close/volume` and turns `splitFactor` (a split on that date, new shares
per old share, 1.0 otherwise) and `divCash` (a cash dividend whose ex-date is that
date) into an actions frame in the actions-file format. Both are passed through
`moneylab.data.actions.adjust`, the same function the file adapter uses, and the
snapshot records the policy `split_and_dividend_adjusted_from_raw`. The raw bars and
actions are stored next to the snapshot (`<id>.raw.parquet`, `<id>.actions.parquet`)
so it can be re-derived.

As a cross-check, `provenance.tiingo_adj_ratio_spread` holds, per symbol, the relative
spread of Tiingo's `adjClose` divided by the lab's adjusted close. Both are total-return
adjustments anchored at different dates, so the ratio should be constant (spread close
to 0). A large value means the two methodologies or the data disagree.

A split or dividend after a snapshot's last date does not change that snapshot; a
fresh import produces a new snapshot, and earlier runs keep pointing at theirs.

## Newly available sessions

`Lab.fetch_new_sessions(snapshot_id, symbols=None, end=None)` asks the provider for
sessions after the snapshot's last date (one request per symbol, never final in the
cache) and returns a `NewSessions`:

* `bars`: the new rows in the file-import format (`date,symbol,open,high,low,close,volume`),
  per symbol strictly after that symbol's last date in the snapshot;
* `actions`: splits and dividends that went ex in the window;
* `basis_changed`: true when `actions` is not empty.

Prices are as traded. Adjustment only rewrites prices *before* an action, so with no
actions these rows are exactly what the snapshot's adjusted series would hold for those
sessions. When there are actions, a consumer such as paper trading applies them to open
positions (split: quantity times value; dividend: cash per share), and research should
import a fresh snapshot. The filtering step is the pure function
`moneylab.data.providers.new_sessions(known, raw, actions)`. Nothing is stored.
`uv run moneylab new-sessions SNAPSHOT_ID` prints the rows as CSV.

## Watchlists

`Lab.save_watchlist(name, symbols, label)` stores a named, labelled watchlist in the
SQLite store with its members and creation time. Saving different members or a
different label under the same name adds a new version; versions are never edited.
`Lab.run(config, snapshot_id, watchlist=name)` runs any saved strategy on the
watchlist's current members and records the watchlist (name, version, label, members,
created_at) in the run's resolved settings. CLI: `uv run moneylab watchlist NAME
SYMBOLS... --label TEXT` saves, `uv run moneylab watchlist NAME` shows.

## Live check

Offline tests use recorded-style responses in `fixtures/tiingo/` (regenerate with
`uv run python scripts/make_tiingo_fixtures.py`). The live check runs only when asked
and only with a key (about 5 requests):

```bash
uv run pytest -m network -q tests/test_tiingo_live.py
```

It confirms the field names, the split and dividend semantics on AAPL around its
2020-08-31 4-for-1 split, the adjustment cross-check, the cache, a 404 for an unknown
ticker, and that new sessions come back after a recent snapshot.

## Adding another provider

Implement the `Provider` protocol in `moneylab/data/providers/`: `fetch_history(symbols,
start, end, refresh=False)` and `fetch_sessions(symbols, after, end=None)`, each
returning a `ProviderHistory(raw, actions, provenance)` with raw prices. Adjustment,
snapshots and everything downstream stay unchanged.
