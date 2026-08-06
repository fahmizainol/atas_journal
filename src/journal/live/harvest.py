"""Filling in the sessions nobody was connected for.

The feed's backfill makes *this* session whole from its 18:00 ET open however
late you connect. This is the other half: the days you were not connected at
all. Same source, same replay, same live store — a different question about it.

WHAT MAKES IT POSSIBLE, AND WHAT BOUNDS IT. Rithmic's tick replay serves a
listed contract back roughly **120 days** (measured: dense data at 120, nothing
at 140), and serves an **expired** contract not at all, at any depth. So a
contract's whole front-month life is reachable while it is the front month, and
becomes unreachable the moment it rolls. That is the one deadline in this
module: **deep-harvest the outgoing contract before switching ``LIVE_SYMBOL``.**

WHY A COMPLETION FLAG AND NOT A COVERAGE TEST. "Which days am I missing?" looks
like a calendar question and is not one here. ``ticks.market_closed`` only knows
*full* exchange closures, and only for contracts with a roll probe — a pinned raw
contract has none, so it answers False for every day of the year. Deriving it
from the tape instead fails the other way: a half-day session and a session with
a hole in it look identical from the timestamps, so a Thanksgiving Friday would
be re-fetched on every startup for as long as the process ever starts. So a
harvested day writes ``harvest.complete`` into its manifest and is skipped on
that, whatever the row count — including zero, which is a real answer for a day
the exchange did not trade.

WHAT IT DOES NOT MAKE. A harvested day is not a *watched* day, and the manifest
says so:

  - **no signal journal.** Nothing recorded what the shelf believed during a
    session nobody ran it over, and nothing can reconstruct it. Phase 6's prefix
    integrity has nothing to check on a harvested day, and should say so rather
    than report a clean pass over an empty comparison.
  - **Rithmic's clock throughout.** A watched day is stamped from the exchange's
    own ``source_ssboe``; a replayed one carries Rithmic's, a median 287µs later.
    Sub-millisecond and it moves no bar — but it is a systematic offset against
    Databento's ``ts_event``, which is exactly what Phase 6 stage 1 measures.

Neither is a defect. Both are things a reader of the data has to be able to find
out without asking a person, which is what ``source: "harvest"`` is for.

NOT BACKTEST DATA. This writes to ``data/live/ticks/`` and nowhere else
(docs/live-shadow-plan.md decision 3). ``get_day_ticks`` — what the engine loads
a session with — reads the Databento cache and does not fall through here, so
harvesting July does not make July backtestable. The gates and the weekly seed
*do* fall through, which is the point: they are what a shadow session needs.
"""

from __future__ import annotations

import os
import threading
from datetime import date, timedelta

import pandas as pd

from ..sim import ticks as tickmod
from .recorder import TickRecorder, read_manifest

# How far back the automatic sweeps look. A trailing window rather than a
# contract start date: the roll is a volume migration over several days, not an
# instant, so "when did this contract begin" is a judgment that would have to be
# re-made and re-hardcoded every quarter. Thirty days covers any realistic gap —
# a holiday, a fortnight away — and the 120-day ceiling is the real backstop.
# The one-time deep harvest is a CLI run with an explicit start.
HARVEST_DAYS = int(os.getenv("LIVE_HARVEST_DAYS", "30"))

# One sweep at a time, process-wide. Not tidiness: Rithmic allows one concurrent
# session per login, so two sweeps racing would force-log-out each other and the
# live feed with them.
_lock = threading.Lock()
_running = False


def in_progress() -> bool:
    return _running


