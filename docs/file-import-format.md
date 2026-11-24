# File-import format

One CSV file, header required, one row per symbol per exchange session:

| column | type | notes |
| --- | --- | --- |
| `date` | `YYYY-MM-DD` | an XNYS session date |
| `symbol` | text | upper-cased on import |
| `open`, `high`, `low`, `close` | decimal > 0 | split- and dividend-adjusted |
| `volume` | integer | shares |

Rules:

* `(date, symbol)` must be unique. A correction is a new file, which becomes a new
  snapshot; earlier snapshots and the runs that used them are untouched.
* A missing row for a session after a symbol's first row is a **missing bar**. The lab
  never fills it: no trade happens that session, pending orders wait for the next bar,
  and indicators restart their warm-up after the gap.
* The snapshot id is a hash of the normalised contents, so identical data always gets
  the same id.

The adjustment policy is recorded as `split_and_dividend_adjusted` in each snapshot's
metadata.

## Raw prices with an actions file

`uv run moneylab import PATH --actions ACTIONS.csv` (or `Lab.import_file(path, actions_path=...)`) accepts raw,
unadjusted prices plus an actions CSV `date,symbol,type,value`: `split` (new shares per
old share, effective `date`) or `dividend` (cash per share, ex-date `date`). The lab
adjusts them once at import (`moneylab.data.actions`) and records the policy
`split_and_dividend_adjusted_from_raw`. The Tiingo adapter builds the same two frames
from the API and uses the same function, so identical data loaded either way gets the
same snapshot id (see [providers](providers.md)). Newly available sessions fetched from
a provider come back in this same column layout.
