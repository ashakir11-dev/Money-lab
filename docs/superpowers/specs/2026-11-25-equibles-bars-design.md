# Equibles bars

Equibles is the only market-data source. Tiingo is removed. The lab computes its own indicators. Dividends are not fetched. Splits are applied when a snapshot is built. The default bar is one hour. A strategy freezes that bar size, and paper trading delivers the same size.

## Bars

`EquiblesProvider` fetches `GET /v1/stocks/{ticker}/bars` with `adjustment=raw` and `session=regular`, and `GET /v1/stocks/{ticker}/splits`. Indicator routes and the dividend route are never called.

| Timeframe | Source |
| --- | --- |
| `1h` | Fetched. Default for a new strategy and for a fetch that names no timeframe. |
| `1d` | Resampled from cached hourly bars: first open, highest high, lowest low, last close, summed volume. |
| `30min`, `15min`, `5min`, `1min` | Fetched at that size. Not built from the hourly cache. |

Hourly bars start on the hour from midnight `America/New_York`. The regular session is the seven bars `09:00`, `10:00`, `11:00`, `12:00`, `13:00`, `14:00`, `15:00`. The `09:00` bar is the half-hour `09:30`–`10:00`. It is kept.

For every timeframe, a bar is on the grid when its start is a multiple of the bar size from local midnight, `start < 16:00`, and `start + size > 09:30`. That rule is what produces the seven hourly starts. A returned bar that is not on the grid fails the import and names the unexpected start.

Raw hourly bars are adjusted with `moneylab.data.actions.adjust`, then resampled if the snapshot is daily. `adjust` compares the session date, so bars on the ex-date stay as traded and earlier bars are rescaled. The split value is `numerator / denominator`. A split dated after the snapshot's last bar is not applied. New Equibles snapshots record the adjustment `split_adjusted_from_raw`. File import keeps its current adjustment label and its current content hash.

A completed response is cached under `$MONEYLAB_HOME/cache/equibles/`. The cache key is ticker, timeframe, `raw`, and `regular`. It is reused only when every bar is `final` and the requested end date is before today's NYSE session in New York. A request that includes today is fetched again. Hourly history is requested as CSV, one response per symbol, up to 50,000 bars. A truncated response continues from the oldest bar's `endTime`. Each 500 bars still spend one quota unit.

The raw bars and the splits are stored beside the snapshot, the same way raw prices and actions are stored today, so the adjusted bars can be rebuilt.

## Clock

`StrategyConfig.timeframe` is one of `1min`, `5min`, `15min`, `30min`, `1h`, `1d`. A new config defaults to `1h`. A resolved config already stored with no timeframe loads as `1d`.

A daily snapshot is one row per session date and goes through the existing `normalise`. Columns stay `date, symbol, open, high, low, close, volume`. Two rows on the same date for one symbol are rejected.

An intraday snapshot does not go through `normalise`. Columns are `bar_start, date, symbol, open, high, low, close, volume`. `bar_start` is a naive `America/New_York` wall time. `date` is the NYSE session date. Identity is `(bar_start, symbol)`. The content hash includes `bar_start`, formatted `YYYY-MM-DDTHH:MM`.

The engine walks one row at a time. A hold of 10 is 10 bars of the snapshot's timeframe. Session labels in decisions, fills, equity, and paper state are the session date for `1d` and `YYYY-MM-DDTHH:MM` for any other timeframe. A signal on the `15:00` bar fills at the next bar's open, which is the next session's `09:00` bar. A run whose config timeframe differs from the snapshot timeframe is refused.

## Paper

A `1d` account still advances with the next NYSE date. The bars for that date are the resample of that day's adjusted hourly bars, ingested as one daily event.

Any other account uses the bar start as the session id. The next id is the next grid slot on an NYSE session. One event is every symbol at that single start. An event that spans two starts, or whose timeframe differs from the frozen account, is rejected.

A grid slot with no trades is an accepted event that contains no bars. Every symbol is missing, the existing missing-bar rule applies, and the account advances. A slot that is not yet `final` is not delivered, and no later slot is delivered ahead of it.

A split whose effective date falls inside the advance window refuses the advance. The message is the one `paper_advance` already raises: import a fresh snapshot, save a new run, and start a new paper account. Quantity is not rewritten in place.

## Client

`EQUIBLES_API_KEY` is a `SecretStr`. It is sent only as `Authorization: Bearer …`. It is never placed in a query string, log line, cache file, or snapshot metadata. A missing key fails before any request.

`Lab.provider()` returns `EquiblesProvider`. There is no Tiingo provider.

These responses do not become a snapshot:

| Response | Result |
| --- | --- |
| 401 | The key is missing or rejected. |
| 403 | The plan does not include bars. |
| 404 | The ticker is unknown. Not retried. |
| 429 | Wait for `Retry-After`. No tight retry. The cap resets at 00:00 UTC. |
| 5xx | Four retries, waiting 2s, 4s, 8s, then 16s, then a provider error. |
| `coveredFrom` after the requested start | Fail, naming the first stored day. |
| `incompleteSessions`, or an open NYSE day in `unstoredDates` | Fail, naming those days. |
| A missing bar Equibles did not flag | No trades. The bar stays absent. |
| `final: false` | That bar is left out. |

`POST /api/snapshots/tiingo` is removed. `POST /api/snapshots/equibles` takes symbols, an optional watchlist, start, end, an optional label, and `timeframe` (default `1h`). `moneylab fetch` takes the same timeframe option. `moneylab paper advance` fetches from Equibles at the account's frozen timeframe.

The data page replaces the Tiingo form with this request and a timeframe control that defaults to 1 hour. The strategy builder sets `timeframe`, default 1 hour. The paper page fetches from Equibles and shows session ids that include the time when the account is intraday.

`TiingoProvider`, `TIINGO_API_KEY`, the Tiingo fixtures, and the Tiingo provider tests are removed. Snapshots already stored stay readable and are not rewritten. A snapshot with no timeframe in its metadata is `1d`.

## Proof

Tests use recorded responses. None need a live key.

- One session of hourly bars resamples to one daily bar, including the `09:00` half-hour.
- A 4-for-1 split rescales earlier bars. A later split does nothing. The dividend route is not requested. The snapshot adjustment is `split_adjusted_from_raw`.
- File import still rejects two rows on the same date. An hourly snapshot keeps both hours, and its id changes if an hour changes.
- A new strategy defaults to `1h`. A stored config with no timeframe loads as `1d`. A mismatched run is refused.
- On an hourly fixture, a `15:00` signal fills at the next session's `09:00` open. A hold of two is two bars. Decision and fill session strings contain `T15:00` and `T09:00`.
- Paper steps `2026-11-25T10:00` to `2026-11-25T11:00`, and Friday `15:00` to Monday `09:00`. An empty hour is accepted and advances. A two-hour event and a wrong timeframe are rejected. A split in the window refuses the advance. A daily account still steps by NYSE date.
- Provider tests cover a missing key, 401, 403, 404, 429, a final cache hit, a forming bar fetched again, a late `coveredFrom`, and an incomplete session. The key is absent from logs and from the cache directory.

A live check, run only when asked and only with a key, confirms AAPL's 31 Aug 2020 4-for-1 on raw hourly bars. It is outside the default suite.

## Out of scope

Equibles indicators, dividends, extended hours, index membership, earnings, and short interest. Converting old Tiingo snapshots. Building a finer bar by resampling a coarser one. Rewriting paper quantities across a split.
