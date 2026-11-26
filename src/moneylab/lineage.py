"""Family membership for duplicated runs.

The original run is the one whose parent is missing. The next duplicate is named
one past the number of runs already in that family.
"""

from __future__ import annotations

import re
from typing import Any

_SUFFIX = re.compile(r"(?: \(copy\)| \(duplicate\)| v\d+)$")
_VERSION = re.compile(r" v(\d+)$")


def root_id(runs: list[dict[str, Any]], run_id: str) -> str:
    by_id = {r["id"]: r for r in runs}
    seen: set[str] = set()
    current = run_id
    while current in by_id and current not in seen:
        seen.add(current)
        parent = by_id[current].get("parent_id")
        if not parent or parent not in by_id:
            return current
        current = parent
    return run_id


def family(runs: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    root = root_id(runs, run_id)
    return [r for r in runs if root_id(runs, r["id"]) == root]


def base_name(name: str) -> str:
    stripped = _SUFFIX.sub("", name)
    return stripped or name


def next_version_name(runs: list[dict[str, Any]], source_id: str) -> str:
    root = root_id(runs, source_id)
    by_id = {r["id"]: r for r in runs}
    name = by_id[root]["name"] if root in by_id else "Strategy"
    return f"{base_name(name)} v{len(family(runs, source_id)) + 1}"


def version_number(runs: list[dict[str, Any]], run_id: str) -> int:
    by_id = {r["id"]: r for r in runs}
    run = by_id.get(run_id)
    if run is not None:
        match = _VERSION.search(run["name"])
        if match:
            return int(match.group(1))
    members = sorted(family(runs, run_id), key=lambda r: (r.get("created_at") or "", r["id"]))
    for index, member in enumerate(members, start=1):
        if member["id"] == run_id:
            return index
    return 1
