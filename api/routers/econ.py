"""USD economic-calendar markers for the charts (``journal.econ_calendar``)."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query

from journal import econ_calendar
from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS

from ..session_chart import _epoch_local

router = APIRouter()

MAX_SPAN_DAYS = 400


@router.get("/econ/events")
def econ_events(
    start: str = Query(..., description="YYYY-MM-DD, UTC day, inclusive"),
    end: str = Query(..., description="YYYY-MM-DD, UTC day, inclusive"),
    impact: str = Query("high,medium"),
    tz: str | None = Query(None),
) -> dict:
    """``time`` is wall-clock epoch in *tz* — the same axis the bars ride on
    (``session_chart._epoch_local``) — so a marker lines up without a client shift.
    Missing or stale weeks near the range's end are fetched on the way (≤2)."""
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if d1 < d0 or (d1 - d0).days > MAX_SPAN_DAYS:
        raise HTTPException(422, f"range must be 0..{MAX_SPAN_DAYS} days")
    # Display names ("New York") or IANA ("America/New_York") — the journal's
    # day replayer passes the latter.
    if tz in DISPLAY_TZS:
        disp_tz = DISPLAY_TZS[tz]
    else:
        try:
            disp_tz = ZoneInfo(tz or "")
        except (ZoneInfoNotFoundError, ValueError):
            disp_tz = DISPLAY_TZS[DEFAULT_DISPLAY_TZ]
    impacts = tuple(s.strip().lower() for s in impact.split(",") if s.strip())
    rows = econ_calendar.events(d0, d1, impacts, refresh_limit=2)
    return {
        "events": [
            {
                "id": e["id"],
                "time": _epoch_local(e["dateline"] * 10**9, disp_tz),
                "utc": e["dateline"],
                "name": e["name"],
                "impact": (e.get("impactName") or "").lower(),
                # "All Day" / "Tentative" rows carry a midnight dateline — no real
                # print time, so the chart shouldn't draw them as a line.
                "timed": bool(e.get("timeLabel")) and ":" in (e.get("timeLabel") or ""),
                "actual": e.get("actual") or "",
                "forecast": e.get("forecast") or "",
                "previous": e.get("previous") or "",
                "revision": e.get("revision") or "",
            }
            for e in rows
        ]
    }
