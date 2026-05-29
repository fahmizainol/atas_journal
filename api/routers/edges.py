"""Behavioral edge breakdowns (weekday / hour / hold-time / direction)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from journal import edges

from ..scope import Scope, resolve_scope
from ..serialize import records

router = APIRouter()

_COLS = ["bucket", "trades", "net_pnl", "win_rate", "expectancy"]


@router.get("/edges")
def get_edges(scope: Scope = Depends(resolve_scope)) -> dict:
    tf = scope.filtered
    return {
        "by_weekday": records(edges.by_weekday(tf), _COLS),
        "by_hold_time": records(edges.by_hold_time(tf), _COLS),
        "by_direction": records(edges.by_direction(tf), _COLS),
        "by_hour_kl": records(edges.by_hour_kl(tf), _COLS),
        "by_hour_et": records(edges.by_hour_et(tf), _COLS),
    }
