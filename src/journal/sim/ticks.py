"""Databento trade ticks with on-disk parquet caching.

Mirrors ``databento_client`` (per-symbol, per-day parquet + lru_cache over the
read) but fetches the ``trades`` schema. A session is carved into three windows:

  - ``rth``  09:30 → 16:00 ET — what every strategy reads today;
  - ``on``   18:00 (prev calendar day) → 09:30 ET — the Globex overnight,
    read by strategies that declare ``session="globex"``.
  - ``post`` 16:00 → 18:00 ET — the post-RTH Globex hour (16:00-17:00 is live
    trading, the last hour before the 17:00-18:00 maintenance halt; the halt
    itself just returns no trades). The original two windows left this hour
    uncovered, so cumulative *cross-session* anchors (the weekly VWAP) silently
    dropped it. Spliced into the weekly seed — never into the single-session
    18:00 Globex anchor, which is anchored at 18:00 by design.

They chain contiguously (18:00… | …09:30 | 09:30…16:00 | 16:00…18:00) with no
gap and no duplicate, since ``get_range`` is end-exclusive. Together they are the
whole trading day: prev 18:00 → 18:00 ET.

TWO CACHE LAYOUTS live here, and every read goes through the same resolver:

  - ``day``  one parquet per session holding all three windows. What new fetches
    write, and the layout the cache was migrated to.
  - ``rth`` / ``on`` / ``post`` — one parquet per window. The original layout,
    still read (and still *topped up*) wherever it is what's on disk, so the
    migration never had to re-buy a tick.

The window is baked into the filename in both layouts, so widening one later
invalidates rather than silently serving a short day.

READ CONTRACT — the load-bearing invariant. Callers get exactly the window they
asked for, whichever layout backs it: ``get_day_ticks(include_overnight=False)``
returns 09:30→16:00 and nothing else. The engine builds its tick bars and its
VWAP over the whole frame it is handed (``vwap_bands`` accumulates from row 0),
so a frame that quietly carried the overnight in front would re-phase every bar
and move the NY anchor to 18:00 — silently, with no error, changing the numbers
of every RTH strategy. Slice before you return, always.

Every fetch here is for a *raw* contract (``NQZ5``), never Databento's continuous
symbol — but which raw contract is decided per session, so a window may span a
roll (see ``contract_for``).

That indirection is the whole point. ``NQ.v.0`` rolls on the UTC day boundary,
19:00 ET, which lands an hour *inside* the Globex segment below: on 2025-12-16
the front month flipped mid-overnight and the price gapped 225 points. Fetching
the continuous symbol would therefore splice two contracts into one session's
ticks, and a Globex-anchored VWAP drawn across that seam is nonsense. So we take
Databento's roll *date* and quantize it to our session boundary: both segments of
a session always come from one contract, and the roll falls between sessions.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import pandas as pd
import pyarrow.parquet as pq

from ..config import (CACHE_DIR, CONTRACT_SPECS, DATA_DIR, DATABENTO_DATASET,
                      ET_TZ, continuous_symbol, databento_key, root_symbol)
from ..databento_client import DatabentoUnavailable

TICK_CACHE_DIR = CACHE_DIR / "ticks"

# Recorded live ticks — a store deliberately DISJOINT from the Databento cache
# above. Nothing written here ever grows the backtest corpus, which is what
# makes "do live signals match the backtest" a question with an answer: the
# reference the live day is checked against must not be partly made of the live
# day. See docs/live-shadow-plan.md decision 3, which is permanent.
#
# Decision 4 — "recorded days are not replayable either" — is NOT permanent and
# no longer holds: ``/simulator/days`` lists both stores, tagged by source. That
# changes what a person can *look at*, and nothing about what a backtest reads.
# ``get_day_ticks`` still reads the Databento cache and does not fall through
# here, so a recorded day remains unavailable to the engine.
#
# Note this is not ``config.LIVE_DIR`` — that is the ATAS import drop folder,
# and the collision is only in the word.
LIVE_TICK_DIR = DATA_DIR / "live" / "ticks"

# The windows we fetch and cache. Each segment is baked into its cache
# filename, so widening a window later invalidates rather than silently serves
# a short day.
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
POST_OPEN = time(16, 0)    # RTH close; Globex keeps trading to the 17:00 halt
POST_CLOSE = time(18, 0)   # Globex reopen after the daily maintenance break
GLOBEX_OPEN = time(18, 0)  # previous calendar day (Sunday, for Monday sessions)

TICK_COLS = ["ts_utc", "price", "size", "side"]


def session_bounds_utc(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """RTH open/close for an ET session date, as UTC instants (DST-aware)."""
    open_et = pd.Timestamp(datetime.combine(day, RTH_OPEN), tz=ET_TZ)
    close_et = pd.Timestamp(datetime.combine(day, RTH_CLOSE), tz=ET_TZ)
    return open_et.tz_convert("UTC"), close_et.tz_convert("UTC")


def overnight_bounds_utc(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Globex open (18:00 ET the previous calendar day) → RTH open, as UTC
    instants. Keyed by the *session* date: Monday's overnight starts Sunday."""
    open_et = pd.Timestamp(datetime.combine(day - timedelta(days=1), GLOBEX_OPEN), tz=ET_TZ)
    return open_et.tz_convert("UTC"), session_bounds_utc(day)[0]


