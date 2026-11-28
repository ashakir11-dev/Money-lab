"""Phase-2 diagnostics and the proposer contract. T08. The V2 card is not run."""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from fixtures_util import bars, day
from moneylab.decision.evidence import (
    HoldoutBlocked,
    RegisteredRunRefused,
    discovery_view,
    execute_registered_card,
    observe_candidates,
    paired_forward_contrast,
    read_evidence_files,
    write_evidence_dir,
)
from moneylab.decision.integrity import (
    DEFERRED,
    SUPPORT_RECOVERY_V2_STATUS,
    EvidenceFloor,
    Partition,
    ResearchCard,
    support_recovery_v2_card,
    validate_card,
)
from moneylab.decision.measure import changed_axes
from moneylab.decision.proposals import (
    GovernanceLog,
    next_research_step,
    propose_from_evidence,
)
from moneylab.engine.config import (
    Condition,
    ExitRule,
    IndicatorRef,
    PortfolioSettings,
    SizingRule,
    StrategyConfig,
    resolve,
)
from moneylab.report_export import report_zip
from moneylab.settings import Settings

_LEVEL = {"lookback": 50, "min_move": 2.0, "zone": 1.0, "atr_length": 14}


def _book(**portfolio: object) -> dict[str, object]:
    settings = {
        "starting_cash": 100_000,
        "max_positions": 1,
        "target_allocation": 1,
        "cost_bps_per_side": 0,
        "cost_model": "legacy_unapplied",
        "cooldown_bars": 2,
        "idle_cash": "cash",
    }
    settings.update(portfolio)
    return {
        "universe": ["XA"],
        "benchmark": "SPY",
        "timeframe": "1d",
        "portfolio": PortfolioSettings(**settings),
        "sizing": SizingRule(id="fixed_dollar", params={"amount": 10_000}),
    }


def _card(**portfolio: object) -> ResearchCard:
    book = _book(**portfolio)
    hold = ExitRule(kind="holding_period", params={"sessions": 2})
    treatment = StrategyConfig(
        name="treat",
        entry=[_threshold("sma", "above", 15, {"length": 1})],
        exit=hold,
        **book,  # type: ignore[arg-type]
    )
    control = StrategyConfig(
        name="control",
        entry=[_threshold("sma", "above", 0, {"length": 1})],
        exit=hold,
        **book,  # type: ignore[arg-type]
    )
    floor = EvidenceFloor(
        delta_pp=0.0,
        min_events=1,
        min_folds=1,
        confidence=0.95,
        block_length=2,
        horizon_bars=2,
        timeframe="1d",
    )
    partitions = (
        Partition("discovery", date(2024, 1, 1), date(2024, 6, 30), "development"),
        Partition("validation", date(2024, 7, 1), date(2024, 12, 31), "development"),
        Partition("holdout", date(2026, 1, 1), date(2026, 12, 31), "holdout"),
    )
    return ResearchCard(
        family="momentum",
        behavior="Close above a level.",
        observable_event="SMA(1) above 0.",
        eligibility="XA, one position, next open.",
        treatment=treatment,
        control=control,
        target_estimand="net forward return from the next open",
        horizon="2 bars",
        timeframe="1d",
        cost_label="legacy_zero_cost",
        falsifier="The contrast is not positive.",
        falsifier_series="active_excess_pp",
        falsifier_op="lte",
        falsifier_level=0.0,
        uncertainty_method="moving_block_bootstrap",
        minimum_evidence=floor,
        trial_budget=2,
        partitions=partitions,
        declared_axis="entry",
    )


def _threshold(indicator: str, op: str, value: float, params: dict[str, object]) -> Condition:
    return Condition(
        kind="threshold",
        indicator=IndicatorRef(id=indicator, params=params),
        params={"op": op, "value": value},
    )


def _prices() -> pd.DataFrame:
    return bars(
        {
            "XA": [
                (10, 10, 9, 10),
                (11, 17, 11, 16),
                (20, 22, 19, 21),
                (21, 23, 20, 22),
                (22, 23, 20, 20),
                (18, 20, 17, 19),
                (19, 20, 18, 18),
            ],
            "SPY": [
                (100, 101, 99, 100),
                (100, 102, 99, 101),
                (100, 103, 99, 102),
                (100, 111, 99, 110),
                (100, 111, 99, 110),
                (100, 111, 99, 110),
                (100, 111, 99, 110),
            ],
        }
    )


