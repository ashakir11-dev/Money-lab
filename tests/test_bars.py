"""Bar grid and the hourly-to-daily resample."""

from datetime import date, datetime

import pandas as pd

from moneylab.data.bars import (
    label_in_range,
    on_grid,
    regular_starts,
    resample_daily,
    session_label,
)


def test_hourly_regular_session_is_seven_bars_including_the_half_hour() -> None:
    starts = regular_starts(date(2026, 11, 25), 60)
    assert [s.strftime("%H:%M") for s in starts] == [
        "09:00",
        "10:00",
        "11:00",
        "12:00",
        "13:00",
        "14:00",
        "15:00",
    ]
    assert on_grid(datetime(2026, 11, 25, 9, 0), 60)
    assert not on_grid(datetime(2026, 11, 25, 8, 0), 60)
    assert not on_grid(datetime(2026, 11, 25, 16, 0), 60)


def test_finer_grids_start_at_the_open() -> None:
    assert regular_starts(date(2026, 11, 25), 30)[0] == datetime(2026, 11, 25, 9, 30)
    assert regular_starts(date(2026, 11, 25), 30)[-1] == datetime(2026, 11, 25, 15, 30)
    assert regular_starts(date(2026, 11, 25), 15)[0] == datetime(2026, 11, 25, 9, 30)
    assert regular_starts(date(2026, 11, 25), 1)[-1] == datetime(2026, 11, 25, 15, 59)


def test_session_label_keeps_a_date_for_midnight_and_a_clock_time_otherwise() -> None:
    assert session_label(pd.Timestamp("2026-11-25")) == "2026-11-25"
    assert session_label(pd.Timestamp("2026-11-25 15:00")) == "2026-11-25T15:00"


def test_a_date_bound_includes_the_whole_session() -> None:
    assert label_in_range("2026-11-25T09:00", "2026-11-25", "2026-11-25")
    assert label_in_range("2026-11-25T15:00", "2026-11-25", "2026-11-25")
    assert not label_in_range("2026-11-26T09:00", "2026-11-25", "2026-11-25")
    assert label_in_range("2026-11-25T10:00", "2026-11-25T10:00", "2026-11-25T11:00")
    assert not label_in_range("2026-11-25T09:00", "2026-11-25T10:00", "2026-11-25T11:00")


def test_resample_daily_uses_the_half_hour_and_sums_volume() -> None:
    day = pd.Timestamp("2026-11-25")
    bars = pd.DataFrame(
        [
            ("2026-11-25 09:00", day, "AAA", 10.0, 11.0, 9.5, 10.5, 100),
            ("2026-11-25 10:00", day, "AAA", 10.5, 12.0, 10.0, 11.0, 200),
            ("2026-11-25 15:00", day, "AAA", 11.0, 11.5, 8.0, 9.0, 50),
            ("2026-11-25 15:00", day, "BBB", 5.0, 6.0, 4.0, 5.5, 7),
        ],
        columns=["bar_start", "date", "symbol", "open", "high", "low", "close", "volume"],
    )
    daily = resample_daily(bars).set_index("symbol")
    assert daily.loc["AAA"]["open"] == 10.0
    assert daily.loc["AAA"]["high"] == 12.0
    assert daily.loc["AAA"]["low"] == 8.0
    assert daily.loc["AAA"]["close"] == 9.0
    assert daily.loc["AAA"]["volume"] == 350
    assert daily.loc["BBB"]["open"] == 5.0
    assert daily.loc["BBB"]["close"] == 5.5
