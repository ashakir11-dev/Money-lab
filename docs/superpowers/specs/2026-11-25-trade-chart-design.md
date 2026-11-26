# Trade chart

Clicking a buy or a sell on a report opens a dialog. The dialog shows that symbol's candlesticks around the fill, every indicator the strategy uses, a mark on the fill, a mark on the signal bar, and the conditions stored for that signal.

The chart is computed when the dialog opens. The run does not store a new file. Indicators are computed with the same functions the engine uses, on the run's full bar grid, and the response is then cut to a fixed slice.

## Slice

`chart_window(n, signal_i, fill_i)` returns an inclusive `[start, end]` into the run's bar grid of length `n`.

- `start = max(0, signal_i - 80)`
- `end = min(n - 1, fill_i + 40)`
- Then `start = min(start, signal_i, fill_i)` and `end = max(end, signal_i, fill_i)`, so both bars stay inside the slice. A fill that waited past 40 bars lengthens the slice. It does not drop the signal mark.
- Counts are bars of the run's timeframe. An hourly run counts hours. A daily run counts days.

The dialog draws that slice and nothing outside it. It does not pan or zoom.

## Request

`GET /api/runs/{run_id}/chart?decision_id=`

`decision_id` is required. It is the id already stored on a fill. In the Trades table the whole row is a button when that id is present. On a round trip, Entry and Exit are separate buttons. Each leg keeps the fill's `decision_id`: the buy fill's id on the entry lot, the sell fill's id on the exit. A sell that closes several lots uses that same sell id on each of those exits. A fill with no `decision_id` is plain text and does not call the endpoint.

These tables are on the detailed report. The basic report does not gain a chart.

## Server

The handler loads the run, its resolved strategy, and its snapshot. It builds the same bar grid the engine used for that run (`start`, `end`, timeframe). It computes every indicator from `required_indicators` for the traded symbol. A breadth indicator is computed once over the run's universe and shared, as in a run. Other indicators are computed only on that symbol's frame. Computation uses the full grid, so a moving average inside the slice has its real warm-up. The arrays in the response are the slice only.

Session labels match the run: `YYYY-MM-DD` for `1d`, `YYYY-MM-DDTHH:MM` otherwise.

Each required indicator ref is one series, under the same `indicator_key` the engine stores on decisions. A MACD ref contributes the single output it names (`macd`, `signal`, or `histogram`). It is not expanded into the other two.

A missing bar is `null` in the candles array. An undefined indicator value is `null`. `price_scale` is the flag on that indicator's registry declaration.

The signal block is the decision row's stored action, reason, conditions, and values, the same objects the decisions endpoint already returns. A decision that stored no conditions, such as a holding-period exit, returns `conditions: []` and `values: {}`. The lines are recomputed. A difference between a recomputed value and the stored value does not fail the request.

`sessions`, `candles`, and every `values` array have the same length. The example below is that shape, shortened:

```json
{
  "symbol": "AAA",
  "timeframe": "1d",
  "sessions": ["2024-01-02", "2024-01-03"],
  "candles": [
    {"open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5},
    {"open": 10.5, "high": 10.8, "low": 10.0, "close": 10.2}
  ],
  "series": [
    {"key": "sma(length=20)", "price_scale": true, "values": [null, 10.2]},
    {"key": "rsi(length=14)", "price_scale": false, "values": [null, 28.1]}
  ],
  "fill": {"session": "2024-01-03", "side": "buy", "price": 10.5},
  "signal": {
    "session": "2024-01-02",
    "action": "enter",
    "reason": "all entry conditions passed",
    "conditions": [
      {"condition": "rsi(length=14) below 30", "observed": 28.1, "threshold": 30, "passed": true}
    ],
    "values": {"rsi(length=14)": 28.1}
  }
}
```

Zero fills for that `decision_id` is a 404 naming the id. More than one fill with that id is a 422 naming the id.

## Dialog

There is no dialog in the app today. This one sits over the report. The title is the symbol, the side, and the fill session, taken from the clicked row so they show before the response arrives.

The chart is on the left. The readout is on the right. A Close button dismisses the dialog, and so does Escape. Clicking the page behind the dialog does not. Opening moves focus to Close. Closing returns focus to the button that opened it.

The chart is one SVG, in the same style as the equity chart. No chart library.

- The top pane is candlesticks. `close >= open` uses the up color. `close < open` uses the down color. A `null` candle is a gap.
- Price-scale series are lines on that pane. The legend names each series by its indicator key.
- Every other series gets its own pane underneath, in `required_indicators` order, with its own scale. A `null` value breaks the line.
- Each pane scales to the values it contains, with enough pad that a candle or a line is not clipped.
- A vertical line runs through every pane on the fill bar. A filled circle at the fill price marks the trade, up color for a buy and down color for a sell.
- The signal bar gets a diamond at that bar's close, in the accent color, and no vertical line. A missing close omits the diamond. The readout still shows the decision.

The readout shows the fill session and price, the signal session, the stored reason, and the stored conditions in the existing conditions table. A hold-period exit has no conditions. The panel shows the reason and no conditions table.

While the request is in flight, the chart area is a loading state. A failed request replaces the candles with an error box titled "Could not load this chart" and omits the readout. The dialog stays open. A successful response whose candles are all `null` shows the readout and the sentence "No bars in this window."

## Errors

| Case | Status | Body |
| --- | --- | --- |
| Unknown run | 404 | `run {id} not found` |
| `decision_id` is not a decision on this run, or that decision has no fill | 404 | names the id |
| More than one fill shares the `decision_id` | 422 | names the id |
| Snapshot file missing | 404 | names the snapshot |
| Symbol has no bars, the signal session is not on the run's bar grid, or an indicator fails to compute | 422 | the exception text |

The handler does not return a partial chart.

## Tests

`chart_window` is tested on its own:

- A fill in the middle returns `signal_i - 80` through `fill_i + 40`.
- A signal inside the first 80 bars clamps the start to 0.
- A fill inside the last 40 bars clamps the end to `n - 1`.
- A signal and a fill more than 40 bars apart both stay inside, and the slice grows by the wait.

The API test uses the demo RSI fixture and `rsi_strategy`, through the existing test client. No live data.

- The first buy in the fills ledger returns 200. Sessions are exactly that slice. Candles and series are the same length as sessions. `sma(length=20)` has `price_scale` true. `rsi(length=14)` has `price_scale` false. The RSI value on the signal session equals the value stored on that decision. The fill session and the signal session are in the slice. No session outside the slice is returned.
- A holding-period sell returns 200, with the stored reason and an empty conditions list.
- An unknown run, an unknown `decision_id`, and a decision that never filled each return 404.

`make api-types` refreshes `frontend/openapi.json` and the generated client types.

The frontend has no test runner. In the browser, on a saved run: click a buy in Trades and Entry on a round trip. The dialog shows that symbol's candles, the moving-average line, an RSI pane, the fill line and circle, the signal diamond, and the stored conditions. Escape closes it and returns focus to the clicked control. A sell uses the down circle. A fill with no `decision_id` is not a button.
