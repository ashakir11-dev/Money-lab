"""Money-lab: a local, single-user trading research lab for US stocks and ETFs."""

__version__ = "0.1.0"
ENGINE_VERSION = "engine/0.3.0"
# Earlier engine versions whose runs this engine reproduces exactly (ledger for ledger,
# checked by tests/test_golden_ledgers.py), so their runs can still be duplicated and
# their paper accounts keep processing.
COMPATIBLE_ENGINE_VERSIONS = frozenset({"engine/0.2.0"})
