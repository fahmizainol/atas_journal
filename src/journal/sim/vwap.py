"""Session-anchored VWAP and deviation bands, computed tick by tick.

Deliberately *not* shared with ``api/charts_data._vwap_rows``, which derives sigma
from 1-minute bar typical prices. These produce different numbers. The sim owns
this one so that the bands the engine trades against are the same bands the chart
draws — a strategy tested on one sigma and shown on another is untestable.

Volume-weighted, which is the standard (and what ATAS draws):

    vwap = sum(p*v) / sum(v)
    var  = sum(p^2*v) / sum(v) - vwap^2
    dev1 = vwap +/- sqrt(var),  dev2 = vwap +/- 2*sqrt(var)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BAND_COLS = ["mid", "std", "upper1", "upper2", "lower1", "lower2"]


def vwap_bands(ticks: pd.DataFrame) -> pd.DataFrame:
    """Running VWAP + 1σ/2σ bands, one row per tick, index-aligned to *ticks*.

    Accumulation starts at the first row, so the caller anchors the session by
    slicing the tick frame (e.g. from the 09:30 ET open) before calling.

    The first few ticks have a near-zero sigma — the bands are degenerate until
    some volume has traded. That is honest rather than a bug: it is exactly what
    a live VWAP looks like seconds after the open. Rules must not lean on them.
    """
    if ticks.empty:
        return pd.DataFrame(columns=BAND_COLS)

    p = ticks["price"].to_numpy(dtype="float64")
    v = ticks["size"].to_numpy(dtype="float64")

    cum_v = np.cumsum(v)
    cum_pv = np.cumsum(p * v)
    cum_p2v = np.cumsum(p * p * v)

    with np.errstate(divide="ignore", invalid="ignore"):
        mid = cum_pv / cum_v
        var = cum_p2v / cum_v - mid * mid
    # Catastrophic cancellation can push a true-zero variance a hair negative.
    std = np.sqrt(np.clip(var, 0.0, None))

    return pd.DataFrame({
        "mid": mid,
        "std": std,
        "upper1": mid + std,
        "upper2": mid + 2 * std,
        "lower1": mid - std,
        "lower2": mid - 2 * std,
    }, index=ticks.index)
