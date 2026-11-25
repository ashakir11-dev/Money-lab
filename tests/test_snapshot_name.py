"""A snapshot's display name is its label, or a line read from its configuration."""

from moneylab.data.snapshot import snapshot_name


def test_typed_label_is_the_name() -> None:
    meta = {
        "label": "  mine  ",
        "source": "tiingo",
        "symbols": ["AAPL"],
        "first_date": "2020-01-02",
        "last_date": "2020-01-03",
    }
    assert snapshot_name(meta) == "mine"


def test_name_reads_source_timeframe_symbols_and_requested_dates() -> None:
    meta = {
        "label": "",
        "source": "tiingo",
        "symbols": ["SPY", "MSFT", "AAPL"],
        "first_date": "2020-01-02",
        "last_date": "2024-12-30",
        "provenance": {"start": "2020-01-01", "end": "2024-12-31"},
    }
    assert snapshot_name(meta) == "tiingo · 1d · AAPL, MSFT, SPY · 2020-01-01 → 2024-12-31"


def test_timeframe_comes_from_metadata_or_provenance() -> None:
    base = {
        "label": "",
        "source": "equibles",
        "symbols": ["AAPL"],
        "first_date": "2024-01-02",
        "last_date": "2024-01-31",
        "provenance": {"start": "2024-01-01", "end": "2024-01-31", "timeframe": "1h"},
    }
    assert snapshot_name(base) == "equibles · 1h · AAPL · 2024-01-01 → 2024-01-31"
    stored = {**base, "timeframe": "15min", "provenance": {**base["provenance"], "timeframe": "1h"}}
    assert snapshot_name(stored) == "equibles · 15min · AAPL · 2024-01-01 → 2024-01-31"


def test_watchlist_names_the_universe() -> None:
    meta = {
        "label": "",
        "source": "equibles",
        "timeframe": "1h",
        "symbols": ["AAA", "BBB", "SPY"],
        "first_date": "2020-01-02",
        "last_date": "2024-12-31",
        "provenance": {
            "start": "2020-01-01",
            "end": "2024-12-31",
            "watchlist": {"name": "Large caps", "version": 2},
        },
    }
    assert snapshot_name(meta) == "equibles · 1h · Large caps v2 · 2020-01-01 → 2024-12-31"


def test_long_symbol_lists_keep_four_names() -> None:
    symbols = ["AAPL", "MSFT", "NVDA", "QQQ", "IWM", "DIA"]
    meta = {
        "label": "",
        "source": "file:prices.csv",
        "symbols": symbols,
        "first_date": "2020-01-02",
        "last_date": "2024-12-30",
    }
    assert (
        snapshot_name(meta)
        == "file:prices.csv · 1d · AAPL, DIA, IWM, MSFT + 2 · 2020-01-02 → 2024-12-30"
    )


def test_missing_snapshot_fields_still_produce_a_name() -> None:
    assert snapshot_name({}) == "unknown · 1d · no symbols · — → —"
