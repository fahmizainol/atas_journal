"""Candlestick chart endpoints: raw bars, excursion, composite trade chart."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS

from .. import charts_data
from ..scope import Scope, resolve_scope

router = APIRouter()


def _find(scope: Scope, trade_no: int):
    tf = scope.filtered
    match = tf[tf["trade_no"] == trade_no] if not tf.empty else tf
    if match.empty:
        raise HTTPException(404, f"Trade #{trade_no} not in scope")
    return match.iloc[0]


@router.get("/bars")
def bars(
    instrument: str = Query(...),
    start_utc: str = Query(...),
    end_utc: str = Query(...),
    tf: str = Query("1m"),
    tz: str | None = Query(None),
) -> dict:
    disp_tz = DISPLAY_TZS[tz if tz in DISPLAY_TZS else DEFAULT_DISPLAY_TZ]
    return charts_data.bars_window(instrument, start_utc, end_utc, tf, disp_tz)


@router.get("/trades/{trade_no}/excursion")
def excursion(trade_no: int, scope: Scope = Depends(resolve_scope)) -> dict:
    trade = _find(scope, trade_no)
    return charts_data.excursion_summary(trade)


@router.get("/trade-chart/{trade_no}")
def trade_chart(
    trade_no: int, tf: str = Query("1m"), scope: Scope = Depends(resolve_scope)
) -> dict:
    trade = _find(scope, trade_no)
    return charts_data.trade_chart(trade, tf, scope.tz)


@router.get("/day-chart/{day}")
def day_chart(
    day: str, tf: str = Query("1m"), scope: Scope = Depends(resolve_scope)
) -> dict:
    d = date.fromisoformat(day)
    tf_frame = scope.filtered
    day_df = tf_frame[tf_frame["entry_ts_local"].dt.date == d] if not tf_frame.empty else tf_frame
    if day_df.empty:
        raise HTTPException(404, f"No trades on {day} in scope")
    day_df = day_df.sort_values("entry_ts_utc").reset_index(drop=True)
    return charts_data.day_chart(day_df, d, tf, scope.tz)
