"""Write recorded-style Tiingo End-of-Day payloads used by the offline provider tests.

``split_dividend/{SYMBOL}.json``: raw prices rebuilt from
``fixtures/data/rsi_divergence.csv`` with a 3-for-1 split (BBB), a 2-for-1 split (DDD)
and cash dividends (AAA, SPY), one response per symbol. The same data is written in
the file adapter's format (``split_dividend_raw.csv`` + ``split_dividend_actions.csv``)
so both adapters can load identical reference data. (The no-action case is built in
the tests directly from the CSV fixture.)

Payload items have Tiingo's field names and order. Run:
``uv run python scripts/make_tiingo_fixtures.py``
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fixtures" / "data" / "rsi_divergence.csv"
OUT = ROOT / "fixtures" / "tiingo"
PRICES = ["open", "high", "low", "close"]

ACTIONS = [
    ("2023-02-15", "AAA", "dividend", 0.85),
    ("2023-03-01", "BBB", "split", 3.0),
    ("2023-03-17", "SPY", "dividend", 1.5),
    ("2023-05-01", "DDD", "split", 2.0),
]


def unadjust(adj: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Invert moneylab.data.actions.adjust (latest action first)."""
    raw = adj.copy()
    raw["volume"] = raw["volume"].astype(float)
    for _, a in actions.sort_values("date", ascending=False).iterrows():
        before = (raw["symbol"] == a["symbol"]) & (raw["date"] < a["date"])
        if a["type"] == "split":
            raw.loc[before, PRICES] *= a["value"]
            raw.loc[before, "volume"] /= a["value"]
        else:
            prev = float(raw.loc[before, "close"].iloc[-1])
            raw.loc[before, PRICES] *= (prev + a["value"]) / prev
    raw[PRICES] = raw[PRICES].round(4)
    raw["volume"] = raw["volume"].round().astype("int64")
    return raw


def payload(raw: pd.DataFrame, adj: pd.DataFrame, actions: pd.DataFrame) -> list[dict[str, object]]:
    items = []
    for (_, r), (_, a) in zip(raw.iterrows(), adj.iterrows(), strict=True):
        act = actions[(actions["symbol"] == r["symbol"]) & (actions["date"] == r["date"])]
        split = act.loc[act["type"] == "split", "value"]
        div = act.loc[act["type"] == "dividend", "value"]
        items.append(
            {
                "date": r["date"].strftime("%Y-%m-%dT00:00:00.000Z"),
                "close": float(r["close"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "open": float(r["open"]),
                "volume": int(r["volume"]),
                "adjClose": float(a["close"]),
                "adjHigh": float(a["high"]),
                "adjLow": float(a["low"]),
                "adjOpen": float(a["open"]),
                "adjVolume": int(a["volume"]),
                "divCash": float(div.iloc[0]) if len(div) else 0.0,
                "splitFactor": float(split.iloc[0]) if len(split) else 1.0,
            }
        )
    return items


def write(name: str, raw: pd.DataFrame, adj: pd.DataFrame, actions: pd.DataFrame) -> None:
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    for sym, g in raw.groupby("symbol"):
        items = payload(g, adj[adj["symbol"] == sym], actions)
        (d / f"{sym}.json").write_text(json.dumps(items, separators=(",", ":")) + "\n")


def main() -> None:
    adj = pd.read_csv(SOURCE, parse_dates=["date"]).sort_values(["symbol", "date"])
    adj = adj.reset_index(drop=True)
    actions = pd.DataFrame(ACTIONS, columns=["date", "symbol", "type", "value"])
    actions["date"] = pd.to_datetime(actions["date"])
    raw = unadjust(adj, actions)
    write("split_dividend", raw, adj, actions)
    raw.to_csv(OUT / "split_dividend_raw.csv", index=False, date_format="%Y-%m-%d")
    actions.to_csv(OUT / "split_dividend_actions.csv", index=False, date_format="%Y-%m-%d")


if __name__ == "__main__":
    main()
