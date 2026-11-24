"""Corporate actions: turn raw prices into split- and dividend-adjusted prices.

Actions file format (CSV): ``date,symbol,type,value``

* ``split``: ``value`` is new shares per old share (2-for-1 is ``2``). Every price
  before ``date`` is divided by ``value`` and volume multiplied by it.
* ``dividend``: ``value`` is the cash amount per share with ex-date ``date``. Every price
  before ``date`` is multiplied by ``1 - value / previous_close`` (total-return
  adjustment), where ``previous_close`` is the raw close of the session before ``date``.

Adjusting is done once, at import, and the result is an immutable snapshot. A later
action produces a new snapshot; runs on the earlier snapshot stay reproducible. Ledger
quantities are therefore in adjusted shares of the snapshot the run used.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ACTION_COLUMNS = ["date", "symbol", "type", "value"]
POLICY = "split_and_dividend_adjusted_from_raw"


def read_actions(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in ACTION_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing column(s) {missing}")
    df = df[ACTION_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["type"] = df["type"].str.lower().str.strip()
    bad = set(df["type"]) - {"split", "dividend"}
    if bad:
        raise ValueError(f"unknown action type(s) {sorted(bad)}")
    if (df["value"] <= 0).any():
        raise ValueError("action values must be positive")
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


def adjust(raw: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Return adjusted bars. ``raw`` uses the file-import columns."""
    out = raw.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out = out.sort_values(["symbol", "date"]).reset_index(drop=True)
    prices = ["open", "high", "low", "close"]
    out[prices] = out[prices].astype(float)
    out["volume"] = out["volume"].astype(float)
    for _, a in actions.iterrows():
        sym_rows = out["symbol"] == a["symbol"]
        before = sym_rows & (out["date"] < a["date"])
        if not before.any():
            continue
        if a["type"] == "split":
            out.loc[before, prices] /= float(a["value"])
            out.loc[before, "volume"] *= float(a["value"])
        else:
            prev_close = float(raw_close_before(raw, a["symbol"], a["date"]))
            factor = 1.0 - float(a["value"]) / prev_close
            if factor <= 0:
                raise ValueError(f"dividend {a['value']} >= previous close for {a['symbol']}")
            out.loc[before, prices] *= factor
    out[prices] = out[prices].round(6)
    out["volume"] = out["volume"].round().astype("int64")
    return out


def raw_close_before(raw: pd.DataFrame, symbol: str, when: pd.Timestamp) -> float:
    r = raw[(raw["symbol"] == symbol) & (pd.to_datetime(raw["date"]) < when)]
    if r.empty:
        raise ValueError(f"no raw close before {when.date()} for {symbol}")
    return float(r.sort_values("date")["close"].iloc[-1])
