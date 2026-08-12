"""Live shadow mode — the session in progress, and what the shelf makes of it.

Three reads and two writes. The reads are what the Charts → Live surface is
built on:

  - ``/live/session``  the header: contract, session date, the two bell instants,
    the Globex anchor and the weekly seed — everything about the day that is not
    a tick.
  - ``/live/tape``     rows [since, n), delta-encoded in the same bytes the
    Simulator's session endpoint ships (``api.tape_codec``), so the client
    decodes a growing tape with the function it already had.
  - ``/live/signals``  where each registered strategy would have signalled, as
    prefix re-runs of the very ``run_session`` the backtest calls.
  - ``/live/history``  whole *prior* sessions, in those same bytes, so the days
    behind the one in progress can be drawn to the left of it. The live chart
    otherwise holds exactly one session and scrolling back runs out of tape at
    the Globex open.

The writes start and stop a feed, and set the two modes it runs in. There are
two feeds, and which one is running decides what *can* be written down:

  - the **fake feed** replays a cached Databento day into memory at wall-clock
    speed and records nothing — its source is already a file on disk, and
    recording it would manufacture a "live" day out of a replayed one;
  - the **Rithmic feed** connects the ticker plant (market data only, never the
    order plant) and records every print to ``data/live/ticks/``, because ten
    ``gx_*`` gate sites and the weekly seed read a session's earlier windows off
    disk and blind-fail-closed when they are not there.

``/live/modes`` turns those writes and the shadow shelf on and off while a
session runs — two switches, not one, because they fail differently. It refuses
the one pair that would produce a plausible wrong answer rather than an obvious
absence: the shelf running over a live feed with nothing on disk behind it. The
reasoning is in ``journal.live.state.check_modes``.

THE GEN TOKEN. The tape poll is incremental: the client says which rows it
already holds and gets what came after them. Those row indices only mean anything
against the session they were read from, so every request carries the token and
every response answers with one. A mismatch is answered with ``reset: true`` and
the whole tape, never with a block that would splice two days into one chart.

NO ORDER ROUTING **IN THIS ROUTER**, and that is now a load-bearing distinction
rather than a blanket claim. Nothing here can send an order; everything that can
is in ``live_orders.py``, behind its own env flag, its own arm and a two-step
confirm. The one seam between them is the ``routing`` flag on
``/live/feed/rithmic``, which decides whether the connection opens Rithmic's
ORDER plant at all — it has to be decided here because one login means one
socket, and the order path rides the tick feed's.

Nothing in Phases 0-6 is shaped for it: the shadow shelf cannot reach the broker
(``shadow.py`` imports nothing from it), routing is manual-only, and the two
recording modes are unchanged. See docs/live-shadow-plan.md § Phase 7.
"""

from __future__ import annotations

import asyncio
import json
import os
import time as timemod
from datetime import date, datetime, time, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from journal import live as livemod
from journal.config import DEFAULT_DISPLAY_TZ, ET_TZ, contract_spec, root_symbol
from journal.live import harvest as harvestmod
from journal.live import journal as jourmod
from journal.live import recorder as recmod
from journal.sim import ib as ibmod
from journal.sim import ticks as tickmod
from journal.sim import weekly as weeklymod

from ..tape_codec import encode_ticks, local_ms, zone_for

router = APIRouter()

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


