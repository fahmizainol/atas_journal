"""Trade Simulator (Replay) — ship a cached session's raw ticks to the browser.

This backs the Lab "Simulator" tab: a fxreplay-style replay where the client
plays a chosen day's tape back tick-by-tick, and you practise entering/closing
against it. Everything after this module is client-side — the playback clock,
the developing VWAP bands, the forming candle, and the (throwaway) trade
blotter all live in the browser. This router's only job is:

  - ``/simulator/days``     which sessions have ticks on disk to replay;
  - ``/simulator/session``  one session's full tape (on+rth+post), delta-encoded.

Both are read-only GETs over ticks already on disk — never a Databento fetch, so
picking a day never spends money (mirrors the regime router's contract). "On
disk" means either store: the Databento corpus, and the Rithmic recordings under
``data/live/ticks``. Each day says which one it came from.

Session context. The client holds the whole tape, so anything about *today* it
can develop for itself; the one thing it cannot is what came before. The session
payload therefore carries a small ``context`` block whose only real content is
``adr14`` — the mean day range of the fourteen sessions before this one, read
out of the saved IB study (``journal.sim.ib``). It is knowable at the open, so
showing it mid-replay leaks nothing, and it is the denominator the simulator's
calibration indicators are pinned to. Absent (``null``) rather than approximated
for a day the study doesn't cover.


Encoding lives in ``api.tape_codec`` — shared with the live tape, which ships
slices of a session still in progress in exactly the same bytes. Two encoders
would be two chances for the one client decoder to be right about only one.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import pandas as pd
import pyarrow.parquet as pq
from fastapi import APIRouter, HTTPException, Query

from journal.config import DEFAULT_DISPLAY_TZ, ET_TZ, contract_spec, root_symbol
from journal.live import recorder as recmod
from journal.sim import ib as ibmod
from journal.sim import ticks as tickmod
from journal.sim import weekly as weeklymod

from ..tape_codec import encode_ticks, local_ms as _local_ms, zone_for as _zone

router = APIRouter()

# The tick cache holds two layouts: the legacy per-segment split
# ({SYMBOL}_{DATE}_rth.parquet + _on/_post) and the whole-day file the fetcher
# writes now ({SYMBOL}_{DATE}_day.parquet). Either one makes a session
# replayable — RTH is the part that must be there, and ``cached_rth`` already
# resolves it across both — so the day list has to walk both.
_CACHE_RE = re.compile(
    r"^(?P<sym>[A-Z0-9]+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<seg>rth|day)\.parquet$")

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


def _wall_ms(day: date, t: time, zone) -> int:
    """Epoch-ms of a given ET session wall-clock time, projected into ``zone``."""
    et = pd.Timestamp(datetime.combine(day, t), tz=ET_TZ)
    naive = et.tz_convert(zone).tz_localize(None)
    return int(pd.Timestamp(naive).value // 1_000_000)


@lru_cache(maxsize=4096)
def _day_file_span(name: str, mtime: float, size: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First/last tick instant of a cached parquet, read from its footer.

    Whole-day file or a single segment — the footer answers the same way, and
    the legacy layout needs it too now that an early close is reported.

    Keyed by (name, mtime, size) so a re-fetched day invalidates itself. The
    listing walks every cached session, and opening ~600 files to read a column
    costs seconds where the footer statistics cost milliseconds — this endpoint
    only needs the span, never the ticks.
    """
    pf = pq.ParquetFile(tickmod.TICK_CACHE_DIR / name)
    i = pf.schema_arrow.names.index("ts_utc")
    md = pf.metadata
    lo = md.row_group(0).column(i).statistics.min
    hi = md.row_group(md.num_row_groups - 1).column(i).statistics.max
    return pd.Timestamp(lo), pd.Timestamp(hi)


