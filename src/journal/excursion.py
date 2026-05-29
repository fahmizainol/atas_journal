"""MAE/MFE and exit efficiency from cached 1m bars.

MFE = most favorable excursion (best unrealized gain during the hold).
MAE = most adverse excursion (worst unrealized loss during the hold).
Exit efficiency = realized PnL / MFE PnL (how much of the best move captured).
"""

from __future__ import annotations

import pandas as pd

from . import databento_client as dbn
from .config import point_value


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
        "bars": bars,
    }


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
        })
    return pd.DataFrame(rows)
