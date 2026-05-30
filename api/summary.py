"""Shared summary cards (direction splits, excursion, activity window).

Used by the day explorer and the overview tab so both render the same set of
metrics over whatever trade frame they pass in (a single day, or the filtered
scope for the picked duration).
"""

from __future__ import annotations

import pandas as pd

from journal import excursion


def _side_stats(sub: pd.DataFrame) -> dict:
    n = len(sub)
    if n == 0:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0}
    pnl = sub["net_pnl"].astype(float)
    return {
        "trades": n,
        "net_pnl": float(pnl.sum()),
        "win_rate": float((pnl > 0).sum() / n * 100),
    }


def summary_extras(df: pd.DataFrame) -> dict:
    """Direction splits, total contracts, avg MFE/MAE/efficiency, activity window."""
    if df is None or df.empty:
        return {
            "total_contracts": 0.0,
            "long": _side_stats(df if df is not None else pd.DataFrame()),
            "short": _side_stats(pd.DataFrame()),
            "avg_mfe_usd": None,
            "avg_mae_usd": None,
            "avg_exit_efficiency": None,
            "window_start": None,
            "window_end": None,
        }

    long_df = df[df["direction"] == "Long"]
    short_df = df[df["direction"] == "Short"]

    exc = excursion.aggregate_excursion(df)
    if exc.empty:
        avg_mfe = avg_mae = avg_eff = None
    else:
        avg_mfe = float(exc["mfe_usd"].mean())
        avg_mae = float(exc["mae_usd"].mean())
        eff = exc["exit_efficiency"].dropna()
        avg_eff = float(eff.mean() * 100) if len(eff) else None

    return {
        "total_contracts": float(df["max_contracts"].sum()),
        "long": _side_stats(long_df),
        "short": _side_stats(short_df),
        "avg_mfe_usd": avg_mfe,
        "avg_mae_usd": avg_mae,
        "avg_exit_efficiency": avg_eff,
        "window_start": df["entry_ts_local"].min(),
        "window_end": df["exit_ts_local"].max(),
    }
