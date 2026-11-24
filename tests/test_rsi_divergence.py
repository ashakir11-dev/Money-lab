"""Why the RSI<30 and RSI<35 ledgers differ, checked against hand calculations.

Fixture facts for AAA on 2023-04-04 (see scripts/make_fixture.py):
    previous close 71.97 <= previous SMA20 72.475   and   close 74.56 > SMA20 72.554
    -> "close crosses above SMA20" passes.
    RSI14 = 31.680231 -> fails "below 30", passes "below 35".
So only the RSI<35 variant buys AAA, at the next open (2023-04-05, 74.91).

Hand calculation of that fill (defaults: 10% target, 5 bps per side):
    equity at 2023-04-04 close = 100,380.00 (cash after the DDD round trip)
    qty   = floor(0.10 * 100,380.00 / 74.56) = floor(134.63) = 134
    gross = 134 * 74.91 = 10,037.94
    cost  = 10,037.94 * 0.0005 = 5.02
    cash  = 100,380.00 - 10,037.94 - 5.02 = 90,337.04
"""

from __future__ import annotations

import pandas as pd

from moneylab.data.snapshot import MarketData
from moneylab.demo import rsi_strategy
from moneylab.engine.config import resolve
from moneylab.engine.simulator import simulate

ORIGINAL_FILLS = [
    # session, symbol, side, qty, price, gross, cost, cash_after
    ("2023-03-08", "DDD", "buy", 135, 73.71, 9950.85, 4.98, 90044.17),
    ("2023-03-23", "DDD", "sell", 135, 76.60, 10341.00, 5.17, 100380.00),
]
AAA_FILLS = [
    ("2023-04-05", "AAA", "buy", 134, 74.91, 10037.94, 5.02, 90337.04),
    ("2023-04-21", "AAA", "sell", 134, 77.23, 10348.82, 5.17, 100680.69),
]
COLS = ["session", "symbol", "side", "qty", "price", "gross", "cost", "cash_after"]


def _fills(lab, run_id):  # type: ignore[no-untyped-def]
    f = lab.store.ledgers(run_id).fills[COLS]
    return [tuple(r) for r in f.itertuples(index=False)]


def test_original_ledger_matches_hand_calculation(demo) -> None:  # type: ignore[no-untyped-def]
    assert _fills(demo["lab"], demo["original"]) == ORIGINAL_FILLS


def test_changed_ledger_adds_exactly_the_aaa_trade(demo) -> None:  # type: ignore[no-untyped-def]
    assert _fills(demo["lab"], demo["changed"]) == ORIGINAL_FILLS + AAA_FILLS


def test_hand_calculated_aaa_fill() -> None:
    equity = 100_380.00
    qty = int(0.10 * equity // 74.56)
    gross = round(qty * 74.91, 2)
    cost = round(gross * 5 / 10_000, 2)
    assert (qty, gross, cost, round(equity - gross - cost, 2)) == (
        AAA_FILLS[0][3],
        AAA_FILLS[0][5],
        AAA_FILLS[0][6],
        AAA_FILLS[0][7],
    )


def test_only_the_threshold_differs(demo) -> None:  # type: ignore[no-untyped-def]
    cmp = demo["lab"].compare(demo["original"], demo["changed"])
    assert cmp["config_diff"] == [{"setting": "entry[1].params.value", "a": 30.0, "b": 35.0}]


def test_first_changed_decision_is_aaa_and_explains_why(demo) -> None:  # type: ignore[no-untyped-def]
    fd = demo["lab"].compare(demo["original"], demo["changed"])["first_difference"]
    assert (fd["session"], fd["symbol"]) == ("2023-04-04", "AAA")

    a, b = fd["a"], fd["b"]
    assert a["action"] == "no_signal" and a["reason"] == "failed: rsi(length=14) below 30"
    assert b["action"] == "enter"

    cross_a, rsi_a = a["conditions"]
    cross_b, rsi_b = b["conditions"]
    assert cross_a["passed"] is True and cross_b["passed"] is True
    assert a["values"]["prev close"] == 71.97 and a["values"]["prev sma(length=20)"] == 72.475
    assert a["values"]["close"] == 74.56 and a["values"]["sma(length=20)"] == 72.554
    assert rsi_a["observed"] == rsi_b["observed"] == 31.680231
    assert (rsi_a["threshold"], rsi_a["passed"]) == (30.0, False)
    assert (rsi_b["threshold"], rsi_b["passed"]) == (35.0, True)

    assert b["fill"]["qty"] == 134 and b["fill"]["price"] == 74.91
    assert fd["impact"]["cash_a"] == 100_380.00
    assert fd["impact"]["cash_b"] == 90_337.04
    assert fd["impact"]["final_equity_delta"] == 300.69


def test_trading_decisions_before_the_divergence_are_identical(demo) -> None:  # type: ignore[no-untyped-def]
    lab = demo["lab"]
    cols = ["session", "symbol", "action", "reason"]

    def trading(run_id: str) -> pd.DataFrame:
        d = lab.store.ledgers(run_id).decisions
        d = d[d["action"].isin(["enter", "exit", "skip"]) & (d["session"] < "2023-04-04")]
        return d[cols].reset_index(drop=True)

    pd.testing.assert_frame_equal(trading(demo["original"]), trading(demo["changed"]))


def test_identical_threshold_reports_no_difference(demo) -> None:  # type: ignore[no-untyped-def]
    lab = demo["lab"]
    again = lab.run(
        rsi_strategy(30, name="same again"), demo["snapshot"], parent_id=demo["original"]
    )
    cmp = lab.compare(demo["original"], again)
    assert cmp["config_diff"] == []
    assert cmp["first_difference"] is None
    assert _fills(lab, again) == ORIGINAL_FILLS


def test_future_prices_cannot_change_the_aaa_decision(demo) -> None:  # type: ignore[no-untyped-def]
    lab = demo["lab"]
    bars = lab.snapshot(demo["snapshot"]).bars.copy()
    later = (bars["symbol"] == "AAA") & (bars["date"] > "2023-04-04")
    bars.loc[later, ["open", "high", "low", "close"]] *= 0.5  # rewrite the future

    resolved = resolve(rsi_strategy(35), lab.settings)
    syms = [*resolved["universe"], "SPY"]
    base = simulate(resolved, MarketData.build(lab.snapshot(demo["snapshot"]).bars, syms))
    edited = simulate(resolved, MarketData.build(bars, syms))
    cols = ["session", "symbol", "action", "reason"]
    upto = lambda d: d[d["session"] <= "2023-04-04"][cols].reset_index(drop=True)  # noqa: E731
    pd.testing.assert_frame_equal(upto(base.decisions), upto(edited.decisions))


def test_reopened_run_shows_resolved_settings(demo) -> None:  # type: ignore[no-untyped-def]
    run = demo["lab"].store.get(demo["changed"])
    r = run["resolved"]
    assert run["parent_id"] == demo["original"]
    assert r["entry"][1]["indicator"] == {"id": "rsi", "version": "1.0.0", "params": {"length": 14}}
    assert r["exit"]["params"] == {"sessions": 10}
    assert r["portfolio"]["starting_cash"] == 100_000.0
    assert r["portfolio"]["cost_bps_per_side"] == 5.0
    assert r["data"]["snapshot_id"] == demo["snapshot"]
