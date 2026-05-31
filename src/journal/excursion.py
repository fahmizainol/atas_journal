"""MAE/MFE and exit efficiency from cached 1m bars.

MFE = most favorable excursion (best unrealized gain during the hold).
MAE = most adverse excursion (worst unrealized loss during the hold).
Exit efficiency = realized PnL / MFE PnL (how much of the best move captured).
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from . import databento_client as dbn
from .atr import atr_series
from .config import point_value

_ATR_PERIOD = 14
# Extra 1m bars pulled *before* entry so Wilder's ATR is warmed up by the time
# the trade starts (period + a few for smoothing convergence).
_ATR_WARMUP_BARS = 30


def trade_excursion(trade: pd.Series) -> dict | None:
    """Compute MAE/MFE for one logical trade. None if bars unavailable."""
    bars = dbn.get_bars(trade["instrument"], trade["entry_ts_utc"], trade["exit_ts_utc"])
    if bars is None or bars.empty:
        return None

    pv = point_value(trade["instrument"])
    qty = float(trade["max_contracts"])
    entry = float(trade["avg_entry"])
    hi_idx = bars["high"].idxmax()
    lo_idx = bars["low"].idxmin()
    hi = float(bars.loc[hi_idx, "high"])
    lo = float(bars.loc[lo_idx, "low"])
    hi_time = bars.loc[hi_idx, "ts_utc"]
    lo_time = bars.loc[lo_idx, "ts_utc"]

    if trade["direction"] == "Long":
        mfe_pts = hi - entry
        mae_pts = lo - entry  # negative
        mfe_price, mae_price = hi, lo
        mfe_time, mae_time = hi_time, lo_time
    else:
        mfe_pts = entry - lo
        mae_pts = entry - hi  # negative
        mfe_price, mae_price = lo, hi
        mfe_time, mae_time = lo_time, hi_time

    mfe_usd = mfe_pts * pv * qty
    mae_usd = mae_pts * pv * qty
    realized = float(trade["gross_pnl"])
    exit_eff = (realized / mfe_usd) if mfe_usd > 0 else None

    avg_atr_pts, avg_atr_usd = _avg_atr_during_hold(trade, pv, qty)

    return {
        "mfe_points": mfe_pts,
        "mae_points": mae_pts,
        "mfe_usd": mfe_usd,
        "mae_usd": mae_usd,
        "mfe_price": mfe_price,
        "mae_price": mae_price,
        "mfe_time": mfe_time,
        "mae_time": mae_time,
        "exit_efficiency": exit_eff,
        "avg_atr_pts": avg_atr_pts,
        "avg_atr_usd": avg_atr_usd,
        "bars": bars,
    }


def _avg_atr_during_hold(
    trade: pd.Series, pv: float, qty: float
) -> tuple[float | None, float | None]:
    """Mean ATR(14) over the bars between entry and exit. Pulls a warmup buffer
    before entry so the first in-hold bar already has a converged ATR."""
    entry_utc = pd.Timestamp(trade["entry_ts_utc"]).tz_convert("UTC")
    exit_utc = pd.Timestamp(trade["exit_ts_utc"]).tz_convert("UTC")
    warmup_start = entry_utc - timedelta(minutes=_ATR_WARMUP_BARS)
    buffered = dbn.get_bars(trade["instrument"], warmup_start, exit_utc, slice_to_window=True)
    if buffered is None or buffered.empty:
        return None, None
    atr = atr_series(buffered, _ATR_PERIOD)
    in_hold = atr[(buffered["ts_utc"] >= entry_utc) & (buffered["ts_utc"] <= exit_utc)]
    in_hold = in_hold.dropna()
    if in_hold.empty:
        return None, None
    avg_pts = float(in_hold.mean())
    return avg_pts, avg_pts * pv * qty


def aggregate_excursion(trades: pd.DataFrame, limit: int | None = None) -> pd.DataFrame:
    """Per-trade MAE/MFE table across trades (only those with bar data)."""
    if trades is None or trades.empty or not dbn.is_available():
        return pd.DataFrame()
    rows = []
    sub = trades if limit is None else trades.head(limit)
    for _, t in sub.iterrows():
        exc = trade_excursion(t)
        if exc is None:
            continue
        rows.append({
            "trade_no": t.get("trade_no"),
            "direction": t["direction"],
            "net_pnl": t["net_pnl"],
            "mfe_usd": exc["mfe_usd"],
            "mae_usd": exc["mae_usd"],
            "exit_efficiency": exc["exit_efficiency"],
            "avg_atr_pts": exc.get("avg_atr_pts"),
            "avg_atr_usd": exc.get("avg_atr_usd"),
        })
    return pd.DataFrame(rows)
