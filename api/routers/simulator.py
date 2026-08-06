"""Trade Simulator (Replay) — ship a cached session's raw ticks to the browser.

This backs the Lab "Simulator" tab: a fxreplay-style replay where the client
plays a chosen day's tape back tick-by-tick, and you practise entering/closing
against it. Everything after this module is client-side — the playback clock,
the developing VWAP bands, the forming candle, and the (throwaway) trade
blotter all live in the browser. This router's only job is:

  - ``/simulator/days``     which sessions have ticks on disk to replay;
  - ``/simulator/session``  one session's full tape (on+rth+post), delta-encoded.

Both are read-only GETs over the existing tick cache — never a Databento fetch,
so picking a day never spends money (mirrors the regime router's contract).

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
from datetime import date, datetime, time
from functools import lru_cache

import pandas as pd
import pyarrow.parquet as pq
from fastapi import APIRouter, HTTPException, Query

from journal.config import DEFAULT_DISPLAY_TZ, ET_TZ, contract_spec, root_symbol
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
    """First/last tick instant of a whole-day parquet, read from its footer.

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


def _day_file_segments(path, day: date) -> tuple[bool, bool]:
    """(has_overnight, has_post) for a whole-day file, from its time span alone.

    The three windows partition the day contiguously in time, so a tape that
    starts before the bell carries the night and one that runs past 16:00 ET
    carries the post hour. A footer with no statistics (or an unreadable one)
    reports neither rather than guessing — the session endpoint re-derives the
    truth from the ticks it actually slices.
    """
    try:
        st = path.stat()
        lo, hi = _day_file_span(path.name, st.st_mtime, st.st_size)
    except Exception:  # noqa: BLE001 — a listing must not fail on one bad file
        return False, False
    return bool(lo < tickmod.session_bounds_utc(day)[0]), \
        bool(hi >= tickmod.post_bounds_utc(day)[0])


@router.get("/simulator/days")
def simulator_days(root: str | None = Query(None)) -> dict:
    """Every session with RTH ticks on disk — the replayable days.

    ``root`` filters to one instrument family (e.g. NQ). Each entry carries the
    raw contract symbol from the filename, so the session endpoint reads exactly
    those parquets and never has to resolve a roll.
    """
    found: dict[tuple[str, str], dict] = {}
    for p in sorted(tickmod.TICK_CACHE_DIR.glob("*.parquet")):
        m = _CACHE_RE.match(p.name)
        if not m:
            continue
        sym, d = m.group("sym"), m.group("date")
        if root and root_symbol(sym) != root:
            continue
        if m.group("seg") == "rth":
            stem = f"{sym}_{d}"
            on = (tickmod.TICK_CACHE_DIR / f"{stem}_on.parquet").exists()
            post = (tickmod.TICK_CACHE_DIR / f"{stem}_post.parquet").exists()
        else:
            on, post = _day_file_segments(p, date.fromisoformat(d))
        # A session held in both layouts is one replayable day: OR the context
        # windows, since cached_overnight/cached_post read across both too.
        prev = found.get((sym, d))
        found[(sym, d)] = {
            "date": d,
            "symbol": sym,
            "root": root_symbol(sym),
            "has_overnight": on or bool(prev and prev["has_overnight"]),
            "has_post": post or bool(prev and prev["has_post"]),
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
        "globex_anchor_ms": globex_anchor_ms,
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