def test_t08_rejected_candidates_fills_censoring_and_uncertainty() -> None:
    bundle = observe_candidates(_prices(), _card(), snapshot_id="fixture")
    by_session = {event["session"]: event for event in bundle.events}
    assert [event["reason"] for event in bundle.events] == [
        "treatment_rejected",
        "accepted",
        "position_blocked",
        "position_blocked",
        "cooldown_blocked",
        "cooldown_blocked",
        "no_next_open",
    ]
    rejected = by_session[day(0)]
    assert rejected["signal_values"]["sma(length=1)"] == 10
    vix = _feature(bundle, rejected["event_id"], "vix_level")
    sector = _feature(bundle, rejected["event_id"], "stock_minus_sector")
    assert vix["value"] is None and vix["missing_reason"] == "series_absent"
    assert sector["value"] is None and sector["missing_reason"] == "series_absent"
    assert vix["source_session"] == day(0)
    assert "vix" in bundle.contrast["missing_context"]
    assert "sector" in bundle.contrast["missing_context"]

    short = _outcome(bundle, rejected["event_id"], 2)
    assert short["fill_session"] == day(1)
    assert short["fill_price"] == 11
    assert short["mark_session"] == day(2)
    assert short["censored"] is False
    assert short["net_return"] == pytest.approx(21 / 11 - 1)

    accepted = by_session[day(1)]
    filled = _outcome(bundle, accepted["event_id"], 2)
    assert filled["fill_price"] == 20
    assert filled["fill_session"] == day(2)
    assert filled["mark_session"] == day(3)
    assert filled["net_return"] == pytest.approx(0.1)
    assert filled["mfe"] == pytest.approx(23 / 20 - 1)
    assert filled["mae"] == pytest.approx(19 / 20 - 1)
    assert filled["benchmark_return"] == pytest.approx(0.1)
    assert filled["benchmark_adjusted"] == pytest.approx(0.0)
    assert filled["realized_net"] == pytest.approx(0.1)
    long = _outcome(bundle, accepted["event_id"], 6)
    assert long["censored"] is True
    assert long["net_return"] is None
    assert long["censor_reason"] == "incomplete_horizon"

    tail = _outcome(bundle, by_session[day(6)]["event_id"], 2)
    assert tail["censored"] is True and tail["net_return"] is None

    horizon = bundle.contrast["horizons"]["2"]
    assert horizon["uncertainty"]["method"] == "moving_block_bootstrap"
    assert any(
        "not treated as independent" in item for item in horizon["uncertainty"]["assumptions"]
    )
    assert bundle.contrast["exploratory"] is True
    assert bundle.contrast["promotable"] is False

    quiet = replace(
        _card(),
        control=_card().control.model_copy(
            update={"entry": [_threshold("sma", "above", 1000, {"length": 1})]}
        ),
    )
    assert observe_candidates(_prices(), quiet).events == []


def test_t08_shorter_block_is_an_assumption_violation() -> None:
    card = replace(
        _card(),
        minimum_evidence=EvidenceFloor(
            delta_pp=0.0,
            min_events=1,
            min_folds=1,
            block_length=1,
            horizon_bars=2,
            timeframe="1d",
        ),
    )
    report = observe_candidates(_prices(), card).contrast
    assumptions = report["horizons"]["2"]["uncertainty"]["assumptions"]
    assert any("block shorter than the outcome horizon" in item for item in assumptions)


def test_t08_does_not_score_2025_or_a_holdout_date() -> None:
    viewed = pd.DataFrame(
        [
            {
                "date": "2025-01-02",
                "symbol": "XA",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
            }
        ]
    )
    with pytest.raises(HoldoutBlocked, match="2025"):
        observe_candidates(viewed, _card())
    holdout = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "symbol": "XA",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
            }
        ]
    )
    with pytest.raises(HoldoutBlocked, match="holdout"):
        observe_candidates(holdout, _card())


