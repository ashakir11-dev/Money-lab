"""Candidate events, point-in-time features, forward labels, and the report drill-down.

A candidate is one symbol and one session where every control entry condition passes
on a finite close. Bars that fail the control are not missed trades. Consecutive
passing sessions stay separate, because their forward windows overlap; the block
bootstrap keeps neighbors together instead of treating those windows as independent.
The identity is symbol, session, and ``control_session_v1``.

The signal is the completed close. The fill price is the next session's open.
Forward returns are measured from that open to the close of the h-th later session
that has a close. Horizons are 6, 13, 26, 39, and 65 bars, plus the card horizon
when it is not already in that list. A horizon that does not finish is censored:
the return is missing, not a shorter window. Feature rows do not carry those labels.

Sector and VIX stay missing unless the bars already include ``sector_close`` or
``vix_close``. Nothing is invented or carried forward across a gap. 2025 is refused
before any label is computed, because that year was already viewed. A holdout or
contaminated partition is refused the same way. Bars are aligned on the card's
timeframe, so an hourly card is not scored on a daily calendar.

Support Recovery V2 stays refused unless the caller sets ``development_only``.
That path still rejects 2025, 2026, and any bar outside a development partition.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import moneylab.components  # noqa: F401  (registers indicators)
from moneylab import ENGINE_VERSION
from moneylab.components.context import ContextDataAbsent
from moneylab.components.registry import get_component, indicator_key, list_components
from moneylab.data.bars import session_label
from moneylab.data.snapshot import MarketData
from moneylab.decision.integrity import (
    Partition,
    ResearchCard,
    moving_block_interval,
    support_recovery_v2_card,
    unread_holdout_partitions,
)
from moneylab.engine.config import config_hash, resolve
from moneylab.engine.simulator import _evaluate
from moneylab.settings import Settings

FEATURE_SET_VERSION = "1.0.0"
DEFINITION_ID = "control_session_v1"
DENOMINATOR = (
    "One candidate per symbol and session where every control entry condition passes "
    "on a finite close. A bar that fails the control is not a candidate. Overlapping "
    "sessions are kept and are not treated as independent draws."
)
HORIZONS = (6, 13, 26, 39, 65)
VIEWED_YEAR = 2025
EVIDENCE_FILES = (
    "candidates.csv",
    "contrast.json",
    "drilldown.json",
    "features.csv",
    "manifest.json",
    "outcomes.csv",
)
UNRESOLVED_GAPS = (
    "No historical sector-classification source is shipped. The join uses a supplied as-of map.",
    "VIX is not on the Equibles stock route. An unknown VIX symbol stays missing.",
    "Forward labels count sessions with a close. holding_period counts aligned sessions.",
    "Same-session events share a block. That is not a cluster-robust estimator.",
    "The equity ledger has no per-asset weights. Same-stock matching needs caller weights.",
    "Support Recovery V2 is not frozen. Its 2026 holdout slot is unchanged.",
    "The unread holdout is 2027. It is refused here and is not run.",
    "2025 was viewed and is refused.",
)
_UNITS = {
    "rsi": "index",
    "roc": "fraction",
    "macd": "price",
    "sma": "price",
    "ema": "price",
    "adx": "index",
    "linreg_trend": "percent_per_year",
    "market_structure": "sign",
    "support": "price",
    "resistance": "price",
    "support_tests": "count",
    "resistance_tests": "count",
    "support_distance": "atr",
    "resistance_distance": "atr",
    "atr": "price",
    "bollinger_pctb": "fraction",
    "avg_volume": "shares",
    "relative_volume": "ratio",
    "macd_slope": "price",
    "stock_minus_sector": "return",
    "sector_minus_spy": "return",
    "stock_minus_spy": "return",
    "vix_level": "index",
    "vix_change_1": "fraction",
    "vix_change_5": "fraction",
    "vix_percentile_252": "fraction",
    "breadth": "fraction",
}
_LEVEL = {"lookback": 50, "min_move": 2.0, "zone": 1.0, "atr_length": 14}
_CONTEXT_COLUMNS = {
    "stock_minus_sector": "sector_close",
    "sector_minus_spy": "sector_close",
    "vix_level": "vix_close",
    "vix_change_1": "vix_close",
    "vix_change_5": "vix_close",
    "vix_percentile_252": "vix_close",
}


class HoldoutBlocked(ValueError):
    """The request asked for a viewed year, a holdout, or a contaminated window."""


class RegisteredRunRefused(ValueError):
    """A registered card was asked to run. This phase does not load its bars."""


@dataclass
class EvidenceBundle:
    """Labels live on ``outcomes``. ``features`` are the point-in-time values only."""

    events: list[dict[str, Any]]
    features: list[dict[str, Any]]
    outcomes: list[dict[str, Any]]
    contrast: dict[str, Any]
    manifest: dict[str, Any]
    block_length: int
    primary_horizon: int
    timeframe: str
    minimum_count: int = 5
    seed: int = 0
    partitions: tuple[Partition, ...] = ()
    files: dict[str, str] = field(default_factory=dict)


def feature_cache_key(snapshot_id: str, timeframe: str, spec: dict[str, Any]) -> str:
    """Shared computations are keyed by snapshot, bar size, and parameter signature."""
    payload = json.dumps({"id": spec["id"], "params": spec["params"]}, sort_keys=True)
    return f"{snapshot_id}|{timeframe}|{payload}"


def feature_specs() -> list[dict[str, Any]]:
    """Every registered indicator at its default parameters."""
    specs: list[dict[str, Any]] = []
    for decl in list_components("indicator"):
        if decl.compute is None and decl.compute_universe is None:
            continue
        params = decl.resolve_params({})
        specs.append(
            {
                "id": decl.id,
                "params": params,
                "version": decl.version,
                "unit": _UNITS.get(decl.id, "native"),
                "derived": False,
            }
        )
    return specs


def execute_registered_card(card: ResearchCard) -> None:
    """Refuse every execution request. V2 is named so a caller can see it was registered."""
    if _is_support_recovery_v2(card):
        raise RegisteredRunRefused(
            "Support Recovery V2 is registered and is not run. No window is loaded."
        )
    raise RegisteredRunRefused("This phase does not execute a research card.")


def observe_candidates(
    bars: pd.DataFrame,
    card: ResearchCard,
    *,
    snapshot_id: str = "unspecified",
    minimum_count: int = 5,
    seed: int = 0,
    development_only: bool = False,
) -> EvidenceBundle:
    """Build candidates from the control, then label them. Holdout dates raise first.

    ``development_only`` is the only way to observe Support Recovery V2. It still
    refuses 2025, a holdout partition, and any bar outside a development partition.
    """
    if _is_support_recovery_v2(card) and not development_only:
        raise RegisteredRunRefused(
            "Support Recovery V2 is registered and is not run. No window is loaded."
        )
    _refuse_blocked_dates(bars, card.partitions)
    if development_only:
        _refuse_outside_development(bars, card.partitions)
    if bars.empty:
        raise ValueError("candidate observation needs bars")
    settings = Settings(_env_file=None)
    treatment = resolve(card.treatment, settings)
    control = resolve(card.control, settings)
    universe = list(treatment["universe"])
    benchmark = str(treatment["benchmark"])
    frames, sessions = _align(bars, universe, benchmark, card.timeframe)
    availability = _attach_context(bars, frames, universe, benchmark)
    specs = feature_specs()
    arrays = _feature_arrays(frames, universe, specs, treatment["entry"], control["entry"])
    labels = [session_label(ts) for ts in sessions]
    trade_start = str((treatment.get("execution") or {}).get("trade_start") or "")
    cost_rate = _cost_rate(treatment)
    horizon = int(card.minimum_evidence.horizon_bars)
    horizons = tuple(dict.fromkeys((*HORIZONS, horizon)))
    cooldown = int((treatment.get("portfolio") or {}).get("cooldown_bars") or 0)
    max_positions = int(treatment["portfolio"]["max_positions"])
    events, books = _walk(
        labels=labels,
        sessions=sessions,
        frames=frames,
        universe=universe,
        arrays=arrays,
        control=control["entry"],
        treatment_conds=treatment["entry"],
        trade_start=trade_start[:10],
        cooldown=cooldown,
        max_positions=max_positions,
        horizon=horizon,
        snapshot_id=snapshot_id,
        config_hash_value=config_hash(treatment),
        availability=availability,
        partitions=card.partitions,
    )
    features = _feature_rows(events, arrays, specs, labels)
    outcomes = _outcome_rows(
        events,
        books,
        frames,
        benchmark if benchmark in frames else "",
        horizons,
        horizon,
        cost_rate,
        labels,
    )
    bundle = EvidenceBundle(
        events=events,
        features=features,
        outcomes=outcomes,
        contrast={},
        manifest={},
        block_length=int(card.minimum_evidence.block_length),
        primary_horizon=horizon,
        timeframe=card.timeframe,
        minimum_count=minimum_count,
        seed=seed,
        partitions=card.partitions,
    )
    bundle.contrast = diagnostic_report(bundle)
    bundle.manifest = _manifest(
        bundle,
        snapshot_id=snapshot_id,
        config_hash_value=config_hash(treatment),
        availability=availability,
        specs=specs,
        timeframe=card.timeframe,
    )
    bundle.files = evidence_files(bundle)
    return bundle


def diagnostic_report(bundle: EvidenceBundle) -> dict[str, Any]:
    """Acceptance, exploratory buckets, and a block-bootstrap interval where it exists."""
    by_reason: dict[str, int] = {}
    accepted = 0
    for event in bundle.events:
        by_reason[event["reason"]] = by_reason.get(event["reason"], 0) + 1
        if event["status"] == "accepted":
            accepted += 1
    feature_index = _feature_index(bundle.features)
    outcome_index = _outcomes_for(bundle.outcomes, bundle.primary_horizon)
    horizons: dict[str, Any] = {}
    assumptions = [
        "forward windows overlap and are not treated as independent observations",
        "events are ordered by session then symbol",
        "a censored horizon contributes no return",
    ]
    for horizon in sorted({row["horizon_bars"] for row in bundle.outcomes}):
        rows = [row for row in bundle.outcomes if row["horizon_bars"] == horizon]
        summary, extra = _horizon_summary(
            rows,
            block_length=bundle.block_length,
            horizon_bars=int(horizon),
            timeframe=bundle.timeframe,
            seed=bundle.seed,
        )
        horizons[str(horizon)] = summary
        assumptions.extend(item for item in extra if item not in assumptions)
    buckets = _buckets(
        bundle.events,
        feature_index,
        outcome_index,
        minimum_count=bundle.minimum_count,
    )
    missing = sorted(
        {row["feature_id"] for row in bundle.features if row["missing_reason"] == "series_absent"}
    )
    context = [name for name in ("sector", "vix", "benchmark") if _context_missing(bundle, name)]
    return {
        "exploratory": True,
        "promotable": False,
        "denominator": DENOMINATOR,
        "primary_horizon": bundle.primary_horizon,
        "acceptance": {
            "candidates": len(bundle.events),
            "accepted": accepted,
            "rejected": len(bundle.events) - accepted,
            "reasons": by_reason,
        },
        "horizons": horizons,
        "buckets": buckets,
        "missing_features": missing,
        "missing_context": context,
        "assumptions": assumptions,
        "benchmark_definition": (
            "benchmark open on the fill session to the benchmark close on the mark "
            "session; missing when either price is missing, with no forward fill"
        ),
    }


def paired_forward_contrast(
    events: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    *,
    horizon: int,
    block_length: int,
    partition: str,
    seed: int = 0,
    draws: int = 400,
    confidence: float = 0.95,
    timeframe: str = "1h",
) -> dict[str, Any]:
    """Support-band mean minus the mean of every control event, at one horizon.

    A control event is a candidate. The support band is a candidate whose reason
    is not ``treatment_rejected``. The contrast is that band's mean net return
    minus the mean net return of every complete control event in ``partition``.
    A calendar year is a fold when it contains a complete band outcome. The
    interval is the circular moving-block bootstrap used by
    ``moving_block_interval``, applied to the event list in session order. A
    draw with no band event is omitted. ``effective_sample`` is the number of
    complete control events divided by the block length.
    """
    if block_length < 1 or draws < 1 or not 0 < confidence < 1:
        raise ValueError("uncertainty settings are out of range")
    by_event = {row["event_id"]: row for row in outcomes if row["horizon_bars"] == horizon}
    rows: list[dict[str, Any]] = []
    censored = 0
    for event in events:
        if event.get("partition") != partition:
            continue
        outcome = by_event.get(event["event_id"])
        if outcome is None or outcome.get("censored") or outcome.get("net_return") is None:
            censored += 1
            continue
        rows.append(
            {
                "session": str(event["session"]),
                "symbol": str(event["symbol"]),
                "net": float(outcome["net_return"]),
                "band": event.get("reason") != "treatment_rejected",
            }
        )
    rows.sort(key=lambda row: (row["session"], row["symbol"]))
    band_rows = [row for row in rows if row["band"]]
    complement = [row for row in rows if not row["band"]]
    years = sorted({int(row["session"][:4]) for row in band_rows})
    point = _mean_of([row["net"] for row in band_rows])
    control_mean = _mean_of([row["net"] for row in rows])
    contrast = None if point is None or control_mean is None else point - control_mean
    outside = _mean_of([row["net"] for row in complement])
    complement_point = None if point is None or outside is None else point - outside
    assumptions = [
        "contrast is the support-band mean minus the mean of every control event",
        "a fold is a calendar year with a complete support-band outcome",
        "circular moving-block bootstrap of that contrast",
        f"block length {block_length}; observations inside a block stay together",
        "resampled blocks are not treated as independent bars",
        f"{confidence:.0%} percentile interval",
        f"timeframe {timeframe}",
        "events are ordered by session then symbol",
        "a bootstrap draw with no support-band event is omitted",
        "same-session events share a block; this is not a cluster-robust estimator",
    ]
    if horizon > 1 and block_length < horizon:
        assumptions.append("assumption_violated: block shorter than the outcome horizon")
    low, high, used = _block_contrast(rows, block_length, seed, draws, confidence)
    if used < 2:
        assumptions.append("fewer than two bootstrap draws contained a support-band event")
        low, high = None, None
    n_control = len(rows)
    return {
        "point": contrast,
        "low": low,
        "high": high,
        "band_mean": point,
        "control_mean": control_mean,
        "complement_point": complement_point,
        "n_control": n_control,
        "n_treatment": len(band_rows),
        "n_complement": len(complement),
        "n_censored": censored,
        "folds": len(years),
        "fold_years": years,
        "effective_sample": None if n_control == 0 else n_control / block_length,
        "assumptions": assumptions,
        "method": "moving_block_bootstrap",
        "draws_used": used,
        "seed": seed,
        "block_length": block_length,
        "horizon": horizon,
        "partition": partition,
    }


def _mean_of(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.mean(np.asarray(values, dtype=float)))


def _block_contrast(
    rows: list[dict[str, Any]],
    block_length: int,
    seed: int,
    draws: int,
    confidence: float,
) -> tuple[float | None, float | None, int]:
    n = len(rows)
    if n < 2 or not any(row["band"] for row in rows):
        return None, None, 0
    rng = np.random.Generator(np.random.PCG64(seed))
    n_blocks = math.ceil(n / block_length)
    samples: list[float] = []
    for _ in range(draws):
        starts = rng.integers(0, n, size=n_blocks)
        picked: list[dict[str, Any]] = []
        for start in starts:
            start_i = int(start)
            stop = start_i + block_length
            if stop <= n:
                picked.extend(rows[start_i:stop])
            else:
                picked.extend(rows[start_i:])
                picked.extend(rows[: stop - n])
        picked = picked[:n]
        band = [float(row["net"]) for row in picked if row["band"]]
        if not band:
            continue
        samples.append(float(np.mean(band) - np.mean([float(row["net"]) for row in picked])))
    if len(samples) < 2:
        return None, None, len(samples)
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(np.asarray(samples, dtype=float), [alpha, 1.0 - alpha])
    return float(low), float(high), len(samples)


def drilldown(bundle: EvidenceBundle, event_id: str) -> dict[str, Any]:
    """One event: the decision, the features, and the labels in separate objects."""
    event = next(item for item in bundle.events if item["event_id"] == event_id)
    features = [row for row in bundle.features if row["event_id"] == event_id]
    outcomes = [row for row in bundle.outcomes if row["event_id"] == event_id]
    return {
        "event": {key: value for key, value in event.items() if key != "signal_values"},
        "signal_values": event.get("signal_values", {}),
        "features": features,
        "outcomes": outcomes,
        "missing_features": [row["feature_id"] for row in features if row["value"] is None],
        "reason": event["reason"],
    }


def evidence_files(bundle: EvidenceBundle) -> dict[str, str]:
    """The machine-readable drill-down a finished report can put in its export."""
    candidates = _csv(
        bundle.events,
        (
            "event_id",
            "symbol",
            "session",
            "snapshot_id",
            "config_hash",
            "engine_version",
            "feature_set",
            "definition",
            "status",
            "reason",
            "detail",
            "execution_eligible",
            "partition",
            "sector",
            "vix",
            "benchmark",
        ),
    )
    features = _csv(
        bundle.features,
        (
            "event_id",
            "feature_id",
            "params",
            "unit",
            "value",
            "missing_reason",
            "source_session",
            "component_version",
            "feature_set",
        ),
    )
    outcomes = _csv(
        bundle.outcomes,
        (
            "event_id",
            "horizon_bars",
            "fill_session",
            "fill_price",
            "mark_session",
            "censored",
            "censor_reason",
            "gross_return",
            "net_return",
            "benchmark_return",
            "benchmark_adjusted",
            "mfe",
            "mae",
            "realized_net",
        ),
    )
    contrast = json.dumps(bundle.contrast, indent=2, sort_keys=True) + "\n"
    details = [drilldown(bundle, event["event_id"]) for event in bundle.events]
    drill = json.dumps(details, indent=2, sort_keys=True) + "\n"
    manifest = dict(bundle.manifest)
    manifest["hashes"] = {
        "candidates": _sha(candidates),
        "features": _sha(features),
        "outcomes": _sha(outcomes),
    }
    manifest["labels_separated"] = True
    return {
        "candidates.csv": candidates,
        "features.csv": features,
        "outcomes.csv": outcomes,
        "contrast.json": contrast,
        "drilldown.json": drill,
        "manifest.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    }


def write_evidence_dir(dest: Path, bundle: EvidenceBundle) -> None:
    """Write the drill-down once. A second write of the same folder is refused."""
    if (dest / "manifest.json").exists():
        raise FileExistsError("evidence manifest is immutable")
    files = bundle.files or evidence_files(bundle)
    dest.mkdir(parents=True, exist_ok=True)
    for name in EVIDENCE_FILES:
        if name == "manifest.json":
            continue
        (dest / name).write_text(files[name])
    (dest / "manifest.json").write_text(files["manifest.json"])


def read_evidence_files(dest: Path) -> dict[str, str] | None:
    """The stored drill-down, or None when this run has no evidence folder."""
    if not (dest / "manifest.json").exists():
        return None
    return {name: (dest / name).read_text() for name in EVIDENCE_FILES}


def discovery_view(bundle: EvidenceBundle, *, validation_permitted: bool = False) -> EvidenceBundle:
    """Drop holdout and, until validation is permitted, validation rows."""
    allowed = {"discovery"}
    if validation_permitted:
        allowed.add("validation")
    events = [event for event in bundle.events if event.get("partition") in allowed]
    ids = {event["event_id"] for event in events}
    viewed = EvidenceBundle(
        events=events,
        features=[row for row in bundle.features if row["event_id"] in ids],
        outcomes=[row for row in bundle.outcomes if row["event_id"] in ids],
        contrast={},
        manifest=dict(bundle.manifest),
        block_length=bundle.block_length,
        primary_horizon=bundle.primary_horizon,
        timeframe=bundle.timeframe,
        minimum_count=bundle.minimum_count,
        seed=bundle.seed,
        partitions=bundle.partitions,
    )
    viewed.contrast = diagnostic_report(viewed)
    return viewed


def _is_support_recovery_v2(card: ResearchCard) -> bool:
    registered = support_recovery_v2_card()
    settings = Settings(_env_file=None)
    return config_hash(resolve(card.treatment, settings)) == config_hash(
        resolve(registered.treatment, settings)
    ) and config_hash(resolve(card.control, settings)) == config_hash(
        resolve(registered.control, settings)
    )


def _refuse_blocked_dates(bars: pd.DataFrame, partitions: tuple[Partition, ...]) -> None:
    if "date" not in bars.columns:
        raise ValueError("bars need a date column")
    unread = next(part for part in unread_holdout_partitions() if part.role == "holdout")
    for dates in _clocks(bars):
        if (dates.dt.year == VIEWED_YEAR).any():
            raise HoldoutBlocked("2025 was viewed and is not scored")
        for ts in dates:
            day = pd.Timestamp(ts).date()
            if unread.start <= day <= unread.end:
                raise HoldoutBlocked("unread holdout")
            part = _covering(pd.Timestamp(ts).date(), partitions)
            if part is not None and (part.role == "holdout" or part.contaminated):
                raise HoldoutBlocked(part.name)


def _refuse_outside_development(bars: pd.DataFrame, partitions: tuple[Partition, ...]) -> None:
    windows = [part for part in partitions if part.role == "development" and not part.contaminated]
    if not windows:
        raise HoldoutBlocked("development run has no development partition")
    for dates in _clocks(bars):
        for ts in dates:
            if pd.isna(ts):
                raise HoldoutBlocked("development run has a bar with no timestamp")
            day = pd.Timestamp(ts).date()
            if day.year >= VIEWED_YEAR:
                raise HoldoutBlocked(f"{day.year} is not a development year")
            if not any(part.start <= day <= part.end for part in windows):
                raise HoldoutBlocked(f"{day.isoformat()} is outside the development partitions")


def _clocks(bars: pd.DataFrame) -> list[pd.Series]:
    clocks = [pd.to_datetime(bars["date"])]
    if "bar_start" in bars.columns:
        clocks.append(pd.to_datetime(bars["bar_start"]))
    return clocks


def _covering(day: date, partitions: tuple[Partition, ...]) -> Partition | None:
    for part in partitions:
        if part.start <= day <= part.end:
            return part
    return None


def _align(
    bars: pd.DataFrame, universe: list[str], benchmark: str, timeframe: str
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    symbols = list(universe)
    if benchmark not in symbols and (bars["symbol"].astype(str).str.upper() == benchmark).any():
        symbols.append(benchmark)
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    data = MarketData.build(frame, symbols, timeframe=timeframe)
    return dict(data.frames), data.sessions


def _attach_context(
    bars: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    universe: list[str],
    benchmark: str,
) -> dict[str, str]:
    availability = {"sector": "absent", "vix": "absent", "benchmark": "absent"}
    raw = bars.copy()
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    raw["symbol"] = raw["symbol"].astype(str).str.upper()
    if benchmark in frames:
        availability["benchmark"] = "present"
        bench_close = frames[benchmark]["close"]
        for symbol in universe:
            if symbol == benchmark:
                continue
            frames[symbol] = frames[symbol].copy()
            frames[symbol]["benchmark_close"] = bench_close
    if "sector_close" in raw.columns:
        saw = False
        for symbol in universe:
            part = raw[raw["symbol"] == symbol]
            projected = _project_context(part, "sector_close", frames[symbol].index)
            frames[symbol] = frames[symbol].copy()
            frames[symbol]["sector_close"] = projected
            saw = saw or bool(projected.notna().any())
        if saw:
            availability["sector"] = "present"
    if "vix_close" in raw.columns:
        projected = _project_context(raw, "vix_close", frames[universe[0]].index)
        if bool(projected.notna().any()):
            availability["vix"] = "present"
        for symbol in universe:
            frames[symbol] = frames[symbol].copy()
            frames[symbol]["vix_close"] = projected.reindex(frames[symbol].index)
    return availability


def _project_context(raw: pd.DataFrame, column: str, index: pd.Index) -> pd.Series:
    """Align a supplied close onto ``index`` without filling a missing session."""
    if raw.empty:
        return pd.Series(np.nan, index=index, dtype=float)
    table = raw.copy()
    stamps = pd.to_datetime(index)
    intraday = bool(((stamps.hour != 0) | (stamps.minute != 0)).any())
    if intraday and "bar_start" in table.columns:
        table["_key"] = pd.to_datetime(table["bar_start"])
    else:
        table["_key"] = pd.to_datetime(table["date"]).dt.normalize()
    values = table.drop_duplicates("_key").set_index("_key")[column]
    return values.reindex(index).astype(float)


def _feature_arrays(
    frames: dict[str, pd.DataFrame],
    universe: list[str],
    specs: list[dict[str, Any]],
    treatment: list[dict[str, Any]],
    control: list[dict[str, Any]],
) -> dict[str, dict[str, np.ndarray]]:
    universe_frames = {symbol: frames[symbol] for symbol in universe}
    shared: dict[str, np.ndarray] = {}
    absent: set[str] = set()
    for spec in specs:
        if spec["derived"]:
            continue
        decl = get_component(spec["id"])
        key = indicator_key({"id": spec["id"], "params": spec["params"]})
        if decl.compute_universe is not None:
            try:
                shared[key] = decl.compute_universe(universe_frames, spec["params"]).to_numpy(
                    dtype=float
                )
            except ContextDataAbsent:
                absent.add(key)
    out: dict[str, dict[str, np.ndarray]] = {}
    refs = [*specs, *[cond["indicator"] for cond in treatment + control]]
    for symbol in universe:
        out[symbol] = {}
        frame = frames[symbol]
        for spec in refs:
            params = spec["params"]
            feature_id = spec["id"]
            key = indicator_key({"id": feature_id, "params": params})
            if key in out[symbol]:
                continue
            if key in absent or _context_column_missing(frame, feature_id):
                out[symbol][key] = np.full(len(frame), np.nan)
                absent.add(key)
                continue
            if key in shared:
                out[symbol][key] = shared[key]
                continue
            decl = get_component(feature_id)
            if decl.compute is None:
                out[symbol][key] = np.full(len(frame), np.nan)
                continue
            try:
                out[symbol][key] = decl.compute(frame, params).to_numpy(dtype=float)
            except ContextDataAbsent:
                out[symbol][key] = np.full(len(frame), np.nan)
                absent.add(key)
    out["__absent__"] = {key: np.array([1.0]) for key in absent}
    return out


def _context_column_missing(frame: pd.DataFrame, feature_id: str) -> bool:
    if feature_id == "sector_minus_spy":
        return "sector_close" not in frame.columns or "benchmark_close" not in frame.columns
    column = _CONTEXT_COLUMNS.get(feature_id)
    if column is None:
        return False
    if feature_id == "stock_minus_spy":
        return "benchmark_close" not in frame.columns
    return column not in frame.columns


def _walk(
    *,
    labels: list[str],
    sessions: pd.DatetimeIndex,
    frames: dict[str, pd.DataFrame],
    universe: list[str],
    arrays: dict[str, dict[str, np.ndarray]],
    control: list[dict[str, Any]],
    treatment_conds: list[dict[str, Any]],
    trade_start: str,
    cooldown: int,
    max_positions: int,
    horizon: int,
    snapshot_id: str,
    config_hash_value: str,
    availability: dict[str, str],
    partitions: tuple[Partition, ...],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, np.ndarray]]]:
    books = {
        symbol: {
            "open": frames[symbol]["open"].to_numpy(dtype=float),
            "high": frames[symbol]["high"].to_numpy(dtype=float),
            "low": frames[symbol]["low"].to_numpy(dtype=float),
            "close": frames[symbol]["close"].to_numpy(dtype=float),
        }
        for symbol in universe
    }
    in_position: set[str] = set()
    pending_buy: set[str] = set()
    pending_sell: set[str] = set()
    held_bars: dict[str, int] = {}
    last_exit: dict[str, int] = {}
    events: list[dict[str, Any]] = []
    absent = set(arrays.get("__absent__", {}))
    for i, label in enumerate(labels):
        _apply_open(i, books, pending_buy, pending_sell, in_position, held_bars, last_exit)
        if trade_start and label[:10] < trade_start:
            _count_hold(i, books, in_position, pending_sell, held_bars, horizon)
            continue
        for symbol in sorted(universe):
            close = books[symbol]["close"][i]
            if not math.isfinite(close):
                continue
            control_ok, _, control_values = _evaluate(
                control, arrays[symbol], books[symbol]["close"], i
            )
            if control_ok is not True:
                continue
            treatment_ok, treatment_rows, _ = _evaluate(
                treatment_conds, arrays[symbol], books[symbol]["close"], i
            )
            reason, detail, status = _decision(
                treatment_ok,
                treatment_rows,
                symbol=symbol,
                i=i,
                in_position=in_position,
                pending_buy=pending_buy,
                last_exit=last_exit,
                cooldown=cooldown,
                max_positions=max_positions,
                opens=books[symbol]["open"],
            )
            if status == "accepted":
                pending_buy.add(symbol)
            day = _as_date(sessions[i])
            part = _covering(day, partitions)
            events.append(
                {
                    "event_id": f"{symbol}|{label}|{DEFINITION_ID}",
                    "symbol": symbol,
                    "session": label,
                    "session_index": i,
                    "snapshot_id": snapshot_id,
                    "config_hash": config_hash_value,
                    "engine_version": ENGINE_VERSION,
                    "feature_set": FEATURE_SET_VERSION,
                    "definition": DEFINITION_ID,
                    "status": status,
                    "reason": reason,
                    "detail": detail,
                    "execution_eligible": status == "accepted",
                    "partition": part.name if part is not None else "discovery",
                    "sector": availability["sector"],
                    "vix": availability["vix"],
                    "benchmark": availability["benchmark"],
                    "signal_values": _json_values(control_values),
                    "absent_keys": sorted(absent),
                }
            )
        _count_hold(i, books, in_position, pending_sell, held_bars, horizon)
    return events, books


def _apply_open(
    i: int,
    books: dict[str, dict[str, np.ndarray]],
    pending_buy: set[str],
    pending_sell: set[str],
    in_position: set[str],
    held_bars: dict[str, int],
    last_exit: dict[str, int],
) -> None:
    for symbol in sorted(pending_buy):
        if math.isfinite(books[symbol]["open"][i]):
            pending_buy.remove(symbol)
            in_position.add(symbol)
            held_bars[symbol] = 0
    for symbol in sorted(pending_sell):
        if math.isfinite(books[symbol]["open"][i]):
            pending_sell.remove(symbol)
            in_position.discard(symbol)
            held_bars.pop(symbol, None)
            last_exit[symbol] = i


def _count_hold(
    i: int,
    books: dict[str, dict[str, np.ndarray]],
    in_position: set[str],
    pending_sell: set[str],
    held_bars: dict[str, int],
    horizon: int,
) -> None:
    for symbol in sorted(in_position):
        if symbol in pending_sell or not math.isfinite(books[symbol]["close"][i]):
            continue
        held_bars[symbol] = held_bars.get(symbol, 0) + 1
        if held_bars[symbol] >= horizon:
            pending_sell.add(symbol)


def _decision(
    treatment_ok: bool | None,
    treatment_rows: list[dict[str, Any]],
    *,
    symbol: str,
    i: int,
    in_position: set[str],
    pending_buy: set[str],
    last_exit: dict[str, int],
    cooldown: int,
    max_positions: int,
    opens: np.ndarray,
) -> tuple[str, str, str]:
    if treatment_ok is not True:
        failed = [row["condition"] for row in treatment_rows if row.get("passed") is False]
        detail = "warmup" if treatment_ok is None and not failed else "; ".join(failed)
        return "treatment_rejected", detail, "rejected"
    if symbol in in_position or symbol in pending_buy:
        return "position_blocked", "", "rejected"
    if cooldown and symbol in last_exit and i - last_exit[symbol] < cooldown:
        return "cooldown_blocked", "", "rejected"
    occupied = len(in_position) + len(pending_buy)
    if occupied >= max_positions:
        return "capacity_blocked", "", "rejected"
    if _next_index(opens, i) is None:
        return "no_next_open", "", "rejected"
    return "accepted", "", "accepted"


def _feature_rows(
    events: list[dict[str, Any]],
    arrays: dict[str, dict[str, np.ndarray]],
    specs: list[dict[str, Any]],
    labels: list[str],
) -> list[dict[str, Any]]:
    absent = set(arrays.get("__absent__", {}))
    rows: list[dict[str, Any]] = []
    for event in events:
        symbol = event["symbol"]
        index = int(event["session_index"])
        for spec in specs:
            key = indicator_key({"id": spec["id"], "params": spec["params"]})
            series = arrays[symbol].get(key)
            value: float | None = None
            if series is not None and math.isfinite(series[index]):
                value = float(series[index])
            if key in absent:
                reason = "series_absent"
            elif value is None:
                reason = "warmup"
            else:
                reason = ""
            rows.append(
                {
                    "event_id": event["event_id"],
                    "feature_id": spec["id"],
                    "params": json.dumps(spec["params"], sort_keys=True),
                    "unit": spec["unit"],
                    "value": value,
                    "missing_reason": reason,
                    "source_session": labels[index],
                    "component_version": spec["version"],
                    "feature_set": FEATURE_SET_VERSION,
                }
            )
    return rows


def _outcome_rows(
    events: list[dict[str, Any]],
    books: dict[str, dict[str, np.ndarray]],
    frames: dict[str, pd.DataFrame],
    benchmark: str,
    horizons: tuple[int, ...],
    primary: int,
    cost_rate: float,
    labels: list[str],
) -> list[dict[str, Any]]:
    bench_open = bench_close = None
    if benchmark:
        bench_open = frames[benchmark]["open"].to_numpy(dtype=float)
        bench_close = frames[benchmark]["close"].to_numpy(dtype=float)
    rows: list[dict[str, Any]] = []
    for event in events:
        symbol = event["symbol"]
        book = books[symbol]
        fill_at = _next_index(book["open"], int(event["session_index"]))
        observed = [] if fill_at is None else _observed(book["close"], fill_at)
        for horizon in horizons:
            row = _one_outcome(
                event,
                book,
                fill_at,
                observed,
                horizon,
                primary,
                cost_rate,
                labels,
                bench_open,
                bench_close,
            )
            rows.append(row)
    return rows


def _one_outcome(
    event: dict[str, Any],
    book: dict[str, np.ndarray],
    fill_at: int | None,
    observed: list[int],
    horizon: int,
    primary: int,
    cost_rate: float,
    labels: list[str],
    bench_open: np.ndarray | None,
    bench_close: np.ndarray | None,
) -> dict[str, Any]:
    blank: dict[str, Any] = {
        "event_id": event["event_id"],
        "horizon_bars": horizon,
        "fill_session": None,
        "fill_price": None,
        "mark_session": None,
        "censored": True,
        "censor_reason": "no_next_open" if fill_at is None else "incomplete_horizon",
        "gross_return": None,
        "net_return": None,
        "benchmark_return": None,
        "benchmark_adjusted": None,
        "mfe": None,
        "mae": None,
        "realized_net": None,
    }
    if fill_at is None or len(observed) < horizon:
        return blank
    mark_at = observed[horizon - 1]
    fill_price = float(book["open"][fill_at])
    mark = float(book["close"][mark_at])
    gross = mark / fill_price - 1.0
    net = (mark * (1.0 - cost_rate)) / (fill_price * (1.0 + cost_rate)) - 1.0
    bench_return = _benchmark_return(bench_open, bench_close, fill_at, mark_at)
    adjusted = None if bench_return is None else net - bench_return
    mfe, mae = _excursions(book, fill_at, mark_at, fill_price)
    realized = net if event["status"] == "accepted" and horizon == primary else None
    return {
        "event_id": event["event_id"],
        "horizon_bars": horizon,
        "fill_session": labels[fill_at],
        "fill_price": fill_price,
        "mark_session": labels[mark_at],
        "censored": False,
        "censor_reason": "",
        "gross_return": gross,
        "net_return": net,
        "benchmark_return": bench_return,
        "benchmark_adjusted": adjusted,
        "mfe": mfe,
        "mae": mae,
        "realized_net": realized,
    }


def _benchmark_return(
    opens: np.ndarray | None, closes: np.ndarray | None, fill_at: int, mark_at: int
) -> float | None:
    if opens is None or closes is None:
        return None
    fill = float(opens[fill_at])
    mark = float(closes[mark_at])
    if not math.isfinite(fill) or not math.isfinite(mark) or fill == 0:
        return None
    return mark / fill - 1.0


def _excursions(
    book: dict[str, np.ndarray], fill_at: int, mark_at: int, fill_price: float
) -> tuple[float | None, float | None]:
    highs = book["high"][fill_at : mark_at + 1]
    lows = book["low"][fill_at : mark_at + 1]
    if not np.isfinite(highs).all() or not np.isfinite(lows).all():
        return None, None
    return float(np.max(highs) / fill_price - 1.0), float(np.min(lows) / fill_price - 1.0)


def _horizon_summary(
    rows: list[dict[str, Any]],
    *,
    block_length: int,
    horizon_bars: int,
    timeframe: str,
    seed: int,
) -> tuple[dict[str, Any], list[str]]:
    complete = [row for row in rows if not row["censored"] and row["net_return"] is not None]
    nets = np.array([float(row["net_return"]) for row in complete], dtype=float)
    summary: dict[str, Any] = {
        "complete": len(complete),
        "censored": len(rows) - len(complete),
        "mean_net": None if nets.size == 0 else float(nets.mean()),
        "median_net": None if nets.size == 0 else float(np.median(nets)),
        "win_rate": None if nets.size == 0 else float(np.mean(nets > 0)),
        "profit_factor": _profit_factor(nets),
        "mean_mfe": _mean_defined(complete, "mfe"),
        "mean_mae": _mean_defined(complete, "mae"),
        "uncertainty": None,
        "unit": "fraction",
    }
    extra: list[str] = []
    if nets.size < 2:
        extra.append("insufficient_for_interval")
        return summary, extra
    interval = moving_block_interval(
        nets,
        block_length=block_length,
        seed=seed,
        horizon_bars=horizon_bars,
        timeframe=timeframe,
    )
    summary["uncertainty"] = {
        "low": interval.low_pp,
        "high": interval.high_pp,
        "point": interval.point_pp,
        "method": interval.method,
        "unit": "fraction",
        "effective_sample": interval.effective_sample,
        "block_length": interval.block_length,
        "assumptions": list(interval.assumptions),
    }
    extra.extend(interval.assumptions)
    return summary, extra


def _buckets(
    events: list[dict[str, Any]],
    features: dict[tuple[str, str], dict[str, Any]],
    outcomes: dict[str, dict[str, Any]],
    *,
    minimum_count: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[float | None]] = {}
    for event in events:
        names = assign_buckets(event["event_id"], features)
        net = outcomes.get(event["event_id"], {}).get("net_return")
        censored = outcomes.get(event["event_id"], {}).get("censored", True)
        for name in names:
            grouped.setdefault(name, []).append(None if censored else net)
    rows = []
    for name in sorted(grouped):
        values = [value for value in grouped[name] if value is not None]
        missing = name.endswith(":missing")
        status = "unavailable" if missing or len(values) < minimum_count else "supported"
        rows.append(
            {
                "name": name,
                "count": len(grouped[name]),
                "complete": len(values),
                "status": status,
                "mean_net": None if not values else float(np.mean(values)),
                "exploratory": True,
            }
        )
    return rows


def assign_buckets(event_id: str, features: dict[tuple[str, str], dict[str, Any]]) -> list[str]:
    """Boundary rules are lower-inclusive except the open top bucket."""
    wanted = {
        "support_distance": _band_support,
        "rsi": _band_rsi,
        "macd": _sign_name,
        "macd_slope": _sign_name,
        "support_tests": _band_tests,
        "relative_volume": _band_volume,
        "market_structure": _sign_name,
        "stock_minus_spy": _sign_name,
        "vix_percentile_252": _band_vix,
        "vix_change_1": _sign_name,
    }
    names = []
    for feature_id, band in wanted.items():
        row = features.get((event_id, feature_id))
        if row is None or row["value"] is None:
            names.append(f"{feature_id}:missing")
            continue
        names.append(f"{feature_id}:{band(float(row['value']))}")
    return names


def _band_support(value: float) -> str:
    if value < 0:
        return "below_0"
    if value < 1:
        return "0_1"
    if value < 2:
        return "1_2"
    if value < 3:
        return "2_3"
    return "3_plus"


def _band_rsi(value: float) -> str:
    if value < 30:
        return "below_30"
    if value < 40:
        return "30_40"
    if value < 50:
        return "40_50"
    if value < 60:
        return "50_60"
    return "60_plus"


def _band_tests(value: float) -> str:
    if value == 1:
        return "one"
    if value == 2:
        return "two"
    if value >= 3:
        return "three_plus"
    return "other"


def _band_volume(value: float) -> str:
    if value < 1:
        return "below_1"
    if value <= 1.5:
        return "1_to_1_5"
    return "above_1_5"


def _band_vix(value: float) -> str:
    if value < 1 / 3:
        return "low"
    if value < 2 / 3:
        return "mid"
    return "high"


def _sign_name(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "flat"


def _manifest(
    bundle: EvidenceBundle,
    *,
    snapshot_id: str,
    config_hash_value: str,
    availability: dict[str, str],
    specs: list[dict[str, Any]],
    timeframe: str,
) -> dict[str, Any]:
    return {
        "feature_set": FEATURE_SET_VERSION,
        "definition_id": DEFINITION_ID,
        "definition": DENOMINATOR,
        "snapshot_id": snapshot_id,
        "config_hash": config_hash_value,
        "engine_version": ENGINE_VERSION,
        "timeframe": timeframe,
        "candidate_count": len(bundle.events),
        "data_availability": availability,
        "cache_keys": [
            feature_cache_key(snapshot_id, timeframe, spec) for spec in specs if not spec["derived"]
        ],
        "unresolved": list(UNRESOLVED_GAPS),
        "approximations": [],
        "decision_reason_codes": sorted({event["reason"] for event in bundle.events}),
    }


def _feature_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(row["event_id"], row["feature_id"]): row for row in rows}


def _outcomes_for(rows: list[dict[str, Any]], horizon: int) -> dict[str, dict[str, Any]]:
    return {row["event_id"]: row for row in rows if row["horizon_bars"] == horizon}


def _context_missing(bundle: EvidenceBundle, name: str) -> bool:
    if not bundle.events:
        return False
    return bundle.events[0].get(name) == "absent"


def _cost_rate(resolved: dict[str, Any]) -> float:
    book = resolved["portfolio"]
    if book.get("cost_model") != "research_bps":
        return 0.0
    return float(book.get("cost_bps_per_side") or 0.0) / 10_000.0


def _next_index(opens: np.ndarray, i: int) -> int | None:
    for j in range(i + 1, opens.size):
        if math.isfinite(opens[j]):
            return j
    return None


def _observed(closes: np.ndarray, fill_at: int) -> list[int]:
    return [j for j in range(fill_at, closes.size) if math.isfinite(closes[j])]


def _json_values(values: dict[str, float | None]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key, value in values.items():
        if value is None or not math.isfinite(float(value)):
            out[key] = None
        else:
            out[key] = float(value)
    return out


def _as_date(ts: pd.Timestamp | datetime) -> date:
    return pd.Timestamp(ts).date()


def _profit_factor(nets: np.ndarray) -> float | None:
    if nets.size == 0:
        return None
    losses = float(-nets[nets < 0].sum())
    if losses == 0:
        return None
    return float(nets[nets > 0].sum() / losses)


def _mean_defined(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return float(np.mean(values))


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in columns})
    return buf.getvalue()


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return json.dumps(value)
    return str(value)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
