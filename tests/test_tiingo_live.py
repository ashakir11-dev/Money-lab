"""Live Tiingo check. Deselected by default; run it in a session with TIINGO_API_KEY:

    uv run pytest -m network -q tests/test_tiingo_live.py

About 5 requests: AAPL and SPY history, a bogus ticker, and AAPL new sessions.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest

from moneylab.data.providers import ProviderNotFound
from moneylab.service import Lab

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(not os.environ.get("TIINGO_API_KEY"), reason="TIINGO_API_KEY not set"),
]


def test_live_history_split_dividend_cache_and_new_sessions(lab: Lab) -> None:
    # AAPL: $0.82 dividend ex 2020-08-07 and a 4-for-1 split on 2020-08-31.
    snap = lab.import_provider(["AAPL", "SPY"], date(2020, 8, 3), date(2020, 9, 30), "live")
    prov = snap.meta["provenance"]
    assert snap.meta["source"] == "tiingo" and prov["http_requests"] == 2
    acts = lab.provider().fetch_history(["AAPL"], date(2020, 8, 3), date(2020, 9, 30)).actions
    got = {
        (d.date().isoformat(), t, round(v, 4))
        for d, t, v in acts[["date", "type", "value"]].itertuples(index=False)
    }
    assert ("2020-08-31", "split", 4.0) in got
    assert ("2020-08-07", "dividend", 0.82) in got
    # Our adjustment from raw prices agrees with Tiingo's adjClose up to a constant.
    assert max(prov["tiingo_adj_ratio_spread"].values()) < 1e-3, prov
    assert lab.settings.tiingo_api_key is not None
    key = lab.settings.tiingo_api_key.get_secret_value()
    for f in lab.settings.home.rglob("*"):
        if f.is_file():
            assert key.encode() not in f.read_bytes(), f

    # Re-import comes from the cache: same snapshot, no requests.
    again = lab.import_provider(["AAPL", "SPY"], date(2020, 8, 3), date(2020, 9, 30))
    assert again.id == snap.id

    with pytest.raises(ProviderNotFound):
        lab.provider().fetch_history(["ZZZZNOTATICKER"], date(2024, 1, 2), date(2024, 1, 31))

    # New sessions after a recent snapshot come back in the import format.
    today = datetime.now(UTC).date()
    recent = lab.import_provider(["AAPL"], today - timedelta(days=30), today - timedelta(days=10))
    new = lab.fetch_new_sessions(recent.id)
    assert not new.bars.empty
    assert new.bars["date"].min().date() > date.fromisoformat(recent.meta["last_date"])