def _wall_ms(day: date, t: time, zone) -> int:
    """Epoch-ms of an ET session wall-clock time, projected into ``zone``."""
    et = pd.Timestamp(datetime.combine(day, t), tz=ET_TZ)
    return int(pd.Timestamp(et.tz_convert(zone).tz_localize(None)).value // 1_000_000)


def _running() -> livemod.state.Live:
    live = livemod.current()
    if live is None:
        raise HTTPException(404, "no live session is running")
    return live


@router.get("/live/status")
def live_status() -> dict:
    """Whether anything is running, and enough to say what. Never 404s — the
    Live tab asks this before it knows there is a session to ask about."""
    live = livemod.current()
    if live is None:
        return {"running": False}
    s = live.session
    last = s.last_ts()
    feed = live.feed
    rec = live.recorder
    return {
        "running": True,
        "gen": s.gen,
        "symbol": s.symbol,
        "date": s.day.isoformat(),
        "rows": s.n,
        "closed": s.closed,
        # A resumed session has a tape and no feed: it is whole up to the
        # restart and is not growing. Said plainly rather than left to look like
        # a quiet market.
        "source": live.source if feed is not None else "resumed",
        "feed_running": bool(feed is not None and feed.running),
        "speed": getattr(feed, "speed", None),
        "last_tick_utc": None if last is None else last.isoformat(),
        "recording": rec is not None,
        "recorded_rows": None if rec is None else rec.rows,
        # The two switches, and the one number that says what turning the first
        # one off has cost so far. `unrecorded` only ever moves on a live feed:
        # the fake one has no recorder to be missing.
        "signals": live.shadow.enabled,
        "journalling": live.shadow.journal is not None,
        "unrecorded_rows": live.unrecorded,
        "can_record": live.source != "fake",
        # Whether this session's connection opened the ORDER plant. Here so the
        # page can decide whether to offer the routing panel at all without a
        # second request, and false for every session that is not a routing one
        # — which is all of them by default.
        "routing": live.broker is not None,
        "feed_status": feed.status() if hasattr(feed, "status") else None,
    }


@router.post("/live/modes")
def live_modes(
    record: bool | None = Query(None, description="write the tape to data/live/"),
    signals: bool | None = Query(None, description="run the shelf over the day"),
) -> dict:
    """Turn recording and the shadow shelf on or off under a running session.

    Omitting a parameter leaves that mode alone, so either can be set without
    knowing the other's current value.

    422 is a refusal, not a failure, and there are two of them
    (``state.check_modes`` carries the reasoning). The load-bearing one is
    **signals on with recording off on a live feed**: the ``gx_*`` gates read the
    session's earlier windows off disk, so with nothing being written they veto
    everything and say nothing about why — a plausible wrong answer, which is
    exactly what this stack refuses to serve. Turning the shelf off as well is
    always allowed; so is recording with the shelf off.
    """
    try:
        live = livemod.set_modes(record=record, signals=signals)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {
        "gen": live.session.gen,
        "recording": live.recording,
        "signals": live.signals,
        "journalling": live.shadow.journal is not None,
        "unrecorded_rows": live.unrecorded,
    }


@router.post("/live/feed/start")
def live_feed_start(
    symbol: str = Query(..., description="Raw contract, e.g. NQU6"),
    date_: str = Query(..., alias="date"),
    speed: float = Query(1.0, gt=0, le=3600),
    start_at: str | None = Query(None, description="ET wall clock, e.g. 09:25"),
) -> dict:
    """Replay a cached session into memory as though it were arriving now.

    ``start_at`` opens the session part-way through: everything before it is
    published in one batch, which is what a live day looks like when you load the
    page mid-morning. ``speed`` multiplies tape time against wall time — 1.0 is
    as long as the day actually took.

    Read-only over the tick cache. A day that was never bought is a 404, never a
    fetch: picking a day must not spend money.
    """
    day = date.fromisoformat(date_)
    at = None
    if start_at:
        h, m = (int(x) for x in start_at.split(":")[:2])
        at = time(h, m)
    try:
        live = livemod.start(symbol, day, speed=speed, start_at=at)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return {"gen": live.session.gen, "symbol": symbol, "date": date_,
            "speed": speed, "start_at": start_at,
            "skipped": live.shadow.skipped}


@router.post("/live/feed/rithmic")
def live_feed_rithmic(
    symbol: str = Query(..., description="RAW contract, e.g. NQU6 — never a root"),
    exchange: str = Query("CME"),
    backfill: bool = Query(True, description="replay the session so far on connect"),
    record: bool = Query(True, description="write the tape to data/live/"),
    signals: bool = Query(True, description="run the shelf over the day"),
    routing: bool = Query(False, description="also open the ORDER plant"),
) -> dict:
    """Connect the real ticker plant and start recording the session.

    ``symbol`` must be the raw contract. A root would send ``contract_for`` to
    probe Databento — which a live path must never do — and the on-disk roll map
    ends 2026-06-30 regardless, so there is nothing there to resolve against.

    Market data by default: the feed opens ``TICKER_PLANT`` — plus
    ``HISTORY_PLANT`` when backfilling — rather than taking the client's
    all-four default (docs/live-shadow-plan.md decision 2).

    ``routing=true`` is the one thing that opens ``ORDER_PLANT`` (and
    ``PNL_PLANT``, for the position), and it is settled **here**, at connect,
    because Rithmic allows one session per login and the order path therefore
    has to ride this same socket. It is deliberately not a runtime switch like
    the two below: a shadow session cannot grow the ability to trade under a
    page somebody is already watching. Refused with 422 unless the environment
    allows routing — see ``journal.live.routing`` and the ``/live/routing``
    endpoints, which are in a different router precisely so that the sentence
    at the top of this file stays true.

    ``backfill`` replays the session from its 18:00 ET open before the live
    stream starts, so connecting at nine in the morning gives a whole session
    rather than one that begins at nine. It returns as soon as the *feed* is
    started; the replay lands a few seconds later and shows up on
    ``/live/status`` under ``feed_status.backfills``, which is also where a
    backfill that failed says so.

    ``record`` and ``signals`` open the connection in a mode rather than
    correcting it afterwards; they are the same two switches ``/live/modes``
    toggles and obey the same refusal (see there).
    """
    if symbol.upper() in {"NQ", "ES", "CL", "GC"} or len(symbol) < 4:
        raise HTTPException(
            422, f"{symbol!r} looks like a root — pin the raw contract (e.g. NQU6)")
    try:
        live = livemod.start_rithmic(symbol.upper(), exchange, backfill=backfill,
                                     record=record, signals=signals,
                                     routing=routing)
    except LookupError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"gen": live.session.gen, "symbol": live.session.symbol,
            "date": live.session.day.isoformat(), "source": live.source,
            "recording": live.recording, "signals": live.signals,
            # Whether the ORDER plant was opened. Read off the session rather
            # than echoed back from the request: the two agree here, and the one
            # worth reporting is the one that is true.
            "routing": live.broker is not None,
            "backfill": backfill, "skipped": live.shadow.skipped}


