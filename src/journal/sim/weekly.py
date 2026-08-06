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

The 16:00–17:00 afternoon hour each completed day used to fall outside the two
cached segments (rth …→16:00, on 18:00→…), so it was silently absent from the
seed — a real skew, not the "slight" one first assumed: on the 2025-03-31 pdl
cross-check it moved the developing lower-2σ by ~16–21 pts vs ATAS. It is now a
third cached segment ('post', 16:00→18:00 ET) and enters the seed via
``session_sums`` whenever it is on disk; ``weekly_vwap`` versioning forces a
re-run so old snapshots don't mix skewed and corrected lines. A day whose post
hour was never bought still contributes on+rth (honest absence, not a fudge).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache

import pandas as pd

from . import ticks as tickmod
from . import vwap as vwapmod

Seed = tuple[float, float, float]  # (Σv, Σpv, Σp²v)


def week_start(day: date) -> date:
    """Monday of the week holding *day* — the session whose Globex open
    (Sunday 18:00 ET) anchors the weekly VWAP."""
    return day - timedelta(days=day.weekday())


def _sums_path(symbol: str, day: date):
    return tickmod.TICK_CACHE_DIR / f"{symbol}_{day.isoformat()}_sums.json"


@lru_cache(maxsize=4096)
def _day_file_segments(name: str, mtime: float, size: int) -> tuple[str, ...]:
    """Which windows a whole-day parquet actually holds, off its label column.

    Keyed by (name, mtime, size) so a re-fetched day invalidates itself. Reading
    one dictionary column of a million rows is cheap; reading the ticks to find
    out is not, and the seed asks this of every prior session in the week.
    """
    df = pd.read_parquet(tickmod.TICK_CACHE_DIR / name, columns=[tickmod.SEG_COL])
    have = set(df[tickmod.SEG_COL].dropna().unique())
    return tuple(seg for seg in tickmod.SEGMENTS if seg in have)


def _segments_on_disk(symbol: str, day: date) -> list[str]:
    # 'post' (16:00-17:00 ET) joined the set once bought: a day that has it now
    # keys ["on","rth","post"], which no longer matches an older ["on","rth"]
    # sums file, so session_sums recomputes it in — the hole heals itself as the
    # hour lands on disk, and days still missing it keep their valid old sums.
    segs = [seg for seg in tickmod.SEGMENTS
            if tickmod._cache_path(symbol, day, seg).exists()]
    if segs:
        return segs
    # The whole-day layout the fetcher writes now. Same question asked of one
    # file instead of three — and it has to be asked, or every day written that
    # way reads as "no ticks on disk" and the week silently has a hole in it,
    # which is how the weekly anchor quietly stopped drawing on recent sessions.
    # The windows are named the same, so the key a day produces is the same in
    # both layouts and the sums already cached under it stay valid.
    p = tickmod._day_path(symbol, day)
    if not p.exists():
        return []
    st = p.stat()
    try:
        return list(_day_file_segments(p.name, st.st_mtime, st.st_size))
    except Exception:  # noqa: BLE001 — a day file written before the label column
        return [seg for seg in tickmod.SEGMENTS
                if not tickmod._read_segment_cached(symbol, day, seg).empty]


def _live_sums(symbol: str, day: date) -> Seed | None:
    """The same three sums over a *recorded* session, or None if none is.

    The live store's equivalent of the sums file, and keyed the same way it is
    keyed everywhere else: on the chunk set. A day still being recorded therefore
    invalidates its own cached sums as it grows, with nobody having to remember
    to clear anything — which matters here more than in the Databento cache,
    where a file only ever changes when somebody buys more of it.

    The sums file is written *inside the live day's own directory*. Putting it
    beside the Databento parquets would be the first crack in the disjointness
    the whole reconciliation rests on.
    """
    chunks = list(tickmod.live_chunks(symbol, day))
    if not chunks:
        return None
    path = tickmod.live_day_dir(symbol, day) / "sums.json"
    if path.exists():
        try:
            rec = json.loads(path.read_text())
            if rec.get("chunks") == chunks:
                return (rec["v"], rec["pv"], rec["p2v"])
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    df = tickmod.live_day_ticks(symbol, day)
    if df is None or df.empty:
        return None
    v, pv, p2v = vwapmod.frame_sums(df)
    try:
        path.write_text(json.dumps({"chunks": chunks, "v": v, "pv": pv, "p2v": p2v}))
    except OSError:
        pass  # the sums are still right; they just cost a re-read next time
    return (v, pv, p2v)


def session_sums(symbol: str, day: date) -> Seed | None:
    """(Σv, Σpv, Σp²v) over one session's cached segments, or None when nothing
    is on disk. Never fetches — this backs chart GETs.

    The scalar result is cached beside the parquets, keyed by which segments it
    was summed over: a night bought after the sums were first taken invalidates
    them rather than being silently left out.

    Falls through to the live store when a session was recorded rather than
    bought — Databento first, exactly as ``ticks.cached_rth`` resolves it, so a
    day that exists in both reads as the bought one and a recorded week is only
    ever built out of recorded days.
    """
    segs = _segments_on_disk(symbol, day)
    if not segs:
        return _live_sums(symbol, day)
    path = _sums_path(symbol, day)
    if path.exists():
        rec = json.loads(path.read_text())
        if rec.get("segments") == segs:
            return (rec["v"], rec["pv"], rec["p2v"])
    v = pv = p2v = 0.0
    for seg in segs:
        # Resolved across both layouts — a window is a file of its own or a
        # slice of the day file, and which one it is is not this module's
        # business.
        s = vwapmod.frame_sums(tickmod._read_segment_cached(symbol, day, seg))
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
