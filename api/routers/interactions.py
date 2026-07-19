"""Level-interaction tracking — the research bench over the tick cache.

Pure market structure, independent of trades: a run exists for any cached
session. Both endpoints are GET and read the tick cache only (never Databento).
The run is cached-or-compute, keyed by the full config hash, so re-opening a
range you have already run is instant.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from journal.sim import ib as ibmod
from journal.sim import interactions as inter
from journal.sim import weekly_vwap as wkmod

router = APIRouter()


@router.get("/interactions")
def interactions_run(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    bin_size: float | None = Query(None),
    va_pct: float | None = Query(None),
    sources: str = Query("ny,globex,session_refs"),
    outcome_window_min: int | None = Query(None),
    zone_cluster_pts: float | None = Query(None),
    refresh: bool = Query(False),
) -> dict:
    cfg = inter.InteractionConfig.build(
        symbol,
        date.fromisoformat(start),
        date.fromisoformat(end),
        bin_size=bin_size,
        va_pct=va_pct,
        sources=[s for s in sources.split(",") if s] or ["ny", "globex", "session_refs"],
        outcome_window_min=outcome_window_min,
        zone_cluster_pts=zone_cluster_pts,
    )
    return inter.get(cfg, refresh=refresh)


@router.get("/interactions/runs")
def interactions_runs() -> list[dict]:
    """Every saved snapshot, newest first — lets the UI reopen a past run
    without retyping its config (same hash -> served from disk)."""
    return inter.list_runs()


@router.get("/interactions/ib")
def ib_run(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    ib_minutes: int | None = Query(None),
    refresh: bool = Query(False),
) -> dict:
    """The Initial Balance / ORB study — session structure only, much cheaper
    than the touch study (minute bars, no per-level scan). Same cached-or-
    compute contract, keyed by its own config hash."""
    cfg = ibmod.IbConfig.build(
        symbol, date.fromisoformat(start), date.fromisoformat(end),
        ib_minutes=ib_minutes,
    )
    return ibmod.get(cfg, refresh=refresh)


@router.get("/interactions/ib/runs")
def ib_runs() -> list[dict]:
    return ibmod.list_runs()


@router.get("/interactions/weekly-vwap")
def weekly_vwap_run(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    outcome_window_min: int | None = Query(None),
    refresh: bool = Query(False),
) -> dict:
    """The weekly-VWAP interaction study — where sessions open in the weekly
    envelope and what its band touches are worth. Same cached-or-compute
    contract, keyed by its own config hash."""
    cfg = wkmod.WeeklyVwapConfig.build(
        symbol, date.fromisoformat(start), date.fromisoformat(end),
        outcome_window_min=outcome_window_min,
    )
    return wkmod.get(cfg, refresh=refresh)


@router.get("/interactions/weekly-vwap/runs")
def weekly_vwap_runs() -> list[dict]:
    return wkmod.list_runs()


@router.get("/interactions/coverage")
def interactions_coverage(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
) -> dict:
    return inter.coverage(symbol, date.fromisoformat(start), date.fromisoformat(end))


@router.get("/interactions/day-chart/{day}")
def interactions_day_chart(
    day: str,
    symbol: str = Query(...),
    bin_size: float | None = Query(None),
    va_pct: float = Query(inter.profmod.VALUE_AREA_PCT),
    sources: str = Query("ny,globex"),
    ticks_per_bar: int | None = Query(None),
) -> dict:
    return inter.day_chart(
        symbol,
        date.fromisoformat(day),
        bin_size=bin_size,
        va_pct=va_pct,
        sources=tuple(s for s in sources.split(",") if s) or ("ny", "globex"),
        ticks_per_bar=ticks_per_bar,
    )
