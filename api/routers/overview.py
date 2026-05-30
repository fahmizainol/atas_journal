"""Overview tab: headline metrics, equity curve, daily PnL, PnL distribution."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from journal import metrics

from ..scope import Scope, resolve_scope
from ..serialize import records, sanitize
from ..summary import summary_extras

router = APIRouter()


@router.get("/metrics")
def get_metrics(scope: Scope = Depends(resolve_scope)) -> dict:
    m = metrics.compute_metrics(scope.filtered)
    m["view"] = scope.view
    return m


@router.get("/summary-extras")
def get_summary_extras(scope: Scope = Depends(resolve_scope)) -> dict:
    return sanitize(summary_extras(scope.filtered))


@router.get("/equity-curve")
def get_equity_curve(scope: Scope = Depends(resolve_scope)) -> list[dict]:
    eq = metrics.equity_curve(scope.filtered)
    return records(eq, ["ts", "trade_no", "pnl", "equity", "drawdown"])


@router.get("/daily-pnl")
def get_daily_pnl(scope: Scope = Depends(resolve_scope)) -> list[dict]:
    daily = metrics.daily_pnl(scope.filtered)
    return records(daily, ["date", "net_pnl", "trades"])


@router.get("/distribution")
def get_distribution(scope: Scope = Depends(resolve_scope)) -> dict:
    df = scope.filtered
    values = df["net_pnl"].astype(float).tolist() if not df.empty else []
    return {"values": values}
