"""Hand-calculated engine fixtures (acceptance gate 4, historical part).

Each scenario's expected orders, skips, quantities and cash were worked out by hand
from the rules in docs/decisions.md and are written next to the assertions.
All entries use "close above X" (SMA of length 1 is the close) so signals are obvious.
"""

from __future__ import annotations

import pandas as pd

from fixtures_util import bars, day, decisions, fills, level_strategy, run
from moneylab.data.actions import adjust


def test_simultaneous_signals_tie_break_max_positions_and_costs() -> None:
    # XA, XB, XC all close at 11 (> 10.5) on day 1. Cash 1000, target 30%, max 2
    # positions, a stored 10 bps cost that is not charged, exit after 2 sessions.
    flat = [(10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (11.0, 11.0), (11.0, 11.0), (12.0, 10.0)]
    xc = [*flat[:5], (11.0, 10.0)]
    df = bars({"XA": flat, "XB": flat, "XC": xc})
    r = run(
        df,
        level_strategy(
            ["XA", "XB", "XC"],
            10.5,
            hold=2,
            starting_cash=1000,
            target_allocation=0.3,
            max_positions=2,
            cost_bps_per_side=10,
        ),
    )

    # Day 1 close, ascending symbol order: XA and XB take the two slots,
    # qty = floor(0.3 * 1000 / 11) = 27 each; XC is skipped for max_positions.
    # Holding count: the fill session (day 2) is holding session one, so at the day 3
    # close XA and XB have been held 2 sessions -> exits. That frees both slots and XC
    # enters: equity 406 + 54 * 11 = 1,000, qty = floor(300 / 11) = 27.
    # Day 4 close: XA is free again and closes 11 > 10.5; one slot is open (XC holds the
    # other), so XA enters (equity 703 + 27 * 11 = 1,000, qty floor(300 / 11) =
    # 27) and XB is skipped for max_positions. Day 5 close: XC (filled day 4) has been
    # held 2 sessions -> exit, which has no later session to fill in.
    assert decisions(r, "enter", "exit", "skip") == [
        (day(1), "XA", "enter", "all entry conditions passed"),
        (day(1), "XB", "enter", "all entry conditions passed"),
        (day(1), "XC", "skip", "max_positions"),
        (day(2), "XA", "skip", "already_held"),
        (day(2), "XB", "skip", "already_held"),
        (day(2), "XC", "skip", "max_positions"),
        (day(3), "XA", "exit", "held 2 sessions >= 2"),
        (day(3), "XB", "exit", "held 2 sessions >= 2"),
        (day(3), "XA", "skip", "already_held"),
        (day(3), "XB", "skip", "already_held"),
        (day(3), "XC", "enter", "all entry conditions passed"),
        (day(4), "XA", "enter", "all entry conditions passed"),
        (day(4), "XB", "skip", "max_positions"),
        (day(4), "XC", "skip", "already_held"),
        (day(5), "XC", "exit", "held 2 sessions >= 2"),
    ]
    # Buys at day-2 open 11: gross 297.00, cost 0, cash 1000 - 297 = 703, then 406.
    # Day 4 open 11: exits first (11 x 27 = 297, cost 0, +297 each -> 703, 1000),
    # then XC buys at 11: 1000 - 297 = 703.
    # Day 5 open 12: XA buys 27: gross 324.00, cost 0, cash 379.
    assert fills(r) == [
        (day(2), "XA", "buy", 27, 11.0, 0.0, 703.0),
        (day(2), "XB", "buy", 27, 11.0, 0.0, 406.0),
        (day(4), "XA", "sell", 27, 11.0, 0.0, 703.0),
        (day(4), "XB", "sell", 27, 11.0, 0.0, 1000.0),
        (day(4), "XC", "buy", 27, 11.0, 0.0, 703.0),
        (day(5), "XA", "buy", 27, 12.0, 0.0, 379.0),
    ]


def test_insufficient_cash_at_decision_and_opening_gap() -> None:
    # Cash 1000, target 60%. A stored 5 bps cost is not charged. YA and YB close at 20
    # (> 15) on day 1.
    # YA: qty floor(600 / 20) = 30, est 30 x 20 = 600 -> enter.
    # YB: qty 30, est 600 > available 1000 - 600 = 400 -> insufficient_cash.
    # Day 2 YA opens at 34 (gap): 30 x 34 = 1020 > cash 1000, so the whole
    # order is skipped with reason insufficient_cash_at_open (never a smaller quantity).
    ya = [(10.0, 10.0), (10.0, 20.0), (34.0, 34.0)]
    yb = [(10.0, 10.0), (10.0, 20.0), (20.0, 12.0)]
    r = run(
        bars({"YA": ya, "YB": yb}),
        level_strategy(
            ["YA", "YB"],
            15,
            hold=50,
            starting_cash=1000,
            target_allocation=0.6,
            cost_bps_per_side=5,
        ),
    )
    skip = r.decisions[(r.decisions["symbol"] == "YB") & (r.decisions["action"] == "skip")]
    assert skip[["session", "reason", "qty", "est_cost", "available"]].values.tolist() == [
        [day(1), "insufficient_cash", 30.0, 600.0, 400.0]
    ]
    gap = r.decisions[(r.decisions["symbol"] == "YA") & (r.decisions["action"] == "skip")]
    assert gap[
        ["session", "reason", "order_qty", "open", "est_cost", "available"]
    ].values.tolist() == [[day(2), "insufficient_cash_at_open", 30.0, 34.0, 1020.0, 1000.0]]
    assert len(r.fills) == 0
    assert r.equity["cash"].tolist() == [1000.0, 1000.0, 1000.0]


def test_unaffordable_open_records_a_skip_not_negative_cash() -> None:
    # Cash 100, target 100%, 0 bps. Close 10 -> qty 10. Next open 150: zero shares
    # affordable -> skip "insufficient_cash_at_open", no fill, cash stays 100.
    za = [(10.0, 5.0), (5.0, 10.0), (150.0, 150.0)]
    r = run(
        bars({"ZA": za}),
        level_strategy(
            ["ZA"], 8, hold=5, starting_cash=100, target_allocation=1.0, cost_bps_per_side=0
        ),
    )
    assert decisions(r, "skip")[0] == (day(2), "ZA", "skip", "insufficient_cash_at_open")
    assert len(r.fills) == 0
    assert r.equity["cash"].tolist() == [100.0, 100.0, 100.0]


def test_missing_bar_waits_and_never_invents_a_price() -> None:
    # MA closes at 20 (> 15) on day 1. Cash 1000, target 50%. A stored 5 bps cost is not
    # charged: qty 25.
    # Day 2 has no MA bar: recorded as missing_bar, the order waits (no invented price).
    # Day 3 open 21: gross 525.00, cost 0, cash 475.00, waited 2 sessions.
    ma = [(10.0, 10.0), (10.0, 20.0), None, (21.0, 21.0)]
    mb = [(5.0, 5.0)] * 4  # keeps day 2 a session in the snapshot
    r = run(
        bars({"MA": ma, "MB": mb}),
        level_strategy(
            ["MA", "MB"],
            15,
            hold=50,
            starting_cash=1000,
            target_allocation=0.5,
            cost_bps_per_side=5,
        ),
    )
    assert (day(2), "MA", "ineligible", "missing_bar") in decisions(r)
    assert fills(r) == [(day(3), "MA", "buy", 25, 21.0, 0.0, 475.0)]
    assert r.fills["sessions_waited"].tolist() == [2]
    # Day 2 equity values MA with its last known close, but no trade used that price.
    assert r.equity["cash"].tolist() == [1000.0, 1000.0, 1000.0, 475.0]


def test_corporate_actions_split_and_dividend() -> None:
    # Raw SP: 100 for days 0-2, 2-for-1 split effective day 3 (raw 50), $1 dividend
    # ex day 4 (raw 49). Adjusted: split halves days 0-2 to 50; dividend factor
    # 1 - 1/50 = 0.98 applies to days 0-3 -> 49 everywhere. The adjusted series is flat.
    raw = bars({"SP": [(100.0, 100.0)] * 3 + [(50.0, 50.0), (49.0, 49.0), (49.0, 49.0)]})
    actions = pd.DataFrame(
        {
            "date": pd.to_datetime([day(3), day(4)]),
            "symbol": ["SP", "SP"],
            "type": ["split", "dividend"],
            "value": [2.0, 1.0],
        }
    )
    adj = adjust(raw, actions)
    assert adj["close"].tolist() == [49.0] * 6
    assert adj["volume"].tolist() == [2000] * 3 + [1000] * 3

    # Buy on day 0 close 49: qty floor(500 / 49) = 10, fill day 1 open 49, 0 bps.
    # Holding through the split and dividend creates no phantom profit or loss.
    cfg = level_strategy(
        ["SP"], 1, hold=50, starting_cash=1000, target_allocation=0.5, cost_bps_per_side=0
    )
    r = run(adj, cfg)
    assert fills(r) == [(day(1), "SP", "buy", 10, 49.0, 0.0, 510.0)]
    assert r.equity["equity"].tolist() == [1000.0] * 6

    # The same position on unadjusted prices would show a fake 250.00 loss on the split.
    raw_r = run(
        raw,
        level_strategy(
            ["SP"], 1, hold=50, starting_cash=1000, target_allocation=0.5, cost_bps_per_side=0
        ),
    )
    assert raw_r.equity["equity"].iloc[3] - raw_r.equity["equity"].iloc[2] == -250.0