"""How far short of 16:00 ET a tape may stop and still be called a full session.

A threshold is unavoidable — an RTH-only tape's last print lands microseconds
*before* the close, so a bare ``hi < close`` marks every ordinary day early. It
is set at a minute because that is the gap NQ cannot produce while trading: the
contract prints many times a second through the bell, so a silent minute into
the close is not a liquid session ending normally. The real early closes this
has to catch (13:00 ET) miss by three hours, so nothing here rests on the exact
value — only on its being far below an hour and far above a second.
"""
_CLOSE_SLACK = pd.Timedelta(minutes=1)


def _span_of(path) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """A cached file's span, or None if the footer cannot answer."""
    try:
        st = path.stat()
        return _day_file_span(path.name, st.st_mtime, st.st_size)
    except Exception:  # noqa: BLE001 — a listing must not fail on one bad file
        return None


def _ends_early(hi: pd.Timestamp | None, day: date) -> bool:
    """Does this tape stop materially before the 16:00 ET close?

    False when the span could not be read: "cannot say" is not "yes", and a
    listing that invented an early close from an unreadable footer would put a
    warning on a day that does not deserve one.
    """
    return bool(hi is not None and hi < tickmod.session_bounds_utc(day)[1] - _CLOSE_SLACK)


def _day_file_segments(path, day: date) -> tuple[bool, bool, bool]:
    """(has_overnight, has_post, ends_early) for a whole-day file, from its span.

    The three windows partition the day contiguously in time, so a tape that
    starts before the bell carries the night and one that runs past 16:00 ET
    carries the post hour. A footer with no statistics (or an unreadable one)
    reports neither rather than guessing — the session endpoint re-derives the
    truth from the ticks it actually slices.
    """
    span = _span_of(path)
    if span is None:
        return False, False, False
    lo, hi = span
    return (bool(lo < tickmod.session_bounds_utc(day)[0]),
            bool(hi >= tickmod.post_bounds_utc(day)[0]),
            _ends_early(hi, day))


def _live_segments(sym: str, day: date) -> dict | None:
    """The window flags for a recorded session — None if it is not replayable.

    A recorded day is whatever the recorder happened to see: a session still in
    progress, one connected to at 10:40, one whose host died at lunch. Listing
    any of those plays a tape that stops without saying so, which is the exact
    lie ``/live/recordings``' coverage strip exists to prevent. Two bars, and
    the interesting thing is which one is asked of what:

      - **settled**, from the manifest — not from the tape. This is the trap
        ``journal.live.harvest`` documents at length: a half-day session and a
        session with a hole in it are *identical* from the timestamps, so "does
        the tape reach 16:00" cannot be the test. 2026-06-19 and 2026-07-03 are
        real 13:00 ET closes, and a tape-derived rule drops both as truncated.
        ``closed`` is written when a session rolls or a harvest completes, so it
        answers the question the timestamps cannot.
      - **the open is covered**, from the span. A day whose tape begins after
        the bell is a fragment however settled it is, and replaying from 10:40
        with no morning behind it is not the session.

    ``ends_early`` is the residue, and it is deliberately not a defect flag: a
    half day and a session the harvester could not finish both stop before
    16:00, and by the paragraph above nothing here can tell them apart. It says
    only what it knows — this tape stops before the standard close — and leaves
    the difference recoverable where the manifest is, in ``/live/recordings``.
    """
    if not (recmod.read_manifest(sym, day) or {}).get("closed"):
        return None
    span = tickmod.live_day_span(sym, day)
    if span is None:
        return None
    lo, hi = span
    rth_open = tickmod.session_bounds_utc(day)[0]
    if lo > rth_open or hi <= rth_open:
        return None
    return {
        "has_overnight": bool(lo < rth_open),
        "has_post": bool(hi >= tickmod.post_bounds_utc(day)[0]),
        "ends_early": _ends_early(hi, day),
    }


