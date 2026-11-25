"""Existing strategies keep exactly their engine/0.2.0 ledgers (see golden_util.py)."""

from __future__ import annotations

import json

from golden_util import GOLDEN, compute


def test_existing_strategies_reproduce_their_golden_ledgers() -> None:
    expected = json.loads(GOLDEN.read_text())
    got = compute()
    assert sorted(got) == sorted(expected)
    for name in expected:
        assert got[name] == expected[name], name
