"""The weekly VWAP anchor: Sunday's 18:00 ET Globex open, carried across days.

The session anchors (NY, Globex) accumulate inside one tick frame, so
``vwap.vwap_bands`` alone is enough for them. The weekly anchor starts days
before the frame it is drawn over, and holding a whole week of ticks in memory
per chart request is not on. Instead each prior session collapses to the three
sums the accumulation actually needs — (Σv, Σpv, Σp²v) — and the current
session's bands are computed with that total as a seed. Algebraically identical
to concatenating the week's ticks; a few floats per day instead.

Two honesty rules, both inherited from how the Globex anchor already behaves
(absent rather than approximated when the night isn't on disk):

  - A week with a hole in it is not drawn. If any expected session between the
    week's start and *day* has no cached ticks, there is no seed — a "weekly"
    VWAP silently missing Monday would be a different line pretending to be
    this one. Exchange-closed days are not holes.
  - A roll restarts the anchor. Sessions always sit wholly on one contract
    (see ``ticks``), so a week that spans a roll would average two price
    series ~100 points apart. The anchor moves up to the roll session's Globex
    open, and the pre-roll days are excluded — never spliced.

One known gap this module cannot close from the cache: the tick cache covers
18:00 → 16:00 ET (overnight + RTH segments), so the 16:00–17:00 afternoon hour
of each completed day is not in the seed. A platform weekly VWAP (ATAS) does
include it. It is the lowest-volume hour of the day; buying it retroactively
for the whole history is a cost decision that hasn't been made.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from . import ticks as tickmod
from . import vwap as vwapmod

Seed = tuple[float, float, float]  # (Σv, Σpv, Σp²v)


def week_start(day: date) -> date:
    """Monday of the week holding *day* — the session whose Globex open
    (Sunday 18:00 ET) anchors the weekly VWAP."""
    return day - timedelta(days=day.weekday())


def _sums_path(symbol: str, day: date):
    return tickmod.TICK_CACHE_DIR / f"{symbol}_{day.isoformat()}_sums.json"


def _segments_on_disk(symbol: str, day: date) -> list[str]:
    return [seg for seg in ("on", "rth")
            if tickmod._cache_path(symbol, day, seg).exists()]


def session_sums(symbol: str, day: date) -> Seed | None:
    """(Σv, Σpv, Σp²v) over one session's cached segments, or None when nothing
    is on disk. Never fetches — this backs chart GETs.

    The scalar result is cached beside the parquets, keyed by which segments it
    was summed over: a night bought after the sums were first taken invalidates
    them rather than being silently left out.
    """
    segs = _segments_on_disk(symbol, day)
    if not segs:
        return None
    path = _sums_path(symbol, day)
    if path.exists():
        rec = json.loads(path.read_text())
        if rec.get("segments") == segs:
            return (rec["v"], rec["pv"], rec["p2v"])
    v = pv = p2v = 0.0
    for seg in segs:
        s = vwapmod.frame_sums(tickmod._read_day_parquet(symbol, day, seg))
        v, pv, p2v = v + s[0], pv + s[1], p2v + s[2]
    path.write_text(json.dumps({"segments": segs, "v": v, "pv": pv, "p2v": p2v}))
    return (v, pv, p2v)


def weekly_seed(contract: str, day: date) -> Seed | None:
    """The accumulation already behind the weekly anchor when *day*'s Globex
    session opens: every same-contract session from the week's start through
    *day - 1*, summed from the tick cache.

    (0, 0, 0) on the week's first session — the weekly anchor IS that session's
    Globex open, and the weekly VWAP coincides with the Globex one all Monday.
    None when the week cannot be honestly built: a prior session the exchange
    traded but whose ticks were never bought, or a contract that cannot be
    resolved from the roll map without a probe (GETs never reach Databento).
    """
    sym = tickmod.contract_for_cached(contract, day)
    if sym is None:
        return None
    seed = (0.0, 0.0, 0.0)
    d = week_start(day)
    while d < day:
        if d.weekday() >= 5 or tickmod.market_closed(contract, d):
            d += timedelta(days=1)
            continue
        if tickmod.contract_for_cached(contract, d) != sym:
            # Pre-roll session: the anchor restarts at the roll, so the week
            # accumulated so far is discarded, not carried across the seam.
            seed = (0.0, 0.0, 0.0)
            d += timedelta(days=1)
            continue
        s = session_sums(sym, d)
        if s is None:
            return None  # a hole in the week — no honest weekly line exists
        seed = (seed[0] + s[0], seed[1] + s[1], seed[2] + s[2])
        d += timedelta(days=1)
    return seed
