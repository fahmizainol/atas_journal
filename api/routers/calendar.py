"""Calendar tab: monthly PnL grid + single-day explorer."""

from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from journal import metrics

from ..scope import Scope, resolve_scope
from ..serialize import records, sanitize
from ..summary import summary_extras

router = APIRouter()


def _day_frame(scope: Scope, day: date) -> pd.DataFrame:
    tf = scope.filtered
    if tf.empty:
        return tf
    sub = tf[tf["entry_ts_local"].dt.date == day]
    return sub.sort_values("entry_ts_utc").reset_index(drop=True)


@router.get("/calendar")
def calendar(scope: Scope = Depends(resolve_scope)) -> dict:
    tf = scope.filtered
    if tf.empty:
        return {"months": [], "days": []}

    t = tf.copy()
    t["date"] = t["entry_ts_local"].dt.date
    days = []
    for d, g in t.groupby("date"):
        pnl = g["net_pnl"].astype(float)
        n = len(pnl)
        days.append({
            "date": d.isoformat(),
            "net_pnl": float(pnl.sum()),
            "trades": n,
            "win_rate": float((pnl > 0).sum() / n * 100) if n else 0.0,
        })
    months = sorted({(d.year, d.month) for d in t["date"]}, reverse=True)
    month_objs = [{"year": y, "month": m,
                   "label": f"{date(y, m, 1):%B %Y}"} for y, m in months]
    return {"months": month_objs, "days": days}


@router.get("/day/{day}")
def day_detail(day: str, scope: Scope = Depends(resolve_scope)) -> dict:
    d = date.fromisoformat(day)
    day_df = _day_frame(scope, d)
    if day_df.empty:
        raise HTTPException(404, f"No trades on {day} in scope")

    kpis = metrics.compute_metrics(day_df)
    equity = metrics.equity_curve(day_df)
    instrument = day_df["instrument"].value_counts().idxmax()

    per_trade_bars = [
        {
            "trade_no": int(r["trade_no"]),
            "net_pnl": float(r["net_pnl"]),
            "time": r["entry_ts_local"].strftime("%H:%M:%S"),
        }
        for _, r in day_df.iterrows()
    ]

    cols = ["trade_no", "instrument", "direction", "max_contracts",
            "entry_ts_local", "exit_ts_local", "duration_s",
            "avg_entry", "avg_exit", "net_pnl"]
    return {
        "kpis": sanitize(kpis),
        "extras": sanitize(summary_extras(day_df)),
        "equity": records(equity, ["ts", "trade_no", "pnl", "equity", "drawdown"]),
        "per_trade_bars": per_trade_bars,
        "trades": records(day_df, cols),
        "instrument": instrument,
    }