def test_evidence_export_keeps_labels_off_the_feature_table(tmp_path: Path) -> None:
    bundle = observe_candidates(_prices(), _card(), snapshot_id="fixture")
    write_evidence_dir(tmp_path / "evidence", bundle)
    with pytest.raises(FileExistsError):
        write_evidence_dir(tmp_path / "evidence", bundle)
    stored = read_evidence_files(tmp_path / "evidence")
    assert stored is not None
    assert "net_return" not in stored["features.csv"]
    assert "net_return" in stored["outcomes.csv"]
    drill = json.loads(stored["drilldown.json"])
    assert drill[0]["features"]
    assert "net_return" not in drill[0]["features"][0]
    assert "net_return" in drill[0]["outcomes"][0]
    payload = report_zip({"id": "r"}, [], stored)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
    assert names[:2] == ["summary.json", "trades.csv"]
    assert "evidence/drilldown.json" in names
    assert "evidence/features.csv" in names


def test_support_recovery_v2_is_registered_and_not_run() -> None:
    card = support_recovery_v2_card()
    assert SUPPORT_RECOVERY_V2_STATUS == "registered_not_run"
    assert "V2 support-recovery run" in DEFERRED
    assert validate_card(card).ok, validate_card(card).reasons
    assert card.timeframe == "1h"
    assert card.treatment.portfolio.max_positions == 1
    assert card.treatment.portfolio.cost_bps_per_side == 5
    assert card.treatment.portfolio.cost_model == "research_bps"
    assert card.treatment.sizing is not None
    assert card.treatment.sizing.params["amount"] == 10_000
    control_entry = [
        (cond.indicator.id, cond.params["op"], cond.params["value"]) for cond in card.control.entry
    ]
    assert control_entry == [("roc", "above", 0)]
    treatment = [
        (cond.indicator.id, cond.params["op"], cond.params["value"])
        for cond in card.treatment.entry
    ]
    assert treatment == [
        ("roc", "above", 0),
        ("support_distance", "above", 0),
        ("support_distance", "below", 1),
    ]
    assert card.treatment.entry[1].indicator.params == _LEVEL
    assert card.minimum_evidence.horizon_bars == 65
    assert card.minimum_evidence.block_length == 65
    for part in card.partitions:
        assert part.start.year != 2025 and part.end.year != 2025
    assert card.partitions[2].role == "holdout"
    with pytest.raises(RegisteredRunRefused, match="not run"):
        execute_registered_card(card)
    with pytest.raises(RegisteredRunRefused, match="not run"):
        observe_candidates(_prices(), card)


def test_proposer_is_discovery_only_one_axis_and_cannot_override_a_kill() -> None:
    card = _proposal_card()
    bundle = _proposal_bundle(card)
    log = GovernanceLog()
    proposal = propose_from_evidence(
        card,
        bundle,
        mode="exploratory_search",
        decision_state="PARK",
        log=log,
    )
    assert proposal.labeled == "fallback"
    assert proposal.authorized
    assert proposal.declared_axis == "entry"
    assert proposal.child is not None
    assert "holdout-secret" not in proposal.evidence_refs
    assert "50" not in proposal.observed_pattern
    assert "50" not in json.dumps(proposal.provenance)
    axes = changed_axes(
        resolve(card.treatment, Settings(_env_file=None)),
        resolve(proposal.child, Settings(_env_file=None)),
    )
    assert axes == ["entry"]
    assert log.spent == 1

    killed = propose_from_evidence(
        card, bundle, mode="family_budget", decision_state="KILL", log=log
    )
    assert killed.child is None
    assert killed.reasons == ("kill_immutable",)
    assert log.spent == 1

    closed = propose_from_evidence(
        card, bundle, mode="market_behavior", decision_state="PARK", require_model=True
    )
    assert closed.reasons == ("model_required",)
    assert closed.child is None

    def complete(prompt: str) -> str:
        assert "holdout-secret" not in prompt
        return json.dumps(
            {
                "bucket": "support_distance:0_1",
                "observed_pattern": "near support",
                "alternative_explanation": "mix",
                "predicted_effect": "fewer entries",
                "falsifier": "the contrast fails",
                "required_sample": 30,
                "declared_axis": "entry",
                "verdict": "kill",
            }
        )

    overreach = propose_from_evidence(
        card, bundle, mode="exploratory_search", decision_state="PARK", complete=complete
    )
    assert overreach.reasons == ("proposer_overreach",)
    assert overreach.child is None
    assert overreach.labeled == "model"

    empty = discovery_view(bundle)
    empty.events = []
    empty.features = []
    empty.outcomes = []
    empty.contrast = {"buckets": []}
    parked = next_research_step(card, empty, "PARK")
    assert parked == "observe"
    assert next_research_step(card, None, "KILL") == "stop"


