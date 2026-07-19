"""Tick-count bars.

A "tick" here is one Databento trade record. That is *not* necessarily one tick
on an ATAS chart: MDP3 reports a single aggressor sweeping N resting orders as N
trade records, and a retail feed may aggregate them into one print. So a 500-tick
bar built here and a 500-tick bar in ATAS will not have identical boundaries, and
since the acceptance rule keys off a candle *close*, signals drift slightly. This
is a known, accepted approximation — keep ``n`` configurable and say so in the UI.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BAR_COLS = ["ts_utc", "open", "high", "low", "close", "volume", "start_idx", "end_idx"]


def tick_bars(ticks: pd.DataFrame, n: int = 500) -> pd.DataFrame:
    """Aggregate a tick frame into fixed-count bars.

    ``ts_utc`` is the timestamp of the bar's **last** tick — the instant the bar
    completed and its close became known. The 1m-bar pipeline stamps bars at
    their *open*; the difference is deliberate. The engine acts on bar closes, so
    a bar must not appear to exist before the tick that finished it.

    ``start_idx``/``end_idx`` are inclusive positions back into ``ticks``, which
    is how the engine looks up the VWAP bands in force at a bar's close.

    A trailing partial bar (< n ticks) is dropped: it never closed, so no
    close-based rule may fire on it.
    """
    if ticks.empty:
        return pd.DataFrame(columns=BAR_COLS)

    n_bars = len(ticks) // n
    if n_bars == 0:
        return pd.DataFrame(columns=BAR_COLS)

    used = n_bars * n
    price = ticks["price"].to_numpy()[:used].reshape(n_bars, n)
    size = ticks["size"].to_numpy()[:used].reshape(n_bars, n)
    # ``.values`` keeps the tz-aware column as datetime64[ns] (UTC wall time);
    # ``.to_numpy()`` would box every tick into a Python Timestamp — an O(ticks)
    # object conversion for a column we only sample every ``n``-th row of. The
    # naive ns values ARE the UTC instants, and ``utc=True`` below re-localizes.
    ts = ticks["ts_utc"].values[:used].reshape(n_bars, n)
    starts = np.arange(n_bars) * n

    return pd.DataFrame({
        "ts_utc": pd.to_datetime(ts[:, -1], utc=True),
        "open": price[:, 0],
        "high": price.max(axis=1),
        "low": price.min(axis=1),
        "close": price[:, -1],
        "volume": size.sum(axis=1).astype("float64"),
        "start_idx": starts,
        "end_idx": starts + n - 1,
    })