@router.post("/live/feed/stop")
def live_feed_stop() -> dict:
    return {"stopped": livemod.stop()}


def _kind_of(man: dict, slugs: list[str]) -> str:
    """How a recorded day was come by, from what survives on disk.

    Four answers, in the order the evidence is trustworthy. The distinction is
    the one ``journal.live.harvest`` names in its docstring and it matters to a
    reader of the data, not just to the UI: a harvested day has **no signal
    journal** (nothing recorded what the shelf believed) and carries **Rithmic's
    clock** rather than the exchange's, a median 287µs later.

      - a signal journal exists -> the shelf ran, so somebody watched it;
      - else a ``shadow`` mark -> a *session* wrote this manifest (only
        ``state._new_session`` and ``set_modes`` set that), with the shelf off;
      - else ``source: "harvest"`` -> the sweep wrote it and nobody watched;
      - else unknown, and said so.

    That last case is real rather than defensive: before the mark was carried
    forward, a watched day that was later gap-filled had its ``shadow`` mark
    overwritten by the sweep's own heartbeat. Days recorded before that fix can
    still land here, and guessing "harvested" for them would put a clock claim on
    a day that does not deserve one.
    """
    harvested = man.get("source") == "harvest"
    if slugs:
        return "filled" if harvested else "watched"
    if "shadow" in man:
        return "watched"
    return "harvest" if harvested else "unknown"