def _feature(bundle: object, event_id: str, feature_id: str) -> dict[str, object]:
    rows = bundle.features  # type: ignore[attr-defined]
    return next(
        row for row in rows if row["event_id"] == event_id and row["feature_id"] == feature_id
    )


def _outcome(bundle: object, event_id: str, horizon: int) -> dict[str, object]:
    rows = bundle.outcomes  # type: ignore[attr-defined]
    return next(
        row for row in rows if row["event_id"] == event_id and row["horizon_bars"] == horizon
    )


def _proposal_card() -> ResearchCard:
    card = _card()
    return replace(
        card,
        family="momentum",
        treatment=card.treatment.model_copy(
            update={"entry": [_threshold("sma", "above", 0, {"length": 1})]}
        ),
        control=card.control.model_copy(
            update={"entry": [_threshold("roc", "above", 0, {"length": 1})]}
        ),
    )


def _proposal_bundle(card: ResearchCard):
    from moneylab.decision.evidence import EvidenceBundle

    events = []
    features = []
    outcomes = []
    for index in range(5):
        event_id = f"disc-{index}"
        events.append(
            {
                "event_id": event_id,
                "partition": "discovery",
                "status": "accepted",
                "reason": "accepted",
                "session": f"2024-01-0{index + 1}",
            }
        )
        features.append(
            {
                "event_id": event_id,
                "feature_id": "support_distance",
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
    events.append(
        {
            "event_id": "holdout-secret",
            "partition": "holdout",
            "status": "accepted",
            "reason": "accepted",
            "session": "2026-01-02",
        }
    )
    features.append(
        {
            "event_id": "holdout-secret",
            "feature_id": "support_distance",
            "value": 0.5,
            "missing_reason": "",
        }
    )
    outcomes.append(
        {
            "event_id": "holdout-secret",
            "horizon_bars": 2,
            "censored": False,
            "net_return": 50,
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
    bundle.contrast = {}
    from moneylab.decision.evidence import diagnostic_report

    bundle.contrast = diagnostic_report(bundle)
    return bundle


def test_development_run_rejects_a_date_outside_the_declared_window() -> None:
    card = support_recovery_v2_card()
    early = pd.DataFrame(
        {
            "date": [pd.Timestamp("2019-06-03")],
            "symbol": ["NVDA"],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
        }
    )
    with pytest.raises(HoldoutBlocked, match="outside"):
        observe_candidates(early, card, development_only=True)


def test_hourly_card_does_not_score_bars_that_have_no_bar_start() -> None:
    with pytest.raises(ValueError, match="bar_start"):
        observe_candidates(_prices(), support_recovery_v2_card(), development_only=True)


def test_paired_contrast_is_the_band_mean_minus_the_control_mean() -> None:
    events = [
        _event("2020-01-02", "accepted"),
        _event("2020-06-01", "treatment_rejected"),
        _event("2021-01-04", "position_blocked"),
        _event("2021-06-01", "treatment_rejected"),
    ]
    outcomes = [
        _net_row(str(events[0]["event_id"]), 0.10),
        _net_row(str(events[1]["event_id"]), 0.00),
        _net_row(str(events[2]["event_id"]), 0.20),
        _net_row(str(events[3]["event_id"]), 0.00),
    ]
    contrast = paired_forward_contrast(
        events,
        outcomes,
        horizon=65,
        block_length=2,
        partition="discovery",
        seed=0,
        draws=20,
    )
    assert contrast["n_treatment"] == 2
    assert contrast["n_control"] == 4
    assert contrast["folds"] == 2
    assert contrast["fold_years"] == [2020, 2021]
    assert contrast["point"] == pytest.approx(0.075)
    assert contrast["complement_point"] == pytest.approx(0.15)
    assert contrast["effective_sample"] == pytest.approx(2.0)
    assert contrast["low"] is not None and contrast["high"] is not None
    assert contrast["low"] <= contrast["high"]


def _event(session: str, reason: str) -> dict[str, object]:
    return {
        "event_id": f"NVDA|{session}|control_session_v1",
        "symbol": "NVDA",
        "session": session,
        "partition": "discovery",
        "reason": reason,
    }


def _net_row(event_id: str, net: float) -> dict[str, object]:
    return {
        "event_id": event_id,
        "horizon_bars": 65,
        "censored": False,
        "net_return": net,
    }