def post_bounds_utc(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The post-RTH Globex hour: 16:00 → 18:00 ET *on the session date*, as UTC.

    16:00-17:00 is live trading (the last hour before the 17:00-18:00 maintenance
    halt); the halt returns no trades, so the fetched frame is ~one hour. This is
    the hour the rth (…→16:00) and on (18:00→…) segments left uncovered."""
    open_et = pd.Timestamp(datetime.combine(day, POST_OPEN), tz=ET_TZ)
    close_et = pd.Timestamp(datetime.combine(day, POST_CLOSE), tz=ET_TZ)
    return open_et.tz_convert("UTC"), close_et.tz_convert("UTC")


def day_bounds_utc(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The whole trading day — prev 18:00 ET → 18:00 ET — as UTC instants.

    Exactly the union of the on/rth/post windows, and exactly the boundary
    ``contract_for`` quantizes the roll to. That second property is what makes a
    single whole-day fetch safe: one range can never straddle two contracts, so
    the seam that motivated splitting the fetch in the first place cannot appear
    inside a day file.
    """
    return overnight_bounds_utc(day)[0], post_bounds_utc(day)[1]


def session_date_for(ts: pd.Timestamp) -> date:
    """Which ET session date an instant belongs to.

    The inverse of ``day_bounds_utc``: a session runs prev 18:00 → 18:00 ET, so
    anything at or after 18:00 belongs to the *next* session. This is what a live
    feed rolls on — and it rolls on the tick clock, never the wall clock, so the
    boundary is wherever the exchange says it is and a host whose clock has
    drifted still cuts the day in the right place.

    Saturday is unreachable: the week ends at Friday 17:00 ET and reopens Sunday
    18:00, so 18:00-Friday-onwards resolves forward to Monday, and the Sunday
    evening session is Monday's — which is exactly what ``overnight_bounds_utc``
    already assumes when it keys Monday's night to Sunday.
    """
    et = ts.tz_convert(ET_TZ)
    d = et.date()
    if et.time() >= GLOBEX_OPEN:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _cache_path(symbol: str, day: date, segment: str = "rth"):
    return TICK_CACHE_DIR / f"{symbol}_{day.isoformat()}_{segment}.parquet"


def _empty_marker(symbol: str, day: date, segment: str):
    """Sentinel recording that a segment's pull came back genuinely empty (an
    early close before the post hour, a holiday night). Only ever written for
    the on/post segments: an empty RTH pull stays uncached so the runner's
    broken-window guard can keep reading "an rth file that exists is a file
    with ticks". Without this, every run re-bought the same 13 empty post
    segments from Databento — ~7s of network each, ~90s of the startup stall."""
    return TICK_CACHE_DIR / f"{symbol}_{day.isoformat()}_{segment}.empty"


# --- roll -------------------------------------------------------------------


def rolls(contract: str) -> bool:
    """True for a root we roll ('NQ'); False for a symbol to fetch verbatim.

    Membership of CONTRACT_SPECS is the test, rather than "does this look like a
    root?", because rolling means asking Databento for a continuous symbol — and
    the only instruments we can honestly do that for are the ones we already keep
    specs for. Everything else is taken at face value: an exact contract ('NQZ5'),
    which is what every run made before the roll existed pinned and must keep
    replaying to, and a synthetic symbol under test.
    """
    return contract in CONTRACT_SPECS


def _roll_map_path(root: str):
    return TICK_CACHE_DIR / f"roll_{root}.json"


# The map is read once per process, not once per lookup: finalize() resolves a
# contract for every trade and the charts for every request, and that shouldn't
# be a disk read each time. Invalidated wherever the map is written.
_ROLL_CACHE: dict[str, dict] = {}


def _load_roll(root: str) -> dict:
    """The roll file: {"sessions": {date: symbol}, "closed": [dates]}.

    ``closed`` is the probe's negative evidence — weekdays with no RTH bars in the
    continuous series while later sessions have them, i.e. days the exchange never
    opened. An early flat-format file (dates -> symbol only) loads as sessions with
    nothing known to be closed.
    """
    if root not in _ROLL_CACHE:
        p = _roll_map_path(root)
        raw = json.loads(p.read_text()) if p.exists() else {}
        if "sessions" not in raw:
            raw = {"sessions": raw, "closed": []}
        _ROLL_CACHE[root] = raw
    c = _ROLL_CACHE[root]
    return {"sessions": dict(c["sessions"]), "closed": list(c["closed"])}


def _load_roll_map(root: str) -> dict[str, str]:
    return _load_roll(root)["sessions"]


def _probe_front_month(root: str, start: date, end: date) -> dict[str, str]:
    """Which raw contract was front month during each session's RTH, in [start, end].

    Sampled from Databento's own continuous symbol, so we inherit its volume roll
    rather than maintaining a roll calendar by hand. Two details carry the weight:

    - Only bars *inside RTH* are counted. The continuous symbol switches at 19:00
      ET, so a sample taken anywhere in the overnight could return either side of
      the roll; RTH (09:30-16:00 ET) sits wholly within one UTC day and is always
      unambiguous.
    - The result keys a whole session, overnight included. On the roll session the
      night is therefore bought from the *new* contract, an hour before Databento's
      own switch. That hour is real data — the new contract was trading, it just
      wasn't the volume leader yet — and it keeps the session on one contract.

    This is an ohlcv-1m pull (cents for a quarter), not a tick pull.
    """
    import databento as dbn

    key = databento_key()
    if key is None:
        raise DatabentoUnavailable("DATABENTO_API_KEY not set")

    data = dbn.Historical(key).timeseries.get_range(
        dataset=DATABENTO_DATASET, schema="ohlcv-1m", stype_in="continuous",
        symbols=[continuous_symbol(root)],
        start=session_bounds_utc(start)[0].to_pydatetime(),
        end=session_bounds_utc(end)[1].to_pydatetime(),
    )
    df = data.to_df(price_type="float", pretty_ts=True)
    if df.empty:
        return {}
    df = df.reset_index()
    ts = pd.to_datetime(df["ts_event"], utc=True)
    et = ts.dt.tz_convert(ET_TZ)
    df = df[(et.dt.time >= RTH_OPEN) & (et.dt.time < RTH_CLOSE)]
    if df.empty:
        return {}
    df = df.assign(day=et.dt.date)

    # The busiest instrument_id of each RTH — one value in practice, but a mode
    # rather than a first() means a stray print can't decide a whole session.
    per_day = df.groupby("day")["instrument_id"].agg(lambda s: s.mode().iat[0])
    ids = sorted({int(v) for v in per_day})
    res = dbn.Historical(key).symbology.resolve(
        dataset=DATABENTO_DATASET, symbols=[str(i) for i in ids],
        stype_in="instrument_id", stype_out="raw_symbol",
        start_date=start.isoformat(), end_date=(end + timedelta(days=1)).isoformat(),
    )
    sym = {int(k): v[0]["s"] for k, v in res["result"].items() if v}
    return {d.isoformat(): sym[int(i)] for d, i in per_day.items() if int(i) in sym}


def _next_weekday(d: date) -> date:
    """The next Mon-Fri after ``d`` — the day that would carry a session if one ran."""
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def ensure_roll_map(contract: str, start: date, end: date) -> dict[str, str]:
    """Resolve and cache the front month for every session in [start, end].

    Primes the map in one pull so ``contract_for`` is a dict lookup during the run
    loop. A verbatim symbol needs no map and buys nothing.
    """
    if not rolls(contract):
        return {}
    root = root_symbol(contract)
    data = _load_roll(root)
    have, closed = data["sessions"], set(data["closed"])
    want = [d for d in session_dates(start, end) if d.isoformat() not in have]
    if not want:
        return have

    have.update(_probe_front_month(root, want[0], want[-1]))
    # A weekday with no RTH bars gets no probe result. It is still recorded — as its
    # previous session's contract, else every later lookup would re-probe Databento
    # for a day that will never resolve, and charts call this per request. Whether
    # it is also *known closed* depends on where it sits: a quiet day with a later
    # session behind it is an exchange holiday (the data continues without it), but
    # a quiet day at the end of the probe could just be data not yet published, and
    # calling that "closed" would let a run silently skip a real session.
    last_seen = max(have) if have else None
    first_seen = min(have) if have else None
    for d in want:
        k = d.isoformat()
        if k in have:
            continue
        # A day sitting before a known session -> its empty probe is the calendar, not
        # missing data, so it is closed. Decided independently of whether a contract
        # can be put on it: closed means there was nothing to buy.
        is_closed = bool(last_seen and k < last_seen)
        prior = [v for kk, v in sorted(have.items()) if kk < k]
        if prior:
            have[k] = prior[-1]
        elif is_closed and first_seen and _next_weekday(d).isoformat() == first_seen:
            # No prior session to carry forward, yet this is the weekday immediately
            # before the first session ever probed and it came back empty — a holiday
            # at the map's leading edge (New Year's Day before a Jan-2 map start). Carry
            # the earliest known contract backward (the front month does not turn over a
            # holiday) so the day resolves, is never re-probed, and the run skips it as
            # closed instead of dying in contract_for. A day further back than one
            # session stays unresolved on purpose — it could be a real session whose
            # data we simply never fetched, and guessing a contract there would silently
            # simulate the wrong instrument.
            have[k] = have[first_seen]
        else:
            continue
        if is_closed:
            closed.add(k)

    TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _roll_map_path(root).write_text(json.dumps({
        "sessions": dict(sorted(have.items())),
        "closed": sorted(closed),
    }, indent=1))
    _ROLL_CACHE[root] = {"sessions": dict(have), "closed": sorted(closed)}
    return have


def contract_for(contract: str, day: date) -> str:
    """The raw contract a given session trades — the one call site for the roll.

    Holidays and half-days have no RTH bars and so no map entry; they carry the
    previous mapped session forward rather than re-probing, since a day with no
    RTH is a day with no trades either way.
    """
    if not rolls(contract):
        return contract
    root = root_symbol(contract)
    m = ensure_roll_map(contract, day, day)
    key = day.isoformat()
    if key in m:
        return m[key]
    prior = [v for k, v in sorted(m.items()) if k < key]
    if prior:
        return prior[-1]
    raise RuntimeError(
        f"cannot resolve the front-month contract for {root} on {day} — "
        f"no session in the roll map at or before it")


def contract_for_cached(contract: str, day: date) -> str | None:
    """``contract_for`` minus the probe: resolve from the roll map already on
    disk, or return None. This is the resolver for GET paths (the regime router)
    — a GET must never reach Databento, and a session the map has never seen has
    no cached ticks to describe anyway."""
    if not rolls(contract):
        return contract
    m = _load_roll_map(root_symbol(contract))
    key = day.isoformat()
    if key in m:
        return m[key]
    prior = [v for k, v in sorted(m.items()) if k < key]
    return prior[-1] if prior else None


def market_closed(contract: str, day: date) -> bool:
    """True when the roll probe saw no session at all that day — a full exchange
    holiday (Christmas), as opposed to a day whose ticks are merely missing.

    This is what lets the "no ticks" guard tell the two apart: an empty pull on a
    closed day is the calendar, an empty pull on any other day is a data problem
    and must still fail the run. Deliberately conservative — a quiet day at the
    probe's trailing edge is never called closed (see ensure_roll_map), and a
    pinned contract has no probe, so no day of its window is ever skippable.
    """
    if not rolls(contract):
        return False
    return day.isoformat() in _load_roll(root_symbol(contract))["closed"]


def estimate_cost(symbol: str, start: date, end: date) -> float:
    """USD cost of the trades pull for [start, end], before fetching anything.

    Priced over whole sessions (prev 18:00 → 18:00 ET), which is what a cold day
    now buys. The old signature quoted overnight→RTH-close and so under-quoted
    every run by the post hour."""
    import databento as dbn

    key = databento_key()
    if key is None:
        raise DatabentoUnavailable("DATABENTO_API_KEY not set")
    s, _ = day_bounds_utc(start)
    _, e = day_bounds_utc(end)
    return dbn.Historical(key).metadata.get_cost(
        dataset=DATABENTO_DATASET, schema="trades", stype_in="raw_symbol",
        symbols=[symbol], start=s.to_pydatetime(), end=e.to_pydatetime(),
    )


def _fetch_range(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Trade ticks for a raw contract symbol over [start, end)."""
    import databento as dbn

    key = databento_key()
    if key is None:
        raise DatabentoUnavailable("DATABENTO_API_KEY not set")

    data = dbn.Historical(key).timeseries.get_range(
        dataset=DATABENTO_DATASET, schema="trades", stype_in="raw_symbol",
        symbols=[symbol], start=start.to_pydatetime(), end=end.to_pydatetime(),
    )
    df = data.to_df(price_type="float", pretty_ts=True)
    if df.empty:
        return pd.DataFrame(columns=TICK_COLS)

    df = df.reset_index()
    # ts_event is the exchange's own stamp; ts_recv is when Databento saw it.
    # Sequencing the sim off ts_event keeps fills in true exchange order.
    ts_col = "ts_event" if "ts_event" in df.columns else "ts_recv"
    df = df.rename(columns={ts_col: "ts_utc"})
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    if "side" not in df.columns:
        df["side"] = "N"
    df = df[TICK_COLS]
    return df.sort_values("ts_utc", kind="stable").reset_index(drop=True)


@lru_cache(maxsize=8)
def _read_parquet_cached(symbol: str, day: date, segment: str = "rth") -> pd.DataFrame:
    """In-memory cache over the parquet read. Treat the result as READ-ONLY —
    the frame is shared with every other caller for the life of the process.

    Small maxsize on purpose: in the day layout this is a *staging* read that
    exists to be sliced by ``_read_segment_cached`` (which caches the slices), so
    holding many whole-day frames would keep ~4x the ticks any caller actually
    wants. The runner walks days in order, so a handful of entries still catches
    all three slices of a day from one read."""
    return pd.read_parquet(_cache_path(symbol, day, segment))


_SEGMENT_BOUNDS = {
    "on": overnight_bounds_utc,
    "post": post_bounds_utc,
    "rth": session_bounds_utc,
    "day": day_bounds_utc,
}

SEGMENTS = ("on", "rth", "post")

# Which window each tick belongs to, stored *in* the day file rather than
# re-derived from its timestamp on every read. See _assign_segments.
SEG_COL = "seg"


def _assign_segments(df: pd.DataFrame, day: date) -> pd.DataFrame:
    """Label every tick of a whole-day frame with its window.

    A three-way cut at the two interior boundaries, deliberately with no outer
    bound: the first and last ticks of the frame are whatever the fetch returned,
    and a rule that dropped ticks outside [18:00, 18:00) would silently lose the
    edges (see below for why they don't line up exactly). Every tick lands in
    exactly one window, so the labels always partition the day.
    """
    if df.empty:
        return df.assign(**{SEG_COL: pd.Series(dtype="object")})
    rth_open = session_bounds_utc(day)[0]
    post_open = post_bounds_utc(day)[0]
    ts = df["ts_utc"]
    seg = pd.Series("post", index=df.index, dtype="object")
    seg[ts < post_open] = "rth"
    seg[ts < rth_open] = "on"
    return df.assign(**{SEG_COL: seg})


def _slice_window(df: pd.DataFrame, day: date, segment: str) -> pd.DataFrame:
    """Carve one window out of a whole-day frame.

    Prefers the stored ``seg`` label over re-deriving the split from timestamps,
    because the two do not agree on the legacy cache. ``_fetch_range`` asks
    Databento for a range of ``ts_recv`` but stores and sorts on ``ts_event``,
    and ts_event <= ts_recv, so a window's first ticks can carry an event stamp a
    few milliseconds *before* the window it was fetched as. On the old
    three-request layout that makes rth and post genuinely overlap at 16:00 — on
    56% of cached days, by 1-7 ticks. Re-deriving the cut from ts_event would
    quietly pull those ticks back into RTH and change what every RTH strategy
    traded, so migration records where each tick actually came from and this
    reads the label. A single whole-day fetch has no interior seam to disagree
    about, so its labels and its timestamps say the same thing.

    The index is reset because a segment parquet reads back with a 0-based
    RangeIndex and callers position into these frames (``engine`` searchsorts for
    the RTH boundary and uses the result as an offset) — a sliced frame carrying
    its parent's index would put every one of those offsets out by the length of
    the overnight. ``seg`` is dropped so callers see exactly the columns a
    segment file has always given them.
    """
    if df.empty:
        return df.drop(columns=[SEG_COL], errors="ignore")
    if SEG_COL in df.columns:
        out = df[df[SEG_COL] == segment].drop(columns=[SEG_COL])
        return out.reset_index(drop=True)
    lo, hi = _SEGMENT_BOUNDS[segment](day)
    ts = df["ts_utc"]
    a = int(ts.searchsorted(lo, side="left"))
    b = int(ts.searchsorted(hi, side="left"))
    return df.iloc[a:b].reset_index(drop=True)


def _day_path(symbol: str, day: date):
    return _cache_path(symbol, day, "day")


def have_segment(symbol: str, day: date, segment: str) -> bool:
    """Is this window on disk in *either* layout? Never fetches."""
    return _day_path(symbol, day).exists() or _cache_path(symbol, day, segment).exists()


def day_complete(symbol: str, day: date) -> bool:
    """True when every window of this session is settled on disk — a day file, or
    all three segments present (counting a confirmed-empty marker as settled).

    This is the "would a run have to spend money here?" test.
    """
    if _day_path(symbol, day).exists():
        return True
    return all(_cache_path(symbol, day, s).exists()
               or _empty_marker(symbol, day, s).exists() for s in SEGMENTS)


def has_rth(symbol: str, day: date) -> bool:
    """Is a *non-empty* RTH window on disk? The runner's broken-window guard.

    Cheap enough to ask once per day of a window. The legacy layout answers from
    the filename alone — an empty RTH pull is never cached, so the file existing
    is the answer — and the day layout reads back only the label column instead
    of a million rows of ticks.
    """
    if _cache_path(symbol, day, "rth").exists():
        return True
    p = _day_path(symbol, day)
    if not p.exists():
        return False
    try:
        return bool((pd.read_parquet(p, columns=[SEG_COL])[SEG_COL] == "rth").any())
    except Exception:  # noqa: BLE001 — a day file written before seg existed
        return not _read_segment_cached(symbol, day, "rth").empty


def _any_segment(symbol: str, day: date) -> bool:
    """True when the legacy layout holds *part* of this session. Such a day is
    topped up window by window rather than re-bought whole — the bytes already on
    disk were paid for once already."""
    return any(_cache_path(symbol, day, s).exists()
               or _empty_marker(symbol, day, s).exists() for s in SEGMENTS)


@lru_cache(maxsize=96)
def _read_segment_cached(symbol: str, day: date, segment: str) -> pd.DataFrame:
    """One window, resolved across both layouts. READ-ONLY, like the parquet read
    it wraps. Cached at the *slice* so the day layout doesn't re-slice a
    million-row frame on every gate lookup."""
    if _cache_path(symbol, day, segment).exists():
        return _read_parquet_cached(symbol, day, segment)
    return _slice_window(_read_parquet_cached(symbol, day, "day"), day, segment)


def _read_day_parquet(symbol: str, day: date, segment: str = "rth") -> pd.DataFrame:
    """The parquet read, as callers outside this module have always known it.

    Kept as a plain function wrapping the cached one so that
    ``_read_day_parquet.cache_clear()`` — the invalidation every test and caller
    already reaches for — clears *both* layers. A second lru_cache that the
    existing call sites didn't know to clear would leave stale slices behind
    exactly where someone had carefully invalidated the read.
    """
    return _read_parquet_cached(symbol, day, segment)


def _clear_tick_caches() -> None:
    _read_parquet_cached.cache_clear()
    _read_segment_cached.cache_clear()
    _clear_live_caches()


_read_day_parquet.cache_clear = _clear_tick_caches  # type: ignore[attr-defined]


def _fetch_day(symbol: str, day: date) -> pd.DataFrame:
    """Buy the whole session in one range and cache it as one parquet.

    An empty pull is not cached: a day with no ticks is either a holiday (the
    roll probe already knows, see ``market_closed``) or a data problem the run
    must fail on, and writing a file for it would make the difference
    unreadable afterwards."""
    df = _fetch_range(symbol, *day_bounds_utc(day))
    if not df.empty:
        df = _assign_segments(df, day)
        TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(_day_path(symbol, day), index=False)
    return df


def _get_segment(symbol: str, day: date, segment: str, use_cache: bool) -> pd.DataFrame:
    """One window, fetching what isn't cached.

    Resolution order matters: a legacy segment file wins over the day file (it is
    the same ticks, already narrowed), the day file is sliced when it is what's
    there, and only a session with nothing on disk reaches Databento — where it
    buys the *whole* day, not the one window asked for, because that is the
    layout going forward and the extra two windows are what every chart and the
    weekly seed would have had to buy separately anyway.
    """
    if use_cache:
        if _cache_path(symbol, day, segment).exists() or _day_path(symbol, day).exists():
            return _read_segment_cached(symbol, day, segment)
        if _empty_marker(symbol, day, segment).exists():
            return pd.DataFrame(columns=TICK_COLS)
        # A part-cached legacy day: top up this window alone rather than re-buying
        # the night (or the RTH) sitting next to it.
        if _any_segment(symbol, day):
            return _get_legacy_segment(symbol, day, segment)
    df = _fetch_day(symbol, day)
    return _slice_window(df, day, segment)


def _get_legacy_segment(symbol: str, day: date, segment: str) -> pd.DataFrame:
    """Fetch and cache a single window in the old per-segment layout.

    Only reached for a session the old layout already partly holds. Keeps the
    original empty-marker behaviour: an empty RTH pull stays uncached so the
    runner's broken-window guard still reads "an rth file that exists is a file
    with ticks", while an empty on/post is recorded so it is never re-bought."""
    cache = _cache_path(symbol, day, segment)
    df = _fetch_range(symbol, *_SEGMENT_BOUNDS[segment](day))
    TICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        df.to_parquet(cache, index=False)
    elif segment != "rth":
        _empty_marker(symbol, day, segment).touch()
    return df


def get_day_ticks(symbol: str, day: date, use_cache: bool = True,
                  include_overnight: bool = False) -> pd.DataFrame | None:
    """Trade ticks for one session — RTH only by default; with
    ``include_overnight`` the Globex segment (prev day 18:00 ET → 09:30 ET) is
    spliced in front, each segment fetched and cached independently.

    Unlike ``databento_client.get_day_bars`` this does **not** swallow fetch
    errors: a sim that silently skips a day would report metrics over a window
    it never actually tested.
    """
    rth = _get_segment(symbol, day, "rth", use_cache)
    if not include_overnight:
        return rth
    on = _get_segment(symbol, day, "on", use_cache)
    if on.empty:
        return rth
    # Both segments are sorted and the windows meet end-exclusive at 09:30,
    # so concatenation is already globally ordered with no duplicate tick.
    # Note the post hour is deliberately absent even here: a Globex strategy is
    # anchored at 18:00 and trades to the bell, and appending 16:00-18:00 would
    # extend every session past the close it was written against.
    return pd.concat([on, rth], ignore_index=True)


def ensure_day(symbol: str, day: date) -> bool:
    """Buy and cache the whole session — night, RTH, and the post hour — in one
    range. True if the session is settled on disk afterwards.

    This is the one place a cold session is paid for. It replaces the old pair of
    ``ensure_overnight`` / ``ensure_post`` calls (kept below for callers that want
    one window): the run form used to choose whether to buy the night, and now a
    run always buys the day, because the default already did and the charts, the
    weekly seed and every cross-session anchor were the things going without.

    Errors propagate, unlike the two helpers below. On a cold session there is
    nothing to degrade *to* — a run whose RTH never arrived cannot report metrics
    over a window it did not test, and the runner's guard turns that into a
    failed run. On a part-cached legacy session the top-up is best-effort for the
    night and the post hour exactly as it was, so a Databento outage still leaves
    the run standing on the RTH it already had.
    """
    if day_complete(symbol, day):
        return True
    if _any_segment(symbol, day):
        for seg in SEGMENTS:
            if _cache_path(symbol, day, seg).exists() or _empty_marker(symbol, day, seg).exists():
                continue
            try:
                _get_legacy_segment(symbol, day, seg)
            except Exception:  # noqa: BLE001 — garnish for a day we can already trade
                if seg == "rth":
                    raise
        return day_complete(symbol, day)
    _fetch_day(symbol, day)
    return _day_path(symbol, day).exists()


def ensure_overnight(symbol: str, day: date) -> bool:
    """Buy and cache the Globex segment for a session, whether or not the engine
    reads it. True if the night is on disk afterwards.

    An RTH strategy simulates on RTH ticks alone — that must not change — but its
    charts want the overnight in front of the session (the Globex-anchored VWAP,
    the night's candles, the full-day profile), and charts never fetch. So a run
    pays for the night up front and every chart of that run is drawn against the
    same data as a globex strategy's. Turned off by the run form's "NY session
    only" box, for when the RTH ticks are all you want to pay for.

    Failure is not fatal, which is the one way this differs from every other fetch
    here: the night is context for the charts, not an input to the rules. A run
    whose RTH ticks are all present must not land in 'error' because Databento was
    unreachable for the garnish — it simulated exactly the trades it claims. The
    session just gets drawn without its overnight, as it was before this existed.

    Answered from disk when it can be: a cached night (or a confirmed-empty one)
    is already ensured, and reading the parquet back just to prove it exists was
    ~2.7s of thrown-away I/O per run across a full window.
    """
    if have_segment(symbol, day, "on"):
        return True
    if _empty_marker(symbol, day, "on").exists():
        return False
    try:
        _get_segment(symbol, day, "on", use_cache=True)
    except Exception:  # noqa: BLE001 — see above: the charts degrade, the run stands
        return False
    return have_segment(symbol, day, "on")


def ensure_post(symbol: str, day: date) -> bool:
    """Buy and cache the post-RTH Globex hour (16:00→18:00 ET) for a session.

    The twin of ``ensure_overnight``: context the charts and the weekly seed want
    but the rules never read, so failure is not fatal — a run whose RTH ticks are
    all present must not error because Databento was unreachable for this hour. It
    just leaves the 16:00-17:00 hour out of any cross-session anchor for that day,
    exactly as it was before this segment existed. Like ensure_overnight, answered
    from disk when a cached or confirmed-empty hour already settles it."""
    if have_segment(symbol, day, "post"):
        return True
    if _empty_marker(symbol, day, "post").exists():
        return False
    try:
        _get_segment(symbol, day, "post", use_cache=True)
    except Exception:  # noqa: BLE001 — see ensure_overnight: garnish, not an input
        return False
    return have_segment(symbol, day, "post")


# --- the live store ---------------------------------------------------------
#
# Recorded days live under LIVE_TICK_DIR/{SYMBOL}/{DATE}/ as a set of sealed
# chunk parquets. Three properties carry the design, and all three exist so a
# reader and a writer can share a directory with no coordination:
#
#   - a chunk is IMMUTABLE once named. The recorder writes to a temp file and
#     renames, so a chunk is either absent or complete — never half-read.
#   - the DIRECTORY IS THE TRUTH, not the manifest. ``session.json`` is a
#     heartbeat (is the recorder alive, when did it last see a print); a chunk on
#     disk counts whether or not the manifest has caught up with it. That removes
#     the whole ordering hazard of "which file do I update first".
#   - chunk names sort in write order, so the concatenation is already the tape.
#
# Reads are cached on the chunk *set*, so a day that grows invalidates itself
# without anyone having to remember to clear anything — the failure the sums
# cache and the segment LRU both had to be designed against.

_LIVE_CHUNK_GLOB = "[0-9]*.parquet"


def live_day_dir(symbol: str, day: date):
    return LIVE_TICK_DIR / symbol / day.isoformat()


def live_chunks(symbol: str, day: date) -> tuple[str, ...]:
    """The sealed chunks of a recorded day, in tape order. () if there are none."""
    d = live_day_dir(symbol, day)
    if not d.is_dir():
        return ()
    return tuple(sorted(p.name for p in d.glob(_LIVE_CHUNK_GLOB)))


def have_live_day(symbol: str, day: date) -> bool:
    """Is any of this session recorded? Never touches the Databento cache."""
    return bool(live_chunks(symbol, day))


@lru_cache(maxsize=1024)
def _live_span_cached(symbol: str, day: date,
                      chunks: tuple[str, ...]) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """The (first, last) recorded instant, folded over every chunk's footer.

    EVERY chunk, not the first and the last. Chunk names sort in *write* order,
    and that stopped being time order when the feed learned to backfill — the
    same caveat ``_read_live_cached`` sorts for. The hull of a set is not the
    hull of its endpoints once the endpoints can be out of order.

    A chunk whose footer carries no statistics, or will not open, is skipped
    rather than guessed at; if that is every chunk the answer is None, and a
    caller that wanted a window gets "cannot say" instead of "no".
    """
    d = live_day_dir(symbol, day)
    lo = hi = None
    for c in chunks:
        try:
            pf = pq.ParquetFile(d / c)
            i = pf.schema_arrow.names.index("ts_utc")
            md = pf.metadata
            for g in range(md.num_row_groups):
                st = md.row_group(g).column(i).statistics
                if st is None:
                    continue
                a, b = pd.Timestamp(st.min), pd.Timestamp(st.max)
                lo = a if lo is None or a < lo else lo
                hi = b if hi is None or b > hi else hi
        except Exception:  # noqa: BLE001 — one bad chunk must not sink the day
            continue
    return None if lo is None or hi is None else (lo, hi)


def live_day_span(symbol: str, day: date) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """First and last recorded instant of a session, or None if nothing is.

    Read from the parquet footers, never the ticks: this backs a *listing*, and
    a listing that opened every chunk of every recorded day would read hundreds
    of megabytes to answer a question about time spans. Cached on the chunk set,
    like every other read of this store, so a day that grows invalidates its own
    entry.

    A hull, not a coverage test. It says where the tape starts and ends, and
    says nothing about holes in between — ``/live/recordings`` owns that
    question, and owns it with a manifest rather than a guess.
    """
    chunks = live_chunks(symbol, day)
    if not chunks:
        return None
    return _live_span_cached(symbol, day, chunks)


@lru_cache(maxsize=8)
def _read_live_cached(symbol: str, day: date, chunks: tuple[str, ...]) -> pd.DataFrame:
    """A recorded day, concatenated. READ-ONLY, like every other cached read.

    ``chunks`` is part of the key rather than derived inside: it is what makes a
    growing day invalidate its own cache entry, and passing it in means the
    caller's view of the directory and the frame it gets back are the same view.

    SORTED, NOT MERELY CONCATENATED. Chunk names sort in *write* order, and that
    stopped being time order when the feed learned to backfill: connect at 07:08
    and a chunk of 07:08 prints is written; reconnect later and the replay of the
    night from 18:00 is written *after* it. Both are the day, and the day is
    their union in time order — so the sort is what the file names cannot say.
    Stable, so prints sharing a stamp keep the order they were recorded in, and
    cheap in practice: the read is cached on the chunk set, so it happens once
    per change rather than once per caller.
    """
    d = live_day_dir(symbol, day)
    parts = [pd.read_parquet(d / c) for c in chunks]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=TICK_COLS)
    df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    if not df["ts_utc"].is_monotonic_increasing:
        df = df.sort_values("ts_utc", kind="stable", ignore_index=True)
    return df[[c for c in TICK_COLS if c in df.columns]]


def live_day_ticks(symbol: str, day: date) -> pd.DataFrame | None:
    """Everything recorded for a session — night, RTH and post hour, in order.

    None when nothing was recorded. This is the raw store; the segment readers
    below are what the engine and the charts see.
    """
    chunks = live_chunks(symbol, day)
    if not chunks:
        return None
    df = _read_live_cached(symbol, day, chunks)
    return None if df.empty else df


def live_segment(symbol: str, day: date, segment: str) -> pd.DataFrame | None:
    """One window of a recorded day, or None if nothing recorded falls in it.

    The cut is derived from the timestamps, with no stored ``seg`` label — and
    unlike the Databento cache that is not a shortcut. The label exists there
    because three separately-fetched windows disagreed at their seams (see
    ``_slice_window``); a recorded day is one time-ordered stream with no
    interior seam to disagree about, so the boundary IS the timestamp.
    """
    df = live_day_ticks(symbol, day)
    if df is None:
        return None
    out = _slice_window(df, day, segment)
    return None if out.empty else out


def _clear_live_caches() -> None:
    _read_live_cached.cache_clear()
    _live_span_cached.cache_clear()


def cached_rth(symbol: str, day: date) -> pd.DataFrame | None:
    """The RTH segment, but only if it is already on disk — never a fetch.

    ``get_day_ticks`` buys what it doesn't find, which is right for a run and
    wrong for anything served from a GET. The regime KPIs are read straight off
    the tick cache by the charts, so they need the read half without the wallet.

    Falls back to the live store, and in that order: a day that was *bought*
    reads as the bought day even if it was also recorded. That direction is the
    load-bearing one — it means recording a session can never change what a
    backtest over that session says, and it leaves the Databento corpus as the
    independent reference Phase 6 reconciles against.
    """
    if not have_segment(symbol, day, "rth"):
        return live_segment(symbol, day, "rth")
    df = _read_segment_cached(symbol, day, "rth")
    return None if df.empty else df


def cached_overnight(symbol: str, day: date) -> pd.DataFrame | None:
    """The overnight segment, but *only* if it is already on disk — never a fetch.

    This exists for the charts. An RTH strategy's chart draws the Globex-anchored
    VWAP as context alongside the NY one it actually traded, and that needs the
    night in front of the session. But a chart is a GET: it must never be the
    thing that spends money at Databento. So a session whose overnight was never
    bought simply doesn't get the second anchor drawn, and nothing is bought to
    draw it. Runs are where ticks get paid for; charts only ever read.

    Falls back to the live store — see ``cached_rth`` for why in that order, and
    note this is the read the ten ``gx_*`` gate sites make. They blind-fail-closed
    on a missing night, so on a live day with nothing behind this call every
    Globex strategy vetoes everything and says nothing about why. That is the
    single reason ticks-on-disk could not be cut from Phase 5.
    """
    if not have_segment(symbol, day, "on"):
        return live_segment(symbol, day, "on")
    df = _read_segment_cached(symbol, day, "on")
    return None if df.empty else df


def cached_post(symbol: str, day: date) -> pd.DataFrame | None:
    """The post-RTH Globex hour (16:00→18:00 ET), only if already on disk — never
    a fetch. Backs the weekly seed and full-day charts from a GET; a session whose
    post hour was never bought simply contributes on+rth to its cross-session
    anchors, as before. Falls back to the live store, Databento first — see
    ``cached_rth``."""
    if not have_segment(symbol, day, "post"):
        return live_segment(symbol, day, "post")
    df = _read_segment_cached(symbol, day, "post")
    return None if df.empty else df


def session_dates(start: date, end: date) -> list[date]:
    """Weekdays in [start, end]. Holidays surface as empty tick pulls."""
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days