@router.get("/live/recordings")
def live_recordings(symbol: str | None = Query(None)) -> dict:
    """Every recorded session in the live store, newest first.

    Still its own endpoint now that ``/simulator/days`` carries a source flag,
    because it answers a different question. That list is "what can I replay",
    and holds a recorded day to a covered RTH window; this one is "what is on
    disk", and a half-recorded session, a harvest that truncated and a day with
    nothing in it are all things it exists to show. A session can appear here
    and not there, and that gap is the reporting, not a bug.

    ``contracts`` carries the one deadline in the live stack per symbol in the
    store: Rithmic replays a *listed* contract back ~120 days and an expired one
    not at all, so whatever is still missing when the contract rolls is missing
    for good. It is computed here rather than on the client because it is
    arithmetic over a measured property of the service, not a display choice.

    Everything read here is a directory listing, a manifest and a glob — no tick
    file is opened — because this is polled by a page.
    """
    days = recmod.recorded_days(symbol)
    out = []
    for sym, day in reversed(days):
        man = recmod.read_manifest(sym, day) or {}
        stats = man.get("stats") or {}
        # Which strategies were journalled — the durable evidence that the shelf
        # ran over this day, and the thing Phase 6's prefix check consumes. Empty
        # is an honest absence on a harvested day, not a gap in this list.
        slugs = jourmod.slugs(sym, day)
        out.append({
            "symbol": sym,
            "date": day.isoformat(),
            "chunks": len(tickmod.live_chunks(sym, day)),
            "rows": man.get("rows"),
            "closed": man.get("closed"),
            "last_tick_utc": man.get("last_tick_utc"),
            "updated_at": man.get("updated_at"),
            "stats": stats,
            "kind": _kind_of(man, slugs),
            "shadow": man.get("shadow"),
            "signals": slugs,
            "harvest": man.get("harvest"),
            # Pulled out of `stats` rather than left for a reader to find: an
            # out-of-order exchange stamp that had to be pushed forward is a
            # finding about the feed once it is not tiny, and it was previously
            # only readable by opening a JSON file.
            "clamped": stats.get("clamped", 0),
            "unrecorded_rows": stats.get("unrecorded_rows", 0),
        })
    recorded: dict[str, set[date]] = {}
    for sym, day in days:
        recorded.setdefault(sym, set()).add(day)
    # The contract being recorded now counts even with nothing on disk yet —
    # that is precisely the state in which a deadline is worth reading.
    want = (symbol or os.environ.get("LIVE_SYMBOL", "")).strip().upper()
    if want:
        recorded.setdefault(want, set())
    contracts = [harvestmod.replay_window(s, recorded[s]) for s in sorted(recorded)]
    return {"recordings": out, "contracts": contracts}


"""How many prior sessions one page may ask for. A ceiling, not a default: each
day is a whole tape to read, encode and ship, and the client's own cache holds
eight."""
HISTORY_MAX_DAYS = 10

"""How far back the walk will look to find them. A week of sessions inside a
month of weekdays tolerates a holiday and a short gap; past that the answer
"there isn't a week of tape behind this day" is the useful one, and the caller
gets what was found rather than a longer and longer search."""
HISTORY_LOOKBACK = 30


def _history_source(symbol: str, day: date) -> str | None:
    """Which store can answer for this session — ``"cache"``, ``"live"``, None.

    Cache first, live second: the same order ``journal.sim.weekly.session_sums``
    resolves the weekly seed in, and for the same reason. The two stores overlap
    (the Databento cache is pinned at the budget, the live one starts wherever
    recording did), and a day held in both has to draw the same bars whichever
    surface asks for it. One rule, in one place, or the Simulator and the live
    chart quietly disagree about a Tuesday.

    The order is not hypothetical either. The **fake feed replays a cached day**,
    so a week of context behind a simulated session lives entirely in the cache,
    while a week behind a Rithmic session lives entirely in the live store. Both
    paths are ordinary.
    """
    if tickmod.has_rth(symbol, day):
        return "cache"
    if tickmod.have_live_day(symbol, day):
        return "live"
    return None


