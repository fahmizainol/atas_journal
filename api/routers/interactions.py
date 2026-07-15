"""Level-interaction tracking — the research bench over the tick cache.

Pure market structure, independent of trades: a run exists for any cached
session. Both endpoints are GET and read the tick cache only (never Databento).
The run is cached-or-compute, keyed by the full config hash, so re-opening a
range you have already run is instant.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from journal.sim import interactions as inter

router = APIRouter()


@router.get("/interactions")
def interactions_run(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    bin_size: float | None = Query(None),
    va_pct: float | None = Query(None),
    sources: str = Query("ny,globex"),
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
        sources=[s for s in sources.split(",") if s] or ["ny", "globex"],
        outcome_window_min=outcome_window_min,
        zone_cluster_pts=zone_cluster_pts,
    )
    return inter.get(cfg, refresh=refresh)


@router.get("/interactions/coverage")
def interactions_coverage(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
) -> dict:
    return inter.coverage(symbol, date.fromisoformat(start), date.fromisoformat(end))
