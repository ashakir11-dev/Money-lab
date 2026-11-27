"""Score paper fills against the fills the frozen model produces on the same bars.

A mismatch is a report. This module does not edit the entry, the filter, or the exit,
and it does not change transaction costs.
"""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd

from moneylab.data.snapshot import MarketData
from moneylab.engine.simulator import simulate


def score_account(
    resolved: dict[str, Any], data: MarketData, paper_fills: pd.DataFrame
) -> dict[str, Any]:
    """Run the frozen recipe unchanged and compare its fills with the paper account."""
    frozen = copy.deepcopy(resolved)
    model = simulate(frozen, data)
    report = score_fills(model.fills, paper_fills)
    report["recipe_edited"] = False
    report["cost_bps_per_side"] = frozen["portfolio"]["cost_bps_per_side"]
    return report


def score_fills(model: pd.DataFrame, paper: pd.DataFrame) -> dict[str, Any]:
    """Match fills on session, symbol, and side. Prices and quantities are compared."""
    model_rows = _index(model)
    paper_rows = _index(paper)
    missing = [key for key in model_rows if key not in paper_rows]
    extra = [key for key in paper_rows if key not in model_rows]
    price_diffs: list[dict[str, Any]] = []
    qty_diffs: list[dict[str, Any]] = []
    for key in model_rows.keys() & paper_rows.keys():
        left, right = model_rows[key], paper_rows[key]
        if int(left["qty"]) != int(right["qty"]):
            qty_diffs.append(
                {"fill": key, "model_qty": int(left["qty"]), "paper_qty": int(right["qty"])}
            )
        if abs(float(left["price"]) - float(right["price"])) > 0.01:
            price_diffs.append(
                {
                    "fill": key,
                    "model_price": float(left["price"]),
                    "paper_price": float(right["price"]),
                }
            )
    return {
        "match": not missing and not extra and not price_diffs and not qty_diffs,
        "missing": missing,
        "extra": extra,
        "price_diffs": price_diffs,
        "qty_diffs": qty_diffs,
        "model_fills": len(model_rows),
        "paper_fills": len(paper_rows),
    }


def _index(fills: pd.DataFrame) -> dict[tuple[str, str, str], dict[str, Any]]:
    if fills is None or fills.empty:
        return {}
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in fills.itertuples(index=False):
        session = str(row.session)[:10]
        key = (session, str(row.symbol), str(row.side))
        out[key] = {"qty": row.qty, "price": row.price}
    return out