def sessions_between(start: date, end: date) -> list[date]:
    """Candidate session dates in ``[start, end]``, oldest first.

    Weekdays only — a session runs prev 18:00 → 18:00 ET, so Sunday evening
    belongs to Monday and Saturday is unreachable (``ticks.session_date_for``).
    Holidays are *not* filtered: they cannot be told from the calendar for a
    pinned contract, and a day that returns nothing is answered by the
    completion flag rather than predicted.
    """
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def gaps_in(symbol: str, day: date) -> list[tuple[int, int]]:
    """The ranges of a closed session still missing from the live store, in ns.

    Empty when the day is whole — either already flagged, or recorded from its
    open to its close. Otherwise the head in front of what was recorded and the
    tail behind it, which is the same shape the feed's own backfill fills and for
    the same reason: a recording that starts at 07:08 has the night missing in
    front of it, and rows for 18:00 cannot be appended behind rows at 07:08.

    A hole *between* two earlier recordings is not returned. Finding it would
    mean telling a gap in the tape from a quiet market, which the tape cannot
    say.
    """
    open_utc, close_utc = tickmod.day_bounds_utc(day)
    lo, hi = int(open_utc.value), int(close_utc.value)

    man = read_manifest(symbol, day) or {}
    if (man.get("harvest") or {}).get("complete"):
        return []

    df = tickmod.live_day_ticks(symbol, day)
    if df is None or df.empty:
        return [(lo, hi)]

    # Held off what is already there by the same slack the feed uses, and for the
    # same measured reason: a replayed print carries Rithmic's stamp where a
    # recorded one carries the exchange's, a median 287µs later. A tail starting
    # exactly at the last recorded tick would therefore re-admit that very print
    # as a new one — volume that never traded, at a price that printed once. Both
    # edges are cut so the fill can only ever *lose* a print at the join.
    from .rithmic import SEAM_SLACK_NS

    first = int(df["ts_utc"].iloc[0].value)
    last = int(df["ts_utc"].iloc[-1].value)
    out = []
    if first - SEAM_SLACK_NS > lo:
        out.append((lo, first - SEAM_SLACK_NS))
    if last + SEAM_SLACK_NS < hi:
        out.append((last + SEAM_SLACK_NS, hi))
    return out


def pending(symbol: str, start: date, end: date) -> list[date]:
    """Closed sessions in the range that still have something to fetch.

    Only *closed* sessions: a day still running is the live feed's job, and
    marking it complete would freeze it half-recorded.
    """
    now = pd.Timestamp.now(tz="UTC")
    days = []
    for day in sessions_between(start, end):
        if tickmod.day_bounds_utc(day)[1] > now:
            continue
        if gaps_in(symbol, day):
            days.append(day)
    return days


async def harvest_day(client, symbol: str, day: date, exchange: str = "CME") -> dict:
    """Fetch and record one closed session's missing ranges.

    Returns a summary rather than raising: a sweep is a background job over many
    days, and one day that Rithmic will not answer for must not stop the rest.
    """
    import asyncio

    from .rithmic import _aggressor_map, _replay_aggressor_map, replay_into

    gaps = gaps_in(symbol, day)
    if not gaps:
        return {"date": day.isoformat(), "skipped": True, "rows": 0}

    agg_num, agg_side = _replay_aggressor_map(), _aggressor_map()
    rec = TickRecorder(symbol, day)
    loop = asyncio.get_running_loop()

    async def publish(frame: pd.DataFrame) -> None:
        # Off the event loop: this ends in a parquet write, and a seal that
        # blocked the loop would stop the history socket draining while it ran.
        await loop.run_in_executor(None, rec.append, frame)

    rows = aggregated = 0
    covered = True
    error = None
    for from_ns, until_ns in gaps:
        try:
            res = await replay_into(client, symbol, exchange, from_ns, until_ns,
                                    agg_num, agg_side, publish)
        except Exception as e:  # noqa: BLE001 — one bad day, not a dead sweep
            error = f"{type(e).__name__}: {e}"
            break
        rows += res["rows"]
        aggregated += res["aggregated"]
        covered = covered and res["covered"]

    rec.flush()
    # The flag is the whole safety property, so it takes three things and not
    # one. Nothing threw; the replay said there was nothing left (it returns a
    # silent prefix often enough that a session came back 50,000 prints long
    # ending at 04:29 ET); **and something actually came back**.
    #
    # That last condition was not in the first version, on the reasoning that
    # zero rows is the honest answer for a day the exchange did not trade — and
    # it is, but it is equally the answer for a replay that transiently returned
    # nothing, and the two cannot be told apart from one call. 2026-07-06 came
    # back empty and was flagged; a 60-second probe of the same day at 10:00 ET
    # returned 2,142 prints. So an empty day is retried instead. The cost is a
    # second per real holiday per sweep. The cost of the other choice is a
    # session permanently recorded as having no trades in it.
    #
    # It is "the day has prints", though, not "this fetch returned prints".
    # A **half-day** — Juneteenth and 3 July both close at 13:00 ET — has a
    # legitimately empty tail, and keying on the fetch would leave every one of
    # them unflagged and re-fetched on every sweep for the life of the machine.
    df = tickmod.live_day_ticks(symbol, day)
    has_prints = df is not None and not df.empty
    last = df["ts_utc"].iloc[-1] if has_prints else None
    done = error is None and covered and has_prints
    rec.heartbeat(last, closed=done, source="harvest",
                  harvest={"complete": done, "rows": rows, "covered": covered,
                           "aggregated": aggregated, "error": error})
    tickmod._clear_tick_caches()
    return {"date": day.isoformat(), "skipped": False, "rows": rows,
            "aggregated": aggregated, "covered": covered, "error": error}


