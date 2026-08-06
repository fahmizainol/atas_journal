"""Draft strategies: study events rendered as trades on real session charts.

Read-only and cache-only, like the research router: the spec files in
``data/drafts/`` are the primary artifact, materialized results snapshot to
``data/cache/drafts/`` on first read, and the day chart delegates to the
interactions day-chart builder — the same payload the Interactions session
tape stitches, so the Drafts tape inherits its timeframes (1m/3m/5m/tick
bars) and its gap-free multi-session behavior — with the draft's
pseudo-trades layered on top, snapped onto whichever bar grid is drawn.
Nothing here simulates: a draft is NOT a backtest, and the payload carries
the guardrail stats (skips, overlaps) rather than hiding them.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS
from journal.sim import drafts as draftsmod
from journal.sim import interactions as inter

from .. import sim_charts

router = APIRouter()


def _spec(slug: str) -> draftsmod.DraftSpec:
    spec = draftsmod.load_spec(slug)
    if spec is None:
        raise HTTPException(404, f"No draft spec named {slug!r}")
    return spec


@dataclass(frozen=True)
class _ChartCfg:
    """The fields sim_charts._trade_rect reads, standing in for a run config."""

    contract: str
    instrument: str


@router.get("/drafts")
def list_drafts() -> list[dict]:
    out = []
    for spec in draftsmod.list_specs():
        snap = draftsmod.read(spec)   # summary only if already materialized
        out.append({
            "slug": spec.slug, "name": spec.name,
            "hypothesis": spec.hypothesis, "source_doc": spec.source_doc,
            "direction": spec.direction, "symbol": spec.symbol,
            "summary": snap["summary"] if snap else None,
        })
    return out


def _covered_days(symbol: str, first: str | None, last: str | None) -> list[str]:
    """Every cached session in the draft's span — the tape renders all of
    them, not just trade days, so the weekly anchor reads continuously and
    adjacent sessions sit adjacent (no silent multi-day jumps)."""
    if not first or not last:
        return []
    cov = inter.coverage(symbol, date.fromisoformat(first), date.fromisoformat(last))
    return [d["date"] for d in cov["days"] if d["rth"]]


@router.get("/drafts/{slug}")
def draft_detail(slug: str, refresh: bool = Query(False)) -> dict:
    spec = _spec(slug)
    snap = draftsmod.get(spec, refresh=refresh)
    s = snap["summary"]
    return {
        "days": _covered_days(spec.symbol, s["first_day"], s["last_day"]),
        "slug": spec.slug, "name": spec.name, "hypothesis": spec.hypothesis,
        "source_doc": spec.source_doc, "notes": spec.notes,
        "direction": spec.direction, "symbol": spec.symbol,
        "race_sigma": spec.race_sigma, "horizon_min": spec.horizon_min,
        "query": spec.query, "checklist": spec.checklist,
        "run_id": snap["run_id"],
        "summary": snap["summary"],
        "trades": snap["trades"],
    }


@router.get("/drafts/{slug}/day-chart/{day}")
def day_chart(slug: str, day: date,
              ticks_per_bar: int | None = Query(None),
              bar_minutes: int | None = Query(None)) -> dict:
    spec = _spec(slug)
    snap = draftsmod.get(spec)

    payload = inter.day_chart(spec.symbol, day, ticks_per_bar=ticks_per_bar,
                              bar_minutes=bar_minutes)
    if not payload.get("available") or not payload.get("bars"):
        raise HTTPException(404, f"No cached ticks for {spec.symbol} on {day}")

    # Snap each trade instant onto the drawn bar grid (raw UTC epochs, same
    # time base as the payload): exact at 1m — the trades live on the minute
    # grid — floor-of-bar at 3m/5m and tick bars. Server-side so the marker
    # times always exist in the candle series (misaligned markers are dropped
    # silently by the chart).
    bar_times = [b["time"] for b in payload["bars"]]

    def bar_time(iso: str) -> int:
        t = int(pd.Timestamp(iso).timestamp())
        i = bisect_right(bar_times, t) - 1
        return bar_times[max(i, 0)]

    zone = DISPLAY_TZS[DEFAULT_DISPLAY_TZ]
    cfg = _ChartCfg(contract=spec.symbol, instrument=spec.symbol)
    markers: list[dict] = []
    rects: list[dict] = []
    for tr in snap["trades"]:
        if tr["day"] != day.isoformat():
            continue
        entry_t, exit_t = bar_time(tr["entry_ts_utc"]), bar_time(tr["exit_ts_utc"])
        m = sim_charts._markers(tr, entry_t, exit_t, None, text=False)
        # A trade whose source never gave an exit collapses onto its entry bar
        # (see the Drysdale draft): the exit arrow would then sit on top of the
        # entry arrow and assert an exit nobody recorded. Entry mark only.
        if entry_t == exit_t and tr["entry_ts_utc"] == tr["exit_ts_utc"]:
            m = m[:-1]
        markers.extend(m)
        rects.append(sim_charts._trade_rect(tr, entry_t, exit_t, zone, cfg))

    payload["markers"] = markers
    payload["trades"] = rects
    return payload
