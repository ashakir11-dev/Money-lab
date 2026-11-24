"""Paper forward testing of frozen runs on incoming sessions (see docs/paper-trading.md)."""

from moneylab.paper.desk import (
    ACCEPTED,
    CONFLICT,
    DUPLICATE,
    REJECTED,
    IngestResult,
    PaperDesk,
)

__all__ = ["ACCEPTED", "CONFLICT", "DUPLICATE", "REJECTED", "IngestResult", "PaperDesk"]
