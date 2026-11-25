"""Plain-English presentation helpers shared by the API: data source labels and
strategy summaries."""

from __future__ import annotations

from moneylab.demo import rsi_strategy
from moneylab.describe import data_source, strategy_summary
from moneylab.engine.config import resolve
from moneylab.settings import Settings


def test_data_source_labels() -> None:
    assert data_source("tiingo").kind == "real"
    assert data_source("file:rsi_divergence.csv").kind == "demo"
    assert data_source("file:my_prices.csv").kind == "file"
    assert data_source("file:my_prices.csv+actions:acts.csv").kind == "file"


def test_strategy_summary_reads_like_a_sentence() -> None:
    text = strategy_summary(resolve(rsi_strategy(30), Settings()))
    assert text == (
        "Buy when close crosses above SMA(20) and RSI(14) below 30; sell after 10 sessions. "
        "Trades 4 symbols against SPY, 10% of equity per position."
    )
