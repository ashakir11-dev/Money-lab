"""Frozen structural exits, context feeds, entry filters, and the unread holdout."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from fixtures_util import bars, day, decisions, run
from moneylab.components.macd import macd
from moneylab.components.macd_slope import macd_slope
from moneylab.components.registry import get_component
from moneylab.data.context_feeds import join_context_feeds, load_context_feeds
from moneylab.data.providers.base import ProviderNotFound
from moneylab.data.snapshot import MarketData
from moneylab.decision.evidence import EvidenceBundle, HoldoutBlocked, observe_candidates
from moneylab.decision.integrity import (
    HoldoutGate,
    example_research_card,
    recovery_swing_partitions,
    unread_holdout_partitions,
)
from moneylab.decision.proposals import propose_from_evidence
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    StrategyConfig,
    resolve,
)
from moneylab.engine.simulator import Account, MarketView, _Position
from moneylab.settings import Settings


def _account(prices: list[tuple[float, float]]) -> tuple[Account, MarketView]:
    config = StrategyConfig(
        name="frozen levels",
        universe=["XA"],
        benchmark="SPY",
        entry=[
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="sma", params={"length": 1}),
                params={"op": "above", "value": 0},
            )
        ],
        exit=ExitRule(kind="holding_period", params={"sessions": 50}),
        portfolio=PortfolioSettings(
            starting_cash=1000,
            target_allocation=1,
            max_positions=1,
            cost_bps_per_side=0,
        ),
    )
    resolved = resolve(config, Settings(_env_file=None))
    frame = bars({"XA": prices})
    data = MarketData.build(frame, ["XA"], day(0), day(len(prices) - 1))
    return Account(resolved), MarketView.build(resolved, data)


def _hold(account: Account, levels: dict[str, float]) -> None:
    account.positions["XA"] = _Position(qty=10, fill_idx=-1, entry_price=12.0, frozen_levels=levels)


def test_open_through_frozen_support_fills_at_that_open() -> None:
    account, view = _account([(12.0, 12.0), (7.0, 7.0)])
    _hold(
        account,
        {"frozen_support": 10.0, "frozen_resistance": 20.0, "frozen_atr": 2.0},
    )
    account.step(view, 0)
    assert account.positions
    account.step(view, 1)
    sells = account.result().fills
    sells = sells[sells["side"] == "sell"]
    assert sells["session"].tolist() == [day(1)]
    assert sells["price"].tolist() == [7.0]
    assert sells["note"].tolist() == ["gapped through frozen support"]
    exit_row = account.result().decisions
    exit_row = exit_row[exit_row["action"] == "exit"].iloc[0]
    assert exit_row["reason"] == "gapped through frozen support"
    assert exit_row["gap_atr"] == pytest.approx((10.0 - 7.0) / 2.0)


def test_close_through_support_sells_at_the_next_open() -> None:
    account, view = _account([(12.0, 9.0), (7.0, 7.0)])
    _hold(
        account,
        {"frozen_support": 10.0, "frozen_resistance": 20.0, "frozen_atr": 2.0},
    )
    account.step(view, 0)
    queued = account.result().decisions
    assert queued[queued["action"] == "exit"]["reason"].tolist() == ["frozen support stop"]
    account.step(view, 1)
    sell = account.result().fills
    sell = sell[sell["side"] == "sell"].iloc[0]
    assert sell["session"] == day(1)
    assert sell["price"] == 7.0
    assert sell["note"] == "gapped through frozen support"


def test_close_through_resistance_is_the_target() -> None:
    account, view = _account([(12.0, 16.0), (18.0, 18.0)])
    _hold(
        account,
        {"frozen_support": 5.0, "frozen_resistance": 15.0, "frozen_atr": 1.0},
    )
    account.step(view, 0)
    exits = account.result().decisions
    assert exits[exits["action"] == "exit"]["reason"].tolist() == ["frozen resistance target"]
    account.step(view, 1)
    sell = account.result().fills
    sell = sell[sell["side"] == "sell"].iloc[0]
    assert sell["price"] == 18.0


def test_relative_volume_is_an_entry_filter() -> None:
    config = StrategyConfig(
        name="relative volume",
        universe=["XA"],
        benchmark="SPY",
        entry=[
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="relative_volume", params={"length": 2}),
                params={"op": "above", "value": 1.5},
            )
        ],
        exit=ExitRule(kind="holding_period", params={"sessions": 10}),
        portfolio=PortfolioSettings(
            starting_cash=1000, target_allocation=1, max_positions=1, cost_bps_per_side=0
        ),
    )
    frame = bars({"XA": [(10.0, 10.0)] * 4})
    frame["volume"] = [10, 10, 40, 40]
    entered = decisions(run(frame, config), "enter")
    assert entered[0][0] == day(2)


def test_macd_slope_matches_the_histogram_difference() -> None:
    frame = pd.DataFrame(
        {
            "close": [float(i) for i in range(1, 40)],
            "open": [float(i) for i in range(1, 40)],
            "high": [float(i) + 0.5 for i in range(1, 40)],
            "low": [float(i) - 0.5 for i in range(1, 40)],
        }
    )
    params = {"fast": 2, "slow": 4, "signal": 2}
    slope = macd_slope(frame, params)
    histogram = macd(frame, {**params, "output": "histogram"})
    finite = slope.dropna()
    assert len(finite) > 0
    index = int(finite.index[-1])
    assert slope.iloc[index] == pytest.approx(histogram.iloc[index] - histogram.iloc[index - 1])
    assert get_component("macd_slope").uses == ("indicator",)


def test_sector_join_uses_the_as_of_map_and_does_not_fill_gaps() -> None:
    sessions = [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-04")]
    stock = pd.DataFrame(
        {
            "date": sessions,
            "symbol": ["NVDA", "NVDA", "NVDA"],
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [1, 1, 1],
        }
    )
    sector_map = pd.DataFrame(
        {
            "as_of": [pd.Timestamp("2020-01-01"), pd.Timestamp("2024-06-01")],
            "symbol": ["NVDA", "NVDA"],
            "sector_etf": ["XLK", "XLC"],
        }
    )
    sector_bars = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-06-03")],
            "symbol": ["XLK", "XLC"],
            "close": [4.0, 6.0],
        }
    )
    vix = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "symbol": ["VIX"], "close": [18.0]})
    joined = join_context_feeds(stock, sector_map=sector_map, sector_bars=sector_bars, vix_bars=vix)
    assert joined["sector_close"].tolist() == pytest.approx([4.0, 6.0, float("nan")], nan_ok=True)
    assert joined["vix_close"].tolist()[0] == 18.0
    assert pd.isna(joined["vix_close"].iloc[1])
    assert pd.isna(joined["vix_close"].iloc[2])


def test_hourly_context_does_not_reuse_a_daily_close() -> None:
    stock = pd.DataFrame(
        {
            "bar_start": [pd.Timestamp("2024-01-02 09:00"), pd.Timestamp("2024-01-02 10:00")],
            "date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-02")],
            "symbol": ["NVDA", "NVDA"],
            "close": [1.0, 1.0],
        }
    )
    sector_map = pd.DataFrame(
        {"as_of": [pd.Timestamp("2020-01-01")], "symbol": ["NVDA"], "sector_etf": ["XLK"]}
    )
    daily = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "symbol": ["XLK"], "close": [4.0]})
    joined = join_context_feeds(stock, sector_map=sector_map, sector_bars=daily)
    assert joined["sector_close"].isna().all()


def test_unknown_vix_symbol_stays_missing() -> None:
    class Provider:
        def fetch_history(
            self, symbols: list[str], start: date, end: date, *, timeframe: str = "1h"
        ) -> SimpleNamespace:
            if symbols == ["VIX"]:
                raise ProviderNotFound("unknown")
            return SimpleNamespace(
                raw=pd.DataFrame(
                    {
                        "date": [pd.Timestamp("2024-01-02")],
                        "symbol": ["XLK"],
                        "close": [4.0],
                    }
                )
            )

    sector_map = pd.DataFrame(
        {"as_of": [pd.Timestamp("2020-01-01")], "symbol": ["NVDA"], "sector_etf": ["XLK"]}
    )
    sector_bars, vix_bars = load_context_feeds(
        Provider(),
        sector_map,
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        timeframe="1d",
        vix_symbol="VIX",
    )
    assert sector_bars is not None and sector_bars["symbol"].tolist() == ["XLK"]
    assert vix_bars is None


def test_unread_holdout_stays_blocked_and_2025_stays_contaminated() -> None:
    parts = unread_holdout_partitions()
    assert parts[2].start == date(2027, 1, 1)
    assert parts[2].contaminated is False
    recovery = {part.name: part for part in recovery_swing_partitions()}
    assert recovery["holdout"].contaminated is True
    assert recovery["holdout"].start.year == 2025
    gate = HoldoutGate.from_partitions(parts)
    calls: list[str] = []
    blocked = gate.request("holdout", "score", loader=lambda: calls.append("loaded"))
    assert blocked["allowed"] is False
    assert blocked["reason"] == "not_frozen"
    assert calls == []
    early = pd.DataFrame(
        {
            "date": [pd.Timestamp("2027-01-04")],
            "symbol": ["NVDA"],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
        }
    )
    with pytest.raises(HoldoutBlocked, match="unread holdout"):
        observe_candidates(early, example_research_card())


def test_relative_volume_bucket_becomes_an_entry_condition() -> None:
    card = example_research_card()
    events = []
    features = []
    outcomes = []
    for i in range(5):
        event_id = f"NVDA|2024-01-0{i + 2}|control_session_v1"
        events.append(
            {
                "event_id": event_id,
                "symbol": "NVDA",
                "session": f"2024-01-0{i + 2}",
                "partition": "discovery",
                "status": "accepted",
                "reason": "accepted",
            }
        )
        features.append(
            {
                "event_id": event_id,
                "feature_id": "relative_volume",
                "value": 0.5,
                "missing_reason": "",
            }
        )
        outcomes.append(
            {
                "event_id": event_id,
                "horizon_bars": 2,
                "censored": False,
                "net_return": 0.01,
            }
        )
    bundle = EvidenceBundle(
        events=events,
        features=features,
        outcomes=outcomes,
        contrast={},
        manifest={},
        block_length=2,
        primary_horizon=2,
        timeframe=card.timeframe,
        minimum_count=5,
        seed=0,
    )
    proposal = propose_from_evidence(card, bundle, mode="exploratory_search", decision_state="PARK")
    assert proposal.authorized
    assert proposal.child is not None
    added = [cond.indicator.id for cond in proposal.child.entry]
    assert "relative_volume" in added
