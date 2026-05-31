"""Average True Range (Wilder's RMA) over a bars DataFrame.

Pure compute — callers supply bars already loaded from databento_client. The
output is aligned to the input index and expressed in price points. The first
`period` values are NaN until Wilder's smoothing has enough data to converge.
"""

from __future__ import annotations

import pandas as pd


def atr_series(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR(period). Expects columns high, low, close. Returns price points."""
    if bars is None or bars.empty:
        return pd.Series(dtype=float)
    h = bars["high"].astype(float)
    l = bars["low"].astype(float)
    prev_close = bars["close"].astype(float).shift(1)
    tr = pd.concat(
        [(h - l), (h - prev_close).abs(), (l - prev_close).abs()], axis=1
    ).max(axis=1)
    # Wilder's smoothing = EMA with alpha = 1/period.
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
