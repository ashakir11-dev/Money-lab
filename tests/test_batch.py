"""Batches of variants: concurrent equals sequential, and every variant has its own account."""

from __future__ import annotations

from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from moneylab.engine.batch import resolve_workers
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
)
from moneylab.service import Lab

SYMBOLS = [f"T{i:02d}" for i in range(12)]


def _bars() -> pd.DataFrame:
    sessions = pd.DatetimeIndex(
        xcals.get_calendar("XNYS").sessions_in_range("2022-01-03", "2022-12-30")
    ).tz_localize(None)
    rng = np.random.default_rng(7)
    rows = []
    for sym in [*SYMBOLS, "SPY"]:
        close = rng.uniform(20, 200) * np.exp(np.cumsum(rng.normal(0, 0.02, len(sessions))))
        opens = np.concatenate(
            [[close[0]], close[:-1] * (1 + rng.normal(0, 0.004, len(close) - 1))]
        )
        f = pd.DataFrame(
            {
                "date": sessions,
                "symbol": sym,
                "open": opens.round(2),
                "high": (np.maximum(opens, close) * 1.005).round(2),
                "low": (np.minimum(opens, close) * 0.995).round(2),
                "close": close.round(2),
                "volume": 1_000_000,
            }
        )
        if sym == "T03":
            f = f.drop(index=[40, 41, 120])  # missing bars
        rows.append(f)
    return pd.concat(rows, ignore_index=True)


def _cross(length: int, direction: str = "above") -> Condition:
    return Condition(
        kind="price_cross",
        indicator=IndicatorRef(id="sma", params={"length": length}),
        params={"direction": direction},
    )


def _rsi(op: str, value: float) -> Condition:
    return Condition(
        kind="threshold",
        indicator=IndicatorRef(id="rsi", params={"length": 14}),
        params={"op": op, "value": value},
    )


def _variants() -> list[StrategyConfig]:
    def v(name: str, entry: list[Condition], exit: ExitRule, **port: float) -> StrategyConfig:
        return StrategyConfig(
            name=name,
            universe=SYMBOLS,
            timeframe="1d",
            entry=entry,
            exit=exit,
            portfolio=PortfolioSettings(**port),  # type: ignore[arg-type]
        )

    hold = ExitRule(kind="holding_period", params={"sessions": 5})
    sig = ExitRule(kind="signal", conditions=[_cross(10, "below")])
    return [
        v("sma10 hold5", [_cross(10)], hold, max_positions=2, target_allocation=0.4),
        v("sma20 rsi<60", [_cross(20), _rsi("below", 60)], hold, max_positions=3),
        v("sma10 sigexit", [_cross(10)], sig, max_positions=4, starting_cash=50_000),
        v("rsi>55 hold10", [_rsi("above", 55)], ExitRule(params={"sessions": 10}), max_positions=5),
        v(
            "sma5 tight cash",
            [_cross(5)],
            hold,
            max_positions=6,
            target_allocation=0.3,
            starting_cash=20_000,
        ),
    ]


def _assert_same_ledgers(lab: Lab, a: str, b: str) -> None:
    la, lb = lab.store.ledgers(a), lab.store.ledgers(b)
    pd.testing.assert_frame_equal(la.decisions, lb.decisions)
    pd.testing.assert_frame_equal(la.fills, lb.fills)
    pd.testing.assert_frame_equal(la.equity, lb.equity)


@pytest.fixture()
def batch_lab(tmp_path: Path) -> tuple[Lab, str]:
    lab = Lab.open(tmp_path / "home")
    snap = lab.import_frame(_bars(), source="test:batch")
    return lab, snap.id


def test_concurrent_batch_matches_sequential_per_variant(batch_lab: tuple[Lab, str]) -> None:
    lab, sid = batch_lab
    configs = _variants()
    seq = lab.run_batch(configs, sid, workers=1)
    con = lab.run_batch(configs, sid, workers=3)
    assert len(seq) == len(con) == len(configs)
    assert len(set(seq) | set(con)) == 2 * len(configs)  # nothing reused: all new runs
    for a, b, cfg in zip(seq, con, configs, strict=True):
        assert lab.store.get(a)["name"] == lab.store.get(b)["name"] == cfg.name
        _assert_same_ledgers(lab, a, b)
    # The variants really are distinct, so equality above is not trivially true.
    finals = {float(lab.store.ledgers(r).equity["equity"].iloc[-1]) for r in seq}
    assert len(finals) == len(configs)


def test_each_variant_has_its_own_account(batch_lab: tuple[Lab, str]) -> None:
    lab, sid = batch_lab
    configs = _variants()
    batch = lab.run_batch(configs, sid, workers=2, parent_ids=[None] * len(configs))
    for run_id, cfg in zip(batch, configs, strict=True):
        alone = lab.run(cfg, sid)  # the same variant run on its own
        _assert_same_ledgers(lab, run_id, alone)
        led = lab.store.ledgers(run_id)
        start = cfg.portfolio.starting_cash or lab.settings.starting_cash
        assert led.equity["equity"].iloc[0] == start
        # Replaying this variant's own fills from its own starting cash gives its cash.
        cash = start
        for f in led.fills.itertuples():
            cash += (-1 if f.side == "buy" else 1) * f.gross - f.cost
            assert round(cash, 2) == pytest.approx(f.cash_after, abs=0.011)
    # Within an account the symbols compete for the same cash and slots.
    reasons = set()
    for r in batch:
        d = lab.store.ledgers(r).decisions
        reasons |= set(d.loc[d["action"] == "skip", "reason"])
    assert reasons & {"insufficient_cash", "max_positions"}


def test_worker_count() -> None:
    assert resolve_workers(0, 100) >= 1
    assert resolve_workers(8, 3) == 3
    assert resolve_workers(2, 10) == 2
