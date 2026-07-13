"""Session regime KPIs — what kind of day it was, from the dual anchored VWAPs.

Keyed by (symbol, session date) and nothing else: a regime is a property of the
market, so the same artifact serves every run that touched the day. See
journal.sim.regime for what the numbers mean and why they are snapshotted at
checkpoints rather than only at the close.

Both endpoints read the tick cache only. A day whose ticks were never bought is
skipped, never fetched — a GET must not spend money at Databento.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS
from journal.sim import regime as regmod
from journal.sim import ticks as tickmod

router = APIRouter()


def _project_ribbon(ribbon: list[dict], tz: str | None) -> list[dict]:
    """Reproject the ribbon's UTC instants onto the chart's time axis.

    The artifact stores true UTC seconds — a regime is tz-free, and caching it any
    other way would key the same day twice. But a lightweight-charts time is the
    epoch of the *wall clock* in the display zone (see charts_data._epoch_local),
    so a ribbon shipped in raw UTC would sit hours off the candles it annotates.
    Same projection as every other chart payload, done at serve time.
    """
    if not ribbon:
        return ribbon
    zone = DISPLAY_TZS.get(tz or DEFAULT_DISPLAY_TZ, DISPLAY_TZS[DEFAULT_DISPLAY_TZ])
    ts = pd.to_datetime([b["time"] for b in ribbon], unit="s", utc=True)
    local = ts.tz_convert(zone).tz_localize(None).astype("datetime64[s]").astype("int64")
    return [{**b, "time": int(t)} for b, t in zip(ribbon, local)]


@router.get("/regime")
def regime_range(
    start: str = Query(...),
    end: str = Query(...),
    symbol: str = Query(...),
) -> dict:
    """Every cached session in [start, end] — KPIs and class, no ribbons.

    The ribbon is per-minute and only the day view draws one; shipping it for a
    month of days would be the bulk of the payload for a scatter that never reads
    it. ``skipped`` names the days that have no ticks on disk, so the calendar can
    show a hole rather than implying a flat day.
    """
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    days, skipped = [], []
    for d in tickmod.session_dates(s, e):
        r = regmod.get_regime(symbol, d)
        if r is None:
            skipped.append(d.isoformat())
            continue
        days.append({
            "date": r["date"], "class": r["class"], "partial": r["partial"],
            "checkpoints": r["checkpoints"],
        })
    return {"days": days, "skipped": skipped}


@router.get("/regime/{day}")
def regime_day(day: str, symbol: str = Query(...), tz: str | None = Query(None)) -> dict:
    """The full artifact for one session, ribbon included and drawn on the same
    time axis as that day's candles."""
    d = date.fromisoformat(day)
    r = regmod.get_regime(symbol, d)
    if r is None:
        raise HTTPException(404, f"No cached ticks for {symbol} on {day}")
    return {**r, "ribbon": _project_ribbon(r["ribbon"], tz)}