@router.get("/live/history/days")
def live_history_days(
    symbol: str = Query(..., description="Raw contract, e.g. NQU6"),
    date_: str = Query(..., alias="date", description="The session to look behind"),
    days: int = Query(5, ge=0, le=HISTORY_MAX_DAYS),
) -> dict:
    """The prior sessions with tape behind ``date``, oldest first.

    Answered here rather than in the client because it is the one question that
    needs both stores in view at once, and because the client cannot see a hole
    without opening a file.

    **The holes are reported, not skipped silently.** 46 of the reachable
    sessions in the live store have nothing recorded, in one contiguous block —
    so a week walked back from today is routinely a week of *calendar* covering
    fewer sessions of *tape*. Gluing what was found straight together draws a
    continuous chart out of a discontinuous week, which is exactly the lie
    ``/live/recordings``' coverage strip exists to prevent; ``missing`` is how
    the chart gets to say so.

    ``missing`` covers only the span actually returned — weekdays between the
    oldest day found and the session itself. Weekdays older than that were never
    part of the answer, so calling them missing would be inventing a gap.

    Same contract only, and by construction: a roll would splice two price series
    a hundred points apart. Walking back from one symbol can never cross one.
    """
    day = date.fromisoformat(date_)
    found: list[dict] = []
    skipped: list[str] = []
    probe = day
    for _ in range(HISTORY_LOOKBACK):
        if len(found) >= days:
            break
        probe -= timedelta(days=1)
        if probe.weekday() >= 5:
            continue
        src = _history_source(symbol, probe)
        if src is None:
            skipped.append(probe.isoformat())
            continue
        found.append({"date": probe.isoformat(), "source": src})
    found.reverse()
    # Trim the skips to the window that was actually returned. `found` is
    # oldest-first now, so anything older than its first entry is outside it.
    if found:
        skipped = [d for d in skipped if d > found[0]["date"]]
    elif days:
        # Nothing found at all: the whole walk is the window, and every weekday
        # in it is a genuine hole. Reported oldest-first like the days are.
        skipped = list(reversed(skipped))
    return {
        "symbol": symbol,
        "date": date_,
        "requested": days,
        "days": found,
        "missing": sorted(skipped) if found else skipped,
    }


@router.get("/live/history/session")
def live_history_session(
    symbol: str = Query(..., description="Raw contract, e.g. NQU6"),
    date_: str = Query(..., alias="date"),
    tz: str | None = Query(None),
) -> dict:
    """One whole prior session, delta-encoded — a context day for the live chart.

    The same bytes ``/simulator/session`` and ``/live/tape`` ship
    (``api.tape_codec``), and the same shape the Simulator's context loader
    already consumes, which is the entire reason this is four lines of encoding
    rather than a second chart: a finished day, a growing day and a day being
    replayed are one wire format and one decoder.

    Real ticks, so a context day candles on any timeframe and profiles off the
    tape exactly as the session does — never bars reconstructed from a summary.

    It cannot be played, only drawn. There is no ``default_start_ms`` here and no
    transport on the surface that asks for it; the live clock is the feed's.
    """
    day = date.fromisoformat(date_)
    zone = zone_for(tz)
    source = _history_source(symbol, day)
    if source is None:
        raise HTTPException(404, f"No tape for {symbol} on {date_} in either store")

    if source == "cache":
        # on (prev 18:00 -> 09:30) | rth | post (16:00 -> 18:00), contiguous and
        # each internally sorted — the same splice `/simulator/session` makes, so
        # the two surfaces draw a cached day identically.
        parts = [f for f in (tickmod.cached_overnight(symbol, day),
                             tickmod.cached_rth(symbol, day),
                             tickmod.cached_post(symbol, day))
                 if f is not None and not f.empty]
        frame = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    else:
        # The live store keeps a day whole and in tape order already — night, RTH
        # and post hour are chunks of one recording, not three reads.
        frame = tickmod.live_day_ticks(symbol, day)

    if frame is None or frame.empty:
        raise HTTPException(404, f"No ticks for {symbol} on {date_}")

    spec = contract_spec(symbol)
    tape = encode_ticks(frame, zone, float(spec["tick_size"]))
    t_ms = local_ms(frame["ts_utc"], zone)
    return {
        "symbol": symbol,
        "root": root_symbol(symbol),
        "date": date_,
        "tz": tz or DEFAULT_DISPLAY_TZ,
        "tick_size": float(spec["tick_size"]),
        "point_value": float(spec["point_value"]),
        # Which store answered. Carried so the chart can say where a day came
        # from rather than leaving the reader to infer it from the date.
        "source": source,
        **tape,
        "session_start_ms": int(t_ms[0]),
        "session_end_ms": int(t_ms[-1]),
        "rth_open_ms": _wall_ms(day, RTH_OPEN, zone),
        "rth_close_ms": _wall_ms(day, RTH_CLOSE, zone),
    }


