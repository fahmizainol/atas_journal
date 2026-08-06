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

NO ORDER ROUTING. Nothing in this router can send an order, and nothing in Phases
0-6 should be shaped as though it might. See docs/live-shadow-plan.md § Phase 7.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

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
) -> dict:
    """Connect the real ticker plant and start recording the session.

    ``symbol`` must be the raw contract. A root would send ``contract_for`` to
    probe Databento — which a live path must never do — and the on-disk roll map
    ends 2026-06-30 regardless, so there is nothing there to resolve against.

    Market data only. Nothing in this router can reach the order plant. The feed
    opens ``TICKER_PLANT`` — plus ``HISTORY_PLANT`` when backfilling — rather
    than taking the client's all-four default (docs/live-shadow-plan.md
    decision 2).

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
                                     record=record, signals=signals)
    except LookupError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"gen": live.session.gen, "symbol": live.session.symbol,
            "date": live.session.day.isoformat(), "source": live.source,
            "recording": live.recording, "signals": live.signals,
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

    Deliberately its own endpoint rather than a source flag on
    ``/simulator/days``: a recorded day is not replayable (decision 4), and the
    two stores stay visible in exactly the places that own them.

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
    start = 0 if reset else since
    frame = s.slice(start)
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