@router.get("/simulator/days")
def simulator_days(root: str | None = Query(None)) -> dict:
    """Every session with RTH ticks on disk — the replayable days.

    ``root`` filters to one instrument family (e.g. NQ). Each entry carries the
    raw contract symbol from the filename, so the session endpoint reads exactly
    those parquets and never has to resolve a roll.

    **Both stores**, tagged ``source``: ``"cache"`` for the Databento corpus,
    ``"live"`` for a session recorded off Rithmic. That reverses decision 4 of
    docs/live-shadow-plan.md, which anticipated the reversal in as many words
    ("reversible if it chafes — glob both stores and tag the source"). It chafed:
    the corpus is pinned at the Databento budget and ends 2026-06-30, so every
    session since has been recorded and unreplayable, which is the wrong half of
    the year to be unable to practise on.

    Decision 3 is untouched and permanent. Nothing here moves a tick between
    stores, and ``get_day_ticks`` — what the *engine* loads a session with —
    still reads the Databento cache alone. A recorded day became something you
    can replay; it did not become something a backtest can quote.

    Cache first on a collision, mirroring ``ticks.cached_rth`` and
    ``live._history_source``: a day held in both stores draws the same bars on
    every surface, or the Simulator and the live chart quietly disagree about a
    Tuesday.
    """
    found: dict[tuple[str, str], dict] = {}
    for p in sorted(tickmod.TICK_CACHE_DIR.glob("*.parquet")):
        m = _CACHE_RE.match(p.name)
        if not m:
            continue
        sym, d = m.group("sym"), m.group("date")
        if root and root_symbol(sym) != root:
            continue
        day = date.fromisoformat(d)
        if m.group("seg") == "rth":
            stem = f"{sym}_{d}"
            on = (tickmod.TICK_CACHE_DIR / f"{stem}_on.parquet").exists()
            post = (tickmod.TICK_CACHE_DIR / f"{stem}_post.parquet").exists()
            # The RTH file's own last print — the post segment, where there is
            # one, runs past the close by definition and would answer this for
            # every day at once. An early close is a property of the *session*.
            early = _ends_early((_span_of(p) or (None, None))[1], day)
        else:
            on, post, early = _day_file_segments(p, day)
        # A session held in both layouts is one replayable day: OR the context
        # windows, since cached_overnight/cached_post read across both too.
        # ``ends_early`` ANDs instead: the two layouts disagreeing means one of
        # them holds a fuller tape, and the fuller one is what will be served.
        prev = found.get((sym, d))
        found[(sym, d)] = {
            "date": d,
            "symbol": sym,
            "root": root_symbol(sym),
            "has_overnight": on or bool(prev and prev["has_overnight"]),
            "has_post": post or bool(prev and prev["has_post"]),
            "ends_early": early and (prev is None or prev["ends_early"]),
            "source": "cache",
        }
    # The live store, second — a day already found above is a cached day, and
    # skipping it here is what keeps the precedence one rule rather than two.
    for sym, day in recmod.recorded_days():
        if root and root_symbol(sym) != root:
            continue
        key = (sym, day.isoformat())
        if key in found:
            continue
        segs = _live_segments(sym, day)
        if segs is None:
            continue
        found[key] = {
            "date": day.isoformat(),
            "symbol": sym,
            "root": root_symbol(sym),
            **segs,
            "source": "live",
        }
    days = sorted(found.values(), key=lambda r: (r["date"], r["symbol"]))
    roots = sorted({r["root"] for r in days})
    return {"days": days, "roots": roots}


