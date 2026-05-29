"""Central JSON sanitizer applied to every response.

JSON has no representation for inf/NaN/NaT/numpy scalars/timestamps, and
``json.dumps`` will happily emit literal ``Infinity``/``NaN`` (invalid JSON that
breaks ``JSON.parse`` in the browser). Every value leaving the API passes
through ``sanitize`` so the frontend ``fmt()`` can render ``"inf"`` -> ∞ and
``null`` -> —.

Rules:
  - ``inf`` -> ``"inf"``, ``-inf`` -> ``"-inf"``
  - ``NaN`` / ``NaT`` / ``pd.isna`` -> ``null``
  - numpy ``int64``/``float64`` -> Python ``int``/``float``
  - ``Timestamp``/``datetime`` -> ISO 8601 string (format in React)
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any

import numpy as np
import pandas as pd
from fastapi.responses import JSONResponse


def sanitize(obj: Any) -> Any:
    """Recursively convert *obj* into JSON-safe primitives."""
    # Mappings / sequences first (before scalar isna checks, which choke on them).
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [sanitize(v) for v in obj.tolist()]
    if isinstance(obj, pd.Series):
        return [sanitize(v) for v in obj.tolist()]

    # None / pandas NA / NaT.
    if obj is None or obj is pd.NaT:
        return None

    # Timestamps / dates -> ISO.
    if isinstance(obj, (pd.Timestamp, _dt.datetime)):
        if pd.isna(obj):
            return None
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()

    # numpy scalars -> python.
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        obj = float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)

    # Floats: inf/-inf -> sentinels, nan -> null.
    if isinstance(obj, float):
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        if math.isnan(obj):
            return None
        return obj

    if isinstance(obj, (int, bool, str)):
        return obj

    # Fallback: scalar NA check (handles e.g. numpy datetime64 NaT).
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


class SanitizedJSONResponse(JSONResponse):
    """JSONResponse that runs every payload through :func:`sanitize`."""

    def render(self, content: Any) -> bytes:
        return super().render(sanitize(content))


def records(df: pd.DataFrame, columns: list[str] | None = None) -> list[dict]:
    """DataFrame -> list of sanitized record dicts (explicit projection)."""
    if df is None or df.empty:
        return []
    if columns is not None:
        df = df[[c for c in columns if c in df.columns]]
    return sanitize(df.to_dict("records"))
