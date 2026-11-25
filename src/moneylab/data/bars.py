"""Bar grid, session labels, and the hourly-to-daily resample.

Equibles hourly bars start on the hour from midnight America/New_York. The regular
session is the bars that overlap 09:30–16:00. The 09:00 bar is the half-hour
09:30–10:00 and is kept. Daily bars are the resample of those hourly bars.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

TIMEFRAMES = ("1min", "5min", "15min", "30min", "1h", "1d")
MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60}
# What Equibles calls the bar. Daily snapshots are resampled here, not fetched.
EQUIBLES_INTERVAL = {**MINUTES, "1h": 60}
EQUIBLES_INTERVAL_NAME = {
    "1min": "1min",
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
    "1h": "1hour",
    "1d": "1hour",
}


def minutes_of(timeframe: str) -> int:
    if timeframe not in MINUTES:
        raise ValueError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    return MINUTES[timeframe]


def session_label(ts: pd.Timestamp | datetime) -> str:
    """Date for a midnight bar, ``YYYY-MM-DDTHH:MM`` otherwise.

    Daily snapshots are stored at midnight, so their labels stay dates. An hourly
    bar is never midnight.
    """
    ts = pd.Timestamp(ts)
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
        return ts.date().isoformat()
    return ts.strftime("%Y-%m-%dT%H:%M")


def on_grid(start: datetime, minutes: int) -> bool:
    """True when ``start`` is a regular-session bar of ``minutes``."""
    if start.second or start.microsecond:
        return False
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    delta = int((start - midnight).total_seconds() // 60)
    if delta % minutes != 0:
        return False
    end = start + timedelta(minutes=minutes)
    open_ = start.replace(hour=9, minute=30, second=0, microsecond=0)
    close = start.replace(hour=16, minute=0, second=0, microsecond=0)
    return start < close and end > open_


def regular_starts(day: date, minutes: int) -> list[datetime]:
    """Bar starts on ``day`` that overlap the regular session."""
    midnight = datetime(day.year, day.month, day.day)
    out: list[datetime] = []
    t = midnight
    while t.date() == day:
        if on_grid(t, minutes):
            out.append(t)
        t += timedelta(minutes=minutes)
    return out


def label_in_range(label: str, start: str | None, end: str | None) -> bool:
    """A date bound includes that whole session. A bar-start bound is exact."""
    if start:
        if "T" not in start:
            if label[:10] < start[:10]:
                return False
        elif label < start:
            return False
    if end:
        if "T" not in end:
            if label[:10] > end[:10]:
                return False
        elif label > end:
            return False
    return True


def resample_daily(bars: pd.DataFrame) -> pd.DataFrame:
    """One daily bar per symbol and session date from intraday bars.

    Open is the first bar, high the highest, low the lowest, close the last,
    volume the sum. The 09:00 half-hour is included when it is present.
    """
    if bars.empty:
        return pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    frame = bars.sort_values(["symbol", "bar_start"])
    rows: list[dict[str, object]] = []
    for (sym, day), group in frame.groupby(["symbol", "date"], sort=True):
        rows.append(
            {
                "date": pd.Timestamp(day),
                "symbol": sym,
                "open": float(group.iloc[0]["open"]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group.iloc[-1]["close"]),
                "volume": int(group["volume"].sum()),
            }
        )
    return pd.DataFrame(rows)
