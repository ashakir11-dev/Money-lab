"""Run analytics on small fixtures whose every number can be checked by hand."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fixtures_util import day
from moneylab.api.app import jsonable
from moneylab.engine import analytics as an


def fill(i: int, sym: str, side: str, qty: int, px: float, cost: float, did: Any, note: str = ""):
    return {
        "session": day(i),
        "symbol": sym,
        "side": side,
        "qty": qty,
        "price": px,
        "gross": qty * px,
        "cost": cost,
        "cash_after": 0.0,
        "decision_id": did,
        "sessions_waited": 1,
        "note": note,
    }


FILLS = pd.DataFrame(
    [
        fill(0, "SPY", "buy", 5, 100.0, 0.5, None, "sweep"),
        fill(0, "XA", "buy", 10, 10.0, 1.0, 1),
        fill(1, "XB", "buy", 5, 20.0, 0.0, 2),
        fill(2, "XA", "sell", 10, 12.0, 1.0, 10),
        fill(2, "XC", "buy", 10, 10.0, 0.0, 3),
        fill(3, "XD", "buy", 5, 10.0, 0.0, 4),
        fill(4, "XB", "sell", 5, 18.0, 0.0, 11),
        fill(5, "XC", "sell", 10, 11.0, 0.0, 12),
        fill(5, "SPY", "sell", 5, 105.0, 0.5, None, "sweep"),
    ]
)
EQUITY = pd.DataFrame(
    {
        "session": [day(i) for i in range(6)],
        "equity": [1000.0, 1010.0, 990.0, 1000.0, 1050.0, 1100.0],
        "benchmark_equity": [1000.0, 1005.0, 1010.0, 1000.0, 1020.0, 1030.0],
    }
)
DECISIONS = pd.DataFrame(
    [
        (1, "enter", "all entry conditions passed"),
        (2, "enter", "all entry conditions passed"),
        (3, "enter", "all entry conditions passed"),
        (4, "enter", "all entry conditions passed"),
        (5, "skip", "already_held"),
        (6, "skip", "already_held"),
        (7, "skip", "max_positions"),
        (8, "skip", "insufficient_cash_at_open"),
        (9, "no_signal", "failed: x"),
        (10, "exit", "take_profit: close 12 >= 11.5 (15% above entry 10)"),
        (11, "exit", "stop_loss: close 18 <= 18.4 (8% below entry 20)"),
        (12, "exit", "held 3 sessions >= 3"),
    ],
    columns=["decision_id", "action", "reason"],
)


def frame(opens: list[float], lows: list[float], highs: list[float], closes: list[float]):
    return pd.DataFrame({"open": opens, "low": lows, "high": highs, "close": closes})


FRAMES = {
    "SPY": frame(
        [100, 101, 102, 103, 104, 105], [99] * 6, [106] * 6, [100, 101, 102, 103, 104, 105]
    ),
    "XA": frame(
        [10, 11, 12, 12, 12, 12], [9, 9.5, 11, 11, 11, 11], [10.5, 12.5, 13, 13, 13, 13], [10] * 6
    ),
    "XB": frame([20, 20, 19, 19, 18, 18], [19] * 6, [21] * 6, [20] * 6),
    "XC": frame([10, 10, 10, 10, 10, 11], [9] * 6, [12] * 6, [10] * 6),
}


def trips() -> pd.DataFrame:
    return an.round_trips(FILLS, EQUITY, DECISIONS, FRAMES, "SPY", "1d")


def test_round_trips_fifo_costs_hold_exit_kind_and_sweep_excluded() -> None:
    t = trips()
    assert list(t["symbol"]) == ["XA", "XB", "XC"]  # sweep SPY and the open XD are not trades
    xa, xb, xc = (t.iloc[i] for i in range(3))
    # XA: 10 x (12 - 10) - 1 - 1 = 18 on a basis of 100 + 1.
    assert xa["pnl"] == pytest.approx(18.0)
    assert xa["ret"] == pytest.approx(18 / 101)
    assert xb["pnl"] == pytest.approx(-10.0)
    assert xb["ret"] == pytest.approx(-0.1)
    assert xc["pnl"] == pytest.approx(10.0)
    # Hold counts sessions from the entry fill to the exit fill.
    assert [int(x) for x in t["hold_sessions"]] == [2, 3, 3]
    assert list(t["exit_kind"]) == ["take_profit", "stop_loss", "max_hold"]
    # Benchmark open to open over the same sessions: 102/100, 104/101, 105/102.
    assert xa["bench_ret"] == pytest.approx(0.02)
    assert xb["bench_ret"] == pytest.approx(104 / 101 - 1)
    assert xc["edge"] == pytest.approx(0.1 - (105 / 102 - 1))
    # XA entered at 10; sessions 0-1 lows 9 and 9.5, highs 10.5 and 12.5, exit price 12.
    assert xa["mae"] == pytest.approx(-0.1)
    assert xa["mfe"] == pytest.approx(0.25)
    assert list(t["regime"]) == ["warmup"] * 3


def test_fifo_matches_partial_sells_in_order() -> None:
    f = pd.DataFrame(
        [
            fill(0, "XA", "buy", 10, 10.0, 0.0, 1),
            fill(1, "XA", "buy", 10, 20.0, 0.0, 2),
            fill(2, "XA", "sell", 15, 30.0, 0.0, 3),
        ]
    )
    t = an.round_trips(f, EQUITY)
    # 10 shares from the first lot (+200) then 5 from the second (+50).
    assert [(r.qty, r.pnl) for r in t.itertuples()] == [(10, 200.0), (5, 50.0)]
    assert [int(x) for x in t["hold_sessions"]] == [2, 1]


def test_trade_stats() -> None:
    s = an.trade_stats(trips())
    assert (s["count"], s["wins"], s["losses"], s["low_sample"]) == (3, 2, 1, True)
    assert s["win_rate"] == pytest.approx(2 / 3)
    assert s["avg_win"] == pytest.approx(14.0)
    assert s["avg_loss"] == pytest.approx(-10.0)
    assert s["payoff_ratio"] == pytest.approx(1.4)
    assert s["expectancy"] == pytest.approx(6.0)
    assert s["profit_factor"] == pytest.approx(2.8)
    assert (s["largest_win"], s["largest_loss"]) == (18.0, -10.0)
    # rets 0.178218, -0.1, 0.1: mean 0.059406, sample sd 0.143482, t = mean / (sd / sqrt 3).
    assert s["t_stat"] == pytest.approx(0.7171, abs=1e-3)
    assert s["avg_hold"] == pytest.approx(8 / 3)
    assert s["median_hold"] == 3
    assert (s["avg_hold_win"], s["avg_hold_loss"]) == (2.5, 3.0)
    assert (s["max_consecutive_losses"], s["max_consecutive_wins"]) == (1, 1)


def test_trade_stats_edge_cases() -> None:
    assert an.trade_stats(an.round_trips(FILLS.iloc[:0], EQUITY)) == {
        "count": 0,
        "low_sample": True,
    }
    only_wins = trips().iloc[[0, 2]]
    s = an.trade_stats(only_wins)
    assert s["profit_factor"] is None and s["avg_loss"] is None and s["largest_loss"] is None
    assert s["payoff_ratio"] is None
    many = pd.concat([trips()] * 10, ignore_index=True)
    assert an.trade_stats(many)["low_sample"] is False  # 30 trades


def test_edge_stats() -> None:
    e = an.edge_stats(trips())
    assert e["count"] == 3 and e["low_sample"] is True
    assert e["mean"] == pytest.approx(
        ((0.178218 - 0.02) + (-0.1 - 0.029703) + 0.070588) / 3, abs=1e-5
    )
    assert e["beat_rate"] == pytest.approx(2 / 3)
    assert an.edge_stats(an.round_trips(FILLS.iloc[:0], EQUITY)) == {"count": 0}


def test_drawdown_stats() -> None:
    e = np.array([100, 110, 98, 105, 110, 121], dtype=float)
    d = an.drawdown_stats(e, [day(i) for i in range(6)])
    assert d["max_drawdown"] == pytest.approx(98 / 110 - 1)
    # Peak on session 1, trough on 2, back to 110 on session 4.
    assert (d["peak_session"], d["trough_session"], d["recovery_session"]) == (
        day(1),
        day(2),
        day(4),
    )
    assert d["max_dd_length"] == 3
    assert d["longest_underwater"] == 2 and d["longest_underwater_start"] == day(1)
    assert d["drawdowns_over_10pct"] == 1
    assert d["time_underwater"] == pytest.approx(2 / 6)
    assert d["current_drawdown"] == 0
    # Never recovers: no recovery session or length.
    d2 = an.drawdown_stats(np.array([100.0, 90.0, 95.0]), ["a", "b", "c"])
    assert d2["recovery_session"] is None and d2["max_dd_length"] is None
    assert d2["longest_underwater"] == 2 and d2["current_drawdown"] == pytest.approx(-0.05)


def test_series_stats_and_cagr() -> None:
    r = np.array([0.03, -0.01, 0.02, -0.02])
    s = an._series_stats(r, 252)
    sd = math.sqrt(0.0017 / 3)
    dsd = math.sqrt(0.0005 / 4)
    assert s["volatility"] == pytest.approx(sd * math.sqrt(252))
    assert s["sharpe"] == pytest.approx(0.005 / sd * math.sqrt(252))
    assert s["sortino"] == pytest.approx(0.005 / dsd * math.sqrt(252))
    flat = an._series_stats(np.zeros(5), 252)
    assert flat["sharpe"] is None and flat["sortino"] is None and flat["volatility"] == 0.0
    # 253 bars is exactly 252 bar-intervals = one year: 100 -> 121 is 21 percent.
    e = np.linspace(100, 121, 253)
    assert an._cagr(e, 252) == pytest.approx(0.21)
    assert an._cagr(np.array([100.0]), 252) is None
    # Hourly runs annualise by 1764 bars: the same series over 1764 bars is a year.
    assert an._cagr(np.linspace(100, 121, 1765), 1764) == pytest.approx(0.21)


def test_beta_correlation_alpha() -> None:
    b = np.array([0.01, -0.02, 0.015, 0.0, 0.01])
    m = an._beta(2 * b, b, 252)
    assert m["beta"] == pytest.approx(2.0)
    assert m["correlation"] == pytest.approx(1.0)
    assert m["alpha"] == pytest.approx(0.0, abs=1e-12)
    # A constant 1 bp per bar on top of the market is an annual alpha of 252 bp.
    m2 = an._beta(b + 0.0001, b, 252)
    assert m2["beta"] == pytest.approx(1.0)
    assert m2["alpha"] == pytest.approx(0.0252)
    assert an._beta(b, np.zeros(5), 252)["beta"] is None


def test_period_returns_use_the_previous_close_as_base() -> None:
    v = np.array([100, 110, 121, 121, 133.1, 133.1])
    out = an._period_returns(v, ["2023", "2023", "2024", "2024", "2024", "2024"])
    assert out["2023"] == pytest.approx(0.10)  # first value is the base for the first period
    assert out["2024"] == pytest.approx(133.1 / 110 - 1)


def test_regime_uses_the_prior_close_against_a_200_session_average() -> None:
    n = 215
    closes = [100.0 + i for i in range(205)] + [50.0] * 10
    frames = {"SPY": frame(closes, closes, closes, closes)}
    reg = an._regimes(frames, "SPY", n, "1d")
    assert reg is not None
    assert reg[199] == "warmup" and reg[200] == "up"
    # Session 205 still sees the prior close (session 204) above its average; the next
    # sees the 50 close far below it.
    assert reg[205] == "up" and reg[206] == "down"
    assert an._regimes({}, "SPY", n, "1d") is None


def test_analyze_end_to_end() -> None:
    a = an.analyze(EQUITY, FILLS, DECISIONS, FRAMES, "SPY", "1d")
    assert a["trades"]["count"] == 3
    c = a["costs"]
    assert c["total"] == pytest.approx(3.0)  # 1 + 1 + two sweep costs of 0.5
    traded = 100 + 120 + 100 + 90 + 100 + 110 + 50  # strategy fills only, both sides
    avg_eq = float(EQUITY["equity"].mean())
    assert c["turnover"] == pytest.approx(traded / 2 / avg_eq / (5 / 252))
    assert c["return_before_costs"] == pytest.approx((1100 - 1000 + 3) / 1000)
    # Trade costs are 2 of 18 - 10 + 10 + 2 = 20 of P&L before costs.
    assert c["share_of_gross_profit"] == pytest.approx(0.1)
    f = a["funnel"]
    # 4 entries + 2 already_held + 1 max_positions = 7 signals; 1 order missed at the open.
    assert (f["signals"], f["entered"], f["filled"]) == (7, 4, 4)
    assert f["skipped"] == {"already_held": 2, "max_positions": 1}
    assert f["not_filled"] == {"insufficient_cash_at_open": 1}
    assert f["near_misses"] == 1
    kinds = {r["key"]: r for r in a["by_exit"]}
    assert set(kinds) == {"take_profit", "stop_loss", "max_hold"}
    assert kinds["stop_loss"]["win_rate"] == 0 and kinds["take_profit"]["pnl"] == pytest.approx(18)
    assert [r["key"] for r in a["by_symbol"]] == ["XA", "XC", "XB"]  # best P&L first
    assert a["market"]["beta"] is not None and a["benchmark"]["cagr"] is not None
    assert a["returns"]["calmar"] is not None
    assert len(a["trade_list"]) == 3
    assert a["by_year"][0]["year"] == "2024" and a["by_year"][0]["trades"] == 3
    assert a["monthly"][0]["month"] == "2024-01"
    assert a["streaks"]["months"] == 1
    # Every value survives JSON (NaN becomes null) and the headline is flat.
    clean = jsonable(a)
    assert clean["trade_list"][0]["exit_kind"] == "take_profit"
    h = an.headline(a)
    assert h["win_rate"] == pytest.approx(2 / 3) and h["low_sample"] is True


def test_analyze_without_trades_or_benchmark() -> None:
    eq = EQUITY.drop(columns="benchmark_equity")
    a = an.analyze(eq, FILLS.iloc[:0], None, None, None, "1d")
    assert a["trades"] == {"count": 0, "low_sample": True}
    assert a["trade_list"] == [] and a["by_symbol"] == [] and "benchmark" not in a
    assert a["by_regime"] == []
    assert an.headline(a)["win_rate"] is None