@router.get("/live/session")
def live_session(tz: str | None = Query(None)) -> dict:
    """The day's header — everything about the session that is not a tick.

    Shaped to match the Simulator's session payload field for field (minus the
    tape and minus ``session_end_ms``, which a day in progress does not have), so
    the client can hand it to the same ``ReplayEngine`` constructor.

    ``session_start_ms`` is null until the first tick lands. There is genuinely no
    answer before then, and the honest null is what stops the client building an
    engine over an empty tape.
    """
    live = _running()
    s = live.session
    zone = zone_for(tz)
    spec = contract_spec(s.symbol)

    on = s.overnight_frame()
    first = s.slice(0, 1)
    start_ms = int(local_ms(first["ts_utc"], zone)[0]) if not first.empty else None
    globex_anchor_ms = (int(local_ms(on["ts_utc"].iloc[:1], zone)[0])
                        if on is not None else None)
    # Same honesty rule as the Simulator: the weekly line is drawn from the sums
    # already behind this session's Globex open, or it is not drawn. No seed
    # without the night — the client's accumulator would then start at the bell
    # and the seed would be short a whole overnight session.
    #
    # The week's earlier sessions come from whichever store holds them:
    # ``session_sums`` reads the Databento cache first and falls through to the
    # live one, so a replayed cached day and a week of recorded days each build
    # their seed out of their own kind. A week with a hole in it still draws no
    # line at all.
    seed = weeklymod.weekly_seed(s.symbol, s.day) if on is not None else None
    adr = ibmod.day_context(root_symbol(s.symbol), s.day)

    return {
        "gen": s.gen,
        "symbol": s.symbol,
        "root": root_symbol(s.symbol),
        "date": s.day.isoformat(),
        "tz": tz or DEFAULT_DISPLAY_TZ,
        "tick_size": float(spec["tick_size"]),
        "point_value": float(spec["point_value"]),
        "rows": s.n,
        "closed": s.closed,
        "session_start_ms": start_ms,
        "rth_open_ms": _wall_ms(s.day, RTH_OPEN, zone),
        "rth_close_ms": _wall_ms(s.day, RTH_CLOSE, zone),
        # Where the night's VWAP *should* be anchored, beside where it is. The
        # feed backfills this session whole from its 18:00 open however late you
        # connect — but Rithmic's replay has been seen to come back short
        # without erroring, and a band anchored mid-night draws exactly like a
        # correct one. Shipping both numbers is what makes that visible at all.
        "globex_open_ms": _wall_ms(s.day - timedelta(days=1), tickmod.GLOBEX_OPEN, zone),
        "globex_anchor_ms": globex_anchor_ms,
        "weekly_seed": list(seed) if seed is not None else None,
        "has_overnight": on is not None,
        "context": {
            "adr14": adr["adr14"],
            "adr_source": adr["source"],
            "ib_minutes": ibmod.DEFAULTS["ib_minutes"],
            "ib_width_edges": list(ibmod.WIDTH_TERCILE_EDGES),
            "post_ib_add_x": ibmod.POST_IB_RANGE_ADD_X,
        },
    }