async def sweep(client, symbol: str, start: date, end: date | None = None,
                exchange: str = "CME", on_day=None) -> list[dict]:
    """Harvest every closed session in the range that is missing anything.

    ``client`` is an already-connected Rithmic client with the history plant
    open. It is passed in rather than made here because of the one-session rule:
    when the live feed is up, this has to ride on *its* connection or it would
    log the feed out.
    """
    global _running

    end = end or (pd.Timestamp.now(tz="UTC").date())
    with _lock:
        if _running:
            return []
        _running = True
    out = []
    try:
        for day in pending(symbol, start, end):
            res = await harvest_day(client, symbol, day, exchange)
            out.append(res)
            if on_day is not None:
                on_day(res)
    finally:
        _running = False
    return out


def default_start(days: int | None = None) -> date:
    return (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days or HARVEST_DAYS)).date()


async def sweep_standalone(symbol: str, start: date, end: date | None = None,
                           exchange: str = "CME", on_day=None) -> list[dict]:
    """Open a connection of our own, sweep, and close it again.

    HISTORY_PLANT alone — no market-data subscription, no ORDER plant. Which
    also makes it the fast path: a replay measured 12s on a quiet event loop
    against 66s beside a running LAST_TRADE subscription.

    **Only safe when no feed is connected.** One session per login, so this would
    force-log-out a running feed; callers check. When a feed *is* up, its own
    background sweep does this work on its connection instead.
    """
    from async_rithmic import RithmicClient, SysInfraType

    from .rithmic import credentials, install_redaction

    creds = credentials()
    install_redaction(creds["password"])
    client = RithmicClient(**creds)
    await client.connect(plants=[SysInfraType.HISTORY_PLANT])
    try:
        return await sweep(client, symbol, start, end, exchange, on_day)
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 — we are already leaving
            pass


def sweep_in_background(symbol: str, days: int | None = None,
                        exchange: str = "CME") -> threading.Thread:
    """Run a standalone sweep on a thread of its own. Returns immediately.

    For API startup: filling in the days a laptop was closed for is worth doing
    unprompted, and worth nothing if it delays the server coming up. Failures
    are printed and dropped — a sweep that cannot reach Rithmic is a missing
    convenience, not a broken API.
    """
    import asyncio

    def run() -> None:
        try:
            got = asyncio.run(sweep_standalone(
                symbol, default_start(days), exchange=exchange,
                on_day=lambda r: print(
                    f"[live-harvest] {r['date']}: "
                    + ("already whole" if r["skipped"] else f"{r['rows']:,} prints"),
                    flush=True)))
        except Exception as e:  # noqa: BLE001
            print(f"[live-harvest] startup sweep skipped: {type(e).__name__}: {e}",
                  flush=True)
            return
        filled = [d for d in got if not d["skipped"] and d["rows"]]
        print(f"[live-harvest] startup sweep done: {len(filled)} session(s) filled, "
              f"{sum(d['rows'] for d in filled):,} prints", flush=True)

    t = threading.Thread(target=run, name="live-harvest", daemon=True)
    t.start()
    return t
