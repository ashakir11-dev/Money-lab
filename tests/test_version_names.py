"""Family version names: the next duplicate is one past the runs already in the family."""

from moneylab.lineage import next_version_name, version_number


def run(
    id: str, name: str, parent: str | None = None, created: str = "2026-01-01T00:00:00"
) -> dict:
    return {"id": id, "name": name, "parent_id": parent, "created_at": created}


def test_first_duplicate_is_v2() -> None:
    runs = [run("a", "RSI pullback")]
    assert next_version_name(runs, "a") == "RSI pullback v2"


def test_next_number_counts_the_whole_family() -> None:
    runs = [
        run("a", "RSI pullback", created="2026-01-01"),
        run("b", "RSI pullback v2", "a", "2026-01-02"),
        run("c", "RSI pullback v3", "b", "2026-01-03"),
    ]
    assert next_version_name(runs, "a") == "RSI pullback v4"
    assert next_version_name(runs, "c") == "RSI pullback v4"


def test_base_name_drops_one_copy_duplicate_or_version_suffix() -> None:
    assert next_version_name([run("a", "Foo (copy)")], "a") == "Foo v2"
    assert next_version_name([run("a", "Foo (duplicate)")], "a") == "Foo v2"
    assert next_version_name([run("a", "Foo v2")], "a") == "Foo v2"
    runs = [run("a", "RSI pullback v2"), run("b", "kept", "a")]
    assert next_version_name(runs, "b") == "RSI pullback v3"


def test_missing_parent_starts_its_own_family() -> None:
    runs = [run("b", "Orphan v3", "gone")]
    assert next_version_name(runs, "b") == "Orphan v2"


def test_version_number_reads_the_suffix_or_creation_order() -> None:
    runs = [
        run("a", "RSI pullback", created="2026-01-01"),
        run("b", "renamed", "a", "2026-01-02"),
        run("c", "RSI pullback v4", "a", "2026-01-03"),
    ]
    assert version_number(runs, "a") == 1
    assert version_number(runs, "b") == 2
    assert version_number(runs, "c") == 4
