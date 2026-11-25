"""Plain-English presentation helpers shared by the API: data source labels and
strategy summaries."""

from __future__ import annotations

from moneylab.demo import rsi_strategy
from moneylab.describe import data_source, strategy_summary
from moneylab.engine.config import ExitRule, IndicatorRef, RankBy, SizingRule, resolve
from moneylab.settings import Settings


def test_data_source_labels() -> None:
    assert data_source("tiingo").kind == "real"
    assert data_source("file:rsi_divergence.csv").kind == "demo"
    assert data_source("file:my_prices.csv").kind == "file"
    assert data_source("file:my_prices.csv+actions:acts.csv").kind == "file"


def test_strategy_summary_reads_like_a_sentence() -> None:
    text = strategy_summary(resolve(rsi_strategy(30), Settings()))
    assert text == (
        "Buy when close crosses above SMA(20) and RSI(14) below 30 at the next open; "
        "sell after 10 sessions. "
        "Trades 4 symbols against SPY, 10% of equity per position."
    )


def test_strategy_summary_names_exits_sizing_and_ranking() -> None:
    cfg = rsi_strategy(30)
    cfg = cfg.model_copy(
        update={
            "exits": [
                ExitRule(kind="stop_loss", params={"pct": 0.08}),
                ExitRule(kind="trailing_stop", params={"pct": 0.125}),
                ExitRule(kind="holding_period", params={"sessions": 20}),
            ],
            "sizing": SizingRule(id="fixed_dollar", params={"amount": 5000}),
            "rank_by": RankBy(indicator=IndicatorRef(id="rsi", params={"length": 14})),
        }
    )
    text = strategy_summary(resolve(cfg, Settings()))
    assert (
        "sell if it closes 8% below the buy price (stop loss), or if it closes 12.5% below "
        "its highest close (trailing stop), or after 20 sessions."
    ) in text
    assert "$5,000 per position" in text
    assert text.endswith("it buys the lowest RSI(14) first.")


def test_strategy_summary_volatility_sizing() -> None:
    cfg = rsi_strategy(30).model_copy(update={"sizing": SizingRule(id="volatility")})
    text = strategy_summary(resolve(cfg, Settings()))
    assert "risk 1% of equity on a 2 x ATR(14) stop, at most 10% of equity per position" in text