@router.get("/live/tape")
def live_tape(
    since: int = Query(0, ge=0),
    gen: str | None = Query(None),
    tz: str | None = Query(None),
) -> dict:
    """Rows ``[since, n)`` of the live tape, delta-encoded.

    The block is self-contained — its own ``t0`` and ``price0``, opening with
    ``dt[0] == dp[0] == 0`` — so the client decodes it on its own and appends the
    result. That is the whole reason the codec is shared with the Simulator: a
    slice of a growing tape and a whole finished session are the same bytes, and
    one decoder is right about both.

    ``reset`` is the answer to a stale ``gen``: the caller's row indices belong to
    a session that is gone, so it gets the tape from row 0 and is told to drop
    what it had.

    ADVANCE ON ``next``, NOT ON ``rows``. The tape keeps growing while this
    request is being served, so ``rows`` is only the count at the moment of reply
    and is generally *ahead* of the block — it is a progress hint. ``next`` is
    ``since + n``: exactly the row this block ends at, and the only cursor that
    cannot skip ticks.
    """
    live = _running()
    s = live.session
    zone = zone_for(tz)
    reset = gen is not None and gen != s.gen
    return _tape_payload(s, 0 if reset else since, None, zone, reset)


def _tape_payload(s, start: int, end: int | None, zone, reset: bool) -> dict:
    """One block of the tape, sliced, encoded and enveloped.

    Shared verbatim between the poll endpoint and the SSE stream so the two can
    never drift: an event's ``data:`` is byte-for-byte a poll response, which is
    what lets the client decode both with the same ``TapeBlock`` type.
    """
    frame = s.slice(start, end)
    tape = encode_ticks(frame, zone, float(contract_spec(s.symbol)["tick_size"]))
    return {
        "gen": s.gen,
        "reset": reset,
        "since": start,
        "next": start + tape["n"],
        "rows": s.n,
        "closed": s.closed,
        **tape,
    }


# Catch-up slices are cut into blocks of this many rows. The blocks are
# self-contained (that is the codec's whole contract), so the client consumes a
# chunked catch-up exactly as it consumes anything else; the cap only bounds how
# long one executor stint and one client-side JSON.parse can be. Steady-state
# blocks are a few dozen rows and never come near it.
_SSE_CHUNK = 200_000
# One comment frame per idle stretch, to keep proxies from calling a silent
# (but healthy) connection dead. Vite's dev proxy and same-origin prod both
# tolerate far longer; 15s is for whatever sits in front some day.
_SSE_HEARTBEAT_S = 15.0
# The control-plane bound: how long a quiet stream goes between looks at
# `livemod.current()`. Data never waits on this — appends wake the stream
# directly — it exists for the one transition with no notifier, `stop()`
# nulling the module global after the close notification already fired.
_SSE_WAIT_S = 1.0


def _sse_frame(payload: dict) -> str:
    """One SSE event. ``id`` is ``{gen}|{next}`` — the browser echoes it back as
    ``Last-Event-ID`` on auto-reconnect, which is what makes reconnection resume
    at the right row of the right accumulation. ``|`` because gen contains ``:``.
    """
    body = json.dumps(payload, separators=(",", ":"))
    return f"id: {payload['gen']}|{payload['next']}\ndata: {body}\n\n"


def _resume_point(request: Request, since: int, gen: str | None) -> tuple[int, str | None]:
    """Where this connection starts reading, header beating query.

    EventSource's auto-reconnect replays the *original* URL — a ``since`` that
    is stale the moment the first block lands — but sends the last event's
    ``id`` as the ``Last-Event-ID`` header. So the header, when present and
    well-formed, is the truth; the query params are only the cold-start seed.
    """
    last_id = request.headers.get("last-event-id")
    if last_id and "|" in last_id:
        g, _, c = last_id.rpartition("|")
        if c.isdigit():
            return int(c), g
    return since, gen


