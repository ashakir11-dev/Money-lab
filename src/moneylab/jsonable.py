"""JSON-clean values: NaN and inf become null, numpy and pandas scalars become Python."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd


def jsonable(obj: Any) -> Any:
    """Plain JSON values: NaN/inf become null, numpy and pandas scalars become Python."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [jsonable(v) for v in obj.tolist()]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, pd.Timestamp | date):
        return obj.isoformat()[:10]
    if obj is pd.NaT:
        return None
    return obj
