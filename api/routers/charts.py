"""Candlestick chart endpoints: raw bars, excursion, composite trade chart."""

from __future__ import annotations

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