@router.get("/live/tape/stream")
async def live_tape_stream(
    request: Request,
    since: int = Query(0, ge=0),
    gen: str | None = Query(None),
    tz: str | None = Query(None),
) -> StreamingResponse:
    """The live tape as a server-sent event stream: ``/live/tape``, pushed.

    Every ``data:`` payload is byte-for-byte a ``/live/tape`` response (built by
    the same ``_tape_payload``), so the client decodes both with one type and
    the cursor contract — advance on ``next``, reset on ``reset`` — is the same
    contract, driven from the server side.

    Delivery is event-time, not cadence: a per-connection event is nudged by
    ``LiveSession`` the moment an append lands (see ``session.subscribe``), so a
    block goes out when the market printed, not when a timer fired. The chart
    downstream inherits the market's own rhythm, which is the entire point.

    The stream outlives any one session. A roll or restart closes the old
    session (which notifies), the next look at ``livemod.current()`` finds the
    new one, and the client gets a ``reset`` block from row 0 — the same answer
    the poll gives a stale ``gen``. Only two things end the stream: the client
    hanging up, or the live state being gone entirely, which is said out loud as
    an ``event: gone`` rather than an error status — a non-200 would put
    EventSource into retry-forever against a 404.
    """
    zone = zone_for(tz)
    start_cursor, start_gen = _resume_point(request, since, gen)

    async def events():
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()

        def nudge() -> None:
            # Runs on the producer's thread. call_soon_threadsafe is the one
            # loop method that is safe from outside; a dead loop raises and
            # session._notify swallows it, which is the correct fate for a
            # nudge aimed at a connection that no longer exists.
            loop.call_soon_threadsafe(wake.set)

        cursor, client_gen = start_cursor, start_gen
        subscribed = None
        first = True
        sent_closed = False
        last_write = timemod.monotonic()
        try:
            while True:
                live = livemod.current()
                if live is None:
                    yield "event: gone\ndata: {}\n\n"
                    return
                s = live.session
                if s is not subscribed:
                    # First pass, or the session rolled underneath us. Subscribe
                    # BEFORE slicing: an append that lands between the slice and
                    # the wait re-sets the event, so nothing can be missed.
                    if subscribed is not None:
                        subscribed.unsubscribe(nudge)
                    s.subscribe(nudge)
                    subscribed = s
                # Clear BEFORE reading n, same no-missed-append ordering.
                wake.clear()
                reset = client_gen is not None and client_gen != s.gen
                start = 0 if reset else cursor
                n, closed = s.n, s.closed
                if reset or n > start or first or (closed and not sent_closed):
                    end = min(start + _SSE_CHUNK, n)

                    def build(s=s, start=start, end=end, reset=reset):
                        payload = _tape_payload(s, start, end, zone, reset)
                        return payload["next"], _sse_frame(payload)

                    # Encode and serialise off the loop: steady-state blocks are
                    # tiny, but a first connect behind a preloaded session is
                    # the whole day so far, and the loop must keep serving.
                    cursor, frame_str = await loop.run_in_executor(None, build)
                    yield frame_str
                    client_gen = s.gen
                    first = False
                    sent_closed = closed
                    last_write = timemod.monotonic()
                    if cursor < n:
                        continue  # still catching up — no wait between chunks
                try:
                    await asyncio.wait_for(wake.wait(), _SSE_WAIT_S)
                except asyncio.TimeoutError:
                    if timemod.monotonic() - last_write >= _SSE_HEARTBEAT_S:
                        yield ": ping\n\n"
                        last_write = timemod.monotonic()
        finally:
            if subscribed is not None:
                subscribed.unsubscribe(nudge)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/live/signals")
def live_signals() -> dict:
    """Where each strategy would have signalled on the day so far.

    Every row is the output of the same ``run_session`` the backtest calls, over
    the live prefix — so a signal here and a trade in a run mean the same thing.
    Strategies with no baseline pinned appear under ``skipped``: shadow mode runs
    each idea under the config its own baseline validated, and there is nothing
    honest to run one under otherwise.
    """
    return _running().shadow.snapshot()