@router.get("/simulator/session")
def simulator_session(
    symbol: str = Query(..., description="Raw contract, e.g. NQH5"),
    date_: str = Query(..., alias="date"),
    tz: str | None = Query(None),
) -> dict:
    """One session's full tape (on + rth + post), delta-encoded for playback.

    RTH is required; the overnight and post segments are spliced in when cached.
    The overnight is what anchors the Globex VWAP at 18:00 — without it the client
    simply doesn't draw that band (``globex_anchor_ms`` is null), and the weekly
    anchor goes with it (``weekly_seed`` is null, as it also is for a week with a
    hole in it — see ``journal.sim.weekly``).

    Serves both stores, because the three ``cached_*`` reads below already did —
    Databento first, live second, per Phase 5. Listing the live store in
    ``/simulator/days`` is what made that reachable; no read here changed.

    ``globex_open_ms`` ships beside ``globex_anchor_ms`` so the pair can be
    compared. On a cached day they are the same instant and always were. On a
    recorded one they can differ: the anchor is the first print that was
    actually *captured*, and a night the feed's backfill did not reach in full
    starts late — Rithmic's replay has been observed to truncate without
    erroring. The band would then be anchored mid-night and look no different
    from a correct one, so the client is given both numbers rather than a
    server-side verdict about how late is too late.
    """
    day = date.fromisoformat(date_)
    zone = _zone(tz)

    rth = tickmod.cached_rth(symbol, day)
    if rth is None:
        raise HTTPException(404, f"No cached RTH ticks for {symbol} on {date_}")
    on = tickmod.cached_overnight(symbol, day)
    post = tickmod.cached_post(symbol, day)

    # on (prev-day 18:00 -> 09:30) | rth (09:30 -> 16:00) | post (16:00 -> 18:00)
    # are contiguous and each internally sorted, so wall-clock order is preserved
    # by concatenation — the prefix-sum deltas below stay non-negative.
    parts = [f for f in (on, rth, post) if f is not None and not f.empty]
    frame = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]

    spec = contract_spec(symbol)
    tick_size = float(spec["tick_size"])
    point_value = float(spec["point_value"])

    tape = encode_ticks(frame, zone, tick_size)
    t_ms = _local_ms(frame["ts_utc"], zone)

    rth_open_ms = _wall_ms(day, RTH_OPEN, zone)
    rth_close_ms = _wall_ms(day, RTH_CLOSE, zone)
    globex_open_ms = _wall_ms(day - timedelta(days=1), tickmod.GLOBEX_OPEN, zone)
    globex_anchor_ms = int(_local_ms(on["ts_utc"].iloc[:1], zone)[0]) if on is not None and not on.empty else None

    # The weekly anchor, as the (Σv, Σpv, Σp²v) already behind it when this
    # session's Globex open arrives — the client seeds its own accumulator with
    # it and gets the same line api/sim_charts draws (weekly.weekly_seed is the
    # one truth for both). Same honesty rule as the Globex band: absent, never
    # approximated. No seed without the night, because the tape the client
    # accumulates over would then start at the bell and the seed would be short
    # a whole overnight session.
    weekly_seed = weeklymod.weekly_seed(symbol, day) if on is not None and not on.empty else None

    # The prior-days context, plus the two research constants the client's
    # indicators are cut at. The constants ship with the payload rather than
    # being duplicated in TypeScript so there is one place they are pinned: they
    # are measured numbers (vol-clock §10c), and a copy of a measured number is a
    # copy that can drift away from the study that produced it.
    adr = ibmod.day_context(root_symbol(symbol), day)

    return {
        "symbol": symbol,
        "root": root_symbol(symbol),
        "date": date_,
        "tz": tz or DEFAULT_DISPLAY_TZ,
        "tick_size": tick_size,
        "point_value": point_value,
        **tape,
        "session_start_ms": int(t_ms[0]),
        "session_end_ms": int(t_ms[-1]),
        "rth_open_ms": rth_open_ms,
        "rth_close_ms": rth_close_ms,
        # Default the replay to the RTH bell unless the tape starts after it.
        "default_start_ms": max(rth_open_ms, int(t_ms[0])),
        "globex_open_ms": globex_open_ms,
        "globex_anchor_ms": globex_anchor_ms,
        # Which store answered. Mirrors ``cached_rth``'s own precedence rather
        # than re-deriving it, so the tag can never disagree with the frame.
        "source": "cache" if tickmod.have_segment(symbol, day, "rth") else "live",
        "weekly_seed": list(weekly_seed) if weekly_seed is not None else None,
        "has_overnight": on is not None and not on.empty,
        "has_post": post is not None and not post.empty,
        "context": {
            "adr14": adr["adr14"],
            "adr_source": adr["source"],
            "ib_minutes": ibmod.DEFAULTS["ib_minutes"],
            "ib_width_edges": list(ibmod.WIDTH_TERCILE_EDGES),
            "post_ib_add_x": ibmod.POST_IB_RANGE_ADD_X,
        },
    }
