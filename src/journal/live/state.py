"""The one live session this process is watching, and what rolls it over.

There is exactly one, and it lives in the API process. With the fake feed that
was a convenience — a session that dies with a ``--reload`` costs nothing, the
source is a file, just replay it. With a real feed it stops being one: a tick
that was not kept is gone for good. That is what ``recorder.py`` is for, and it
is why ``resume()`` exists — a restarted process picks the day back up off disk
instead of starting blind at whatever o'clock it came back.

TWO THINGS THIS MODULE OWNS THAT THE FEED DELIBERATELY DOES NOT:

**The session roll.** A session is prev 18:00 → 18:00 ET, so an always-on host
crosses a boundary every night and the day has to turn over. The feed knows
nothing about it: it publishes batches, and ``_Router`` groups each batch by
``ticks.session_date_for`` and hands the parts to the right session, opening a
new one when the date advances. The roll is therefore decided by the **tick
clock**, never the wall clock — a host whose clock has drifted still cuts the
day where the exchange does, and a batch that straddles 18:00 splits cleanly
instead of landing wholly on one side.

**What is written.** The router appends to the tape and to the recorder on the
same call, so there is no way for one to have a tick the other does not. The
fake feed gets no recorder at all: its source is a cached Databento day, so
recording it would write a second copy of a file we already have, and — worse —
manufacture a "live" day out of one that was merely replayed, which is precisely
the reference Phase 6 needs to stay independent.

``gen`` is the identity of the current accumulation, and it is what makes the
client's incremental poll safe. A client holds rows it has decoded and asks for
what came after them. If the session it was reading has been replaced — a
restart, a roll, a different contract — those row indices describe a tape that no
longer exists, and appending to them would build a chart out of two different
days. So the token travels with every request and every response, and a mismatch
means "throw yours away and start again" rather than "here is some data".
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from datetime import date
from datetime import time as dtime

import pandas as pd

from ..sim import ticks as tickmod
from .feed import FakeFeed, source_frame
from .journal import SignalJournal
from .recorder import TickRecorder
from .session import LiveSession
from .shadow import ShadowRunner

_lock = threading.Lock()
_counter = itertools.count(1)
# Folded into every gen so two *processes* can never mint the same token. The
# counter alone restarts at 1 per boot, so a uvicorn restart on the same
# symbol+day would reissue an old gen — and a client that held rows across the
# restart would splice new ticks onto a tape whose row indices it no longer
# shares (the recorder drops unflushed rows on a kill). With the boot id in the
# token the client sees a mismatch and resets, which is the honest outcome.
_BOOT = os.getpid()

# How often the recorder's heartbeat is rewritten. It is a liveness signal, not
# a data structure — the chunks on disk are the tape whatever this says.
HEARTBEAT_S = 5.0


class Live:
    """A running live session: the tape, what fills it, and what watches it."""

    def __init__(self, session: LiveSession, feed, shadow: ShadowRunner,
                 recorder: TickRecorder | None = None,
                 source: str = "fake") -> None:
        self.session = session
        self.feed = feed
        self.shadow = shadow
        self.recorder = recorder
        self.source = source
        # Ticks that reached the tape while nothing was recording. Only a live
        # feed can produce them (the fake feed never had a recorder to lose), and
        # they are a permanent hole: the tape is in memory, the chunks are what
        # survives, so a day recorded in two halves is a day with a gap in the
        # middle and must say so rather than read as complete.
        self.unrecorded = 0

    # Derived rather than stored, so there is one answer to "is this recording"
    # and it is the object that would be doing the recording.
    @property
    def recording(self) -> bool:
        return self.recorder is not None

    @property
    def signals(self) -> bool:
        return self.shadow.enabled

    @property
    def broker(self):
        """The order plant, if this session's feed opened one. None otherwise.

        Read off the feed rather than stored, for the same reason ``recording``
        is: there is one answer to "can this session send an order" and it is
        the object that would be doing the sending. A resumed session (no feed)
        and a simulated one (a fake feed, which has no such attribute) both
        answer None, which is the correct answer and not a special case.
        """
        return getattr(self.feed, "broker", None)

    def stop(self) -> None:
        # Before anything else. `feed.stop()` is bounded at five seconds and the
        # feed's own teardown detaches the broker, but "eventually" is the wrong
        # guarantee for the ability to trade: stopping the session must leave
        # routing unable to send by the time the call returns.
        broker = self.broker
        if broker is not None:
            broker.detach("session stopped")
        if self.feed is not None:
            self.feed.stop()
        self.shadow.stop()
        self.session.close()
        if self.recorder is not None:
            self.recorder.close(self.session.last_ts())


_current: Live | None = None


def current() -> Live | None:
    return _current


def _new_session(symbol: str, day: date, record: bool, signals: bool = True,
                 source: str = "rithmic") -> Live:
    """Build a session and everything that hangs off it. Does not publish it.

    ``source`` is passed rather than derived from ``record``. It used to be the
    latter, which was true only while the two were the same switch: a Rithmic
    session with recording turned off would otherwise start calling itself a
    fake feed, and the banner that exists to stop this surface being mistaken
    for something it is not would be the thing lying.

    THE JOURNAL FOLLOWS THE RECORDING. It is written for Phase 6's prefix check,
    which compares what the runner said during the session against one settled
    run over the same **live tape** — so a journal with no tape behind it has
    nothing to be checked against. Hence ``record and signals``, not ``signals``.
    """
    gen = f"{symbol}:{day.isoformat()}:{_BOOT}-{next(_counter)}"
    session = LiveSession(symbol, day, gen)
    recorder = TickRecorder(symbol, day) if record else None
    if recorder is not None:
        recorder.marks["shadow"] = "on" if signals else "off"
    shadow = ShadowRunner(
        session,
        journal=SignalJournal(symbol, day) if (record and signals) else None,
        enabled=signals)
    return Live(session, None, shadow, recorder=recorder, source=source)


def _preload(live: Live) -> int:
    """Fill a session from what is already recorded for it. Returns rows loaded.

    Half of restart tolerance, and the half that needs no network. Without it a
    process that came back at eleven would hold a tape that began at eleven — and
    since the engine reads a *frame*, every strategy would then be simulating a
    session that opened two hours late, silently and with plausible numbers. The
    gates would be fine (they read the night off disk, which is there); the
    entries would be wrong.

    The other half is the feed's backfill, and the two answer different
    questions: this one recovers what *this recorder wrote*, and the backfill
    covers the stretch nobody was connected for at all. Composed, the tape is
    whole from the session's open however late the process arrives.
    """
    df = tickmod.live_day_ticks(live.session.symbol, live.session.day)
    if df is None or df.empty:
        return 0
    live.session.append(df)
    return len(df)


class _Router:
    """Delivers a feed's batches to the session each tick belongs to."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._beat_at = 0.0
        self.dropped = 0

    def __call__(self, frame: pd.DataFrame, record: bool = True) -> None:
        """Deliver a batch. ``record=False`` for rows that came *off* disk.

        The feed replays a recorded stretch back through here so that the tape
        is assembled in time order (see ``RithmicFeed._backfill``), and those
        rows must reach the session without reaching the recorder — it appends
        whatever it is handed, so writing them again would duplicate them into a
        later chunk and the day would read back out of order.
        """
        if frame is None or frame.empty:
            return
        days = frame["ts_utc"].map(tickmod.session_date_for)
        # Almost always one group. The split matters on the two occasions a batch
        # can straddle 18:00 ET, and being right there costs a groupby the rest
        # of the time.
        for day, part in frame.groupby(days, sort=True):
            self._deliver(day, part.reset_index(drop=True), record)
        self._beat()

    def _deliver(self, day: date, part: pd.DataFrame, record: bool = True) -> None:
        live = _roll_to(self.symbol, day)
        if live is None:
            self.dropped += len(part)
            print(f"[live-router] dropped {len(part)} tick(s) for {day}: the "
                  "session has already rolled past it", flush=True)
            return
        live.session.append(part)
        if not record:
            return
        if live.recorder is None:
            # Recording is off. Counted rather than ignored: the tape lives in
            # this process and the chunks are what outlives it, so these prints
            # are gone at the next restart. If recording is switched back on the
            # count is stamped into the new recorder's stats, and the day's
            # manifest then says it has a hole instead of looking whole.
            live.unrecorded += len(part)
            return
        try:
            live.recorder.append(part)
        except Exception as e:  # noqa: BLE001 — a lost write beats a dead feed
            st = live.recorder.stats
            st["write_errors"] = st.get("write_errors", 0) + 1
            print(f"[live-recorder] write failed: {type(e).__name__}: {e}",
                  flush=True)

    def _beat(self) -> None:
        now = time.monotonic()
        if now - self._beat_at < HEARTBEAT_S:
            return
        self._beat_at = now
        live = _current
        if live is not None and live.recorder is not None:
            live.recorder.heartbeat(live.session.last_ts())


def _roll_to(symbol: str, day: date) -> Live | None:
    """The live session for ``day``, opening a new one if the date has advanced.

    Rolling *forward* only, and a tick for an already-closed day is **dropped**
    (None) rather than placed anywhere. Neither of the two homes it could have is
    sound: resurrecting the old session would hand the client a tape that jumps
    backwards, and putting it on the current one would break the ordering the
    engine searchsorts the RTH boundary with — silently, since nothing raises on
    an out-of-order tick, it just re-phases bars.

    The feed's monotonic clamp is global across a run, so a published stamp can
    never go backwards and this case should be unreachable through it. It is
    handled anyway because "unreachable" here rests on a different module's
    invariant, and the cost of being wrong is a corrupted tape rather than an
    error.
    """
    global _current
    live = _current
    if live is not None and live.session.symbol == symbol:
        if live.session.day == day:
            return live
        if live.session.day > day:
            return None
    with _lock:
        live = _current
        if live is not None and live.session.symbol == symbol:
            if live.session.day == day:
                return live
            if live.session.day > day:
                return None
        # The modes travel across the roll. They are a decision about this run,
        # not about this day — a recorder that came back at midnight because the
        # session turned over would be the toggle undoing itself at the one hour
        # nobody is watching the screen.
        fresh = _new_session(symbol, day,
                             record=live.recording if live is not None else True,
                             signals=live.signals if live is not None else True,
                             source=live.source if live is not None else "rithmic")
        if live is not None:
            # The feed stays: it belongs to the run, not to the day. Only the
            # session, its recorder and its shadow turn over.
            fresh.feed = live.feed
            live.feed = None
            # The broker rides on the feed, so it comes across too — the socket
            # is the same socket. What it had *staged* does not: `roll_day`
            # opens a journal for the new day and drops any reviewed order,
            # which was priced in the session that just ended. Note the
            # ordering — this has
            # to happen after the feed has moved, because `live.stop()` below
            # reads the broker off `live.feed` and would otherwise detach the
            # connection the fresh session is about to use.
            if fresh.broker is not None:
                fresh.broker.roll_day(day)
            live.stop()
        _preload(fresh)
        _current = fresh
    fresh.shadow.start()
    return fresh


# --- starting ---------------------------------------------------------------


def start(symbol: str, day: date, speed: float = 1.0,
          start_at: dtime | None = None) -> Live:
    """Replace whatever is running with a **fake feed** over one cached session.

    Raises ``LookupError`` when the day has no cached RTH ticks — this is a
    read-only, cache-only path and must never be the thing that spends money at
    Databento, the same rule the Simulator's endpoints run under.

    Records nothing. See the module docstring.
    """
    global _current
    frame = source_frame(symbol, day)
    if frame is None:
        raise LookupError(f"no cached RTH ticks for {symbol} on {day.isoformat()}")
    with _lock:
        if _current is not None:
            _current.stop()
        live = _new_session(symbol, day, record=False, source="fake")
        live.feed = FakeFeed(live.session, frame, speed=speed, start_at=start_at)
        _current = live
    # Started outside the lock: both spawn threads that immediately read the
    # session, and holding the module lock while they come up would serialise a
    # request handler behind thread startup for no reason.
    live.feed.start()
    live.shadow.start()
    return live


def start_rithmic(symbol: str, exchange: str = "CME", day: date | None = None,
                  backfill: bool = True, sweep_days: int | None = None,
                  record: bool = True, signals: bool = True,
                  routing: bool = False) -> Live:
    """Connect the **real** ticker plant and start recording.

    ``symbol`` must be a raw contract (``NQU6``), never a root: ``contract_for``
    resolves a root by probing Databento, which a live path must not do, and the
    on-disk roll map ends 2026-06-30 regardless.

    ``day`` seeds the session before the first tick arrives, so the surface has
    something to show and the recorder resumes into the right directory; it
    defaults to the session the wall clock is in. It is only a *starting* guess —
    once ticks arrive the roll follows their own stamps.

    ``backfill`` replays the session so far off the history plant before the live
    stream begins. On by default, because the alternative is the thing this
    module already refuses to do everywhere else: a tape that *begins* whenever
    somebody clicked, handed to strategies that read it as the session. The two
    seeds compose — ``_preload`` recovers what this recorder wrote and the
    backfill covers the stretch nobody was connected for, so the request starts
    at the tape's own tail rather than at the session open.

    What it does not replace is the recording. A backfill can only reach a
    contract that is still listed (measured: an expired one returns nothing at
    any depth), so a day that is never recorded is a day that cannot be
    reconstructed later.

    ``record`` and ``signals`` are the two switches ``set_modes`` toggles at
    runtime, offered here so a connection can be opened in the mode it is meant
    to run in rather than opened and then corrected. They obey the same rule —
    see ``check_modes``.

    ``routing`` is **not** a third switch of that kind, and the asymmetry is the
    point. Those two change what gets *written down* about a session that is
    being watched either way; this one decides whether the connection opens
    Rithmic's ORDER plant at all. Settled here and unchangeable afterwards, so a
    shadow session can never grow the ability to trade under a running page — it
    would have to be stopped and started deliberately, which is a person's
    decision and looks like one. Refused outright unless the environment allows
    it (``journal.live.routing.policy``), because connecting an order plant
    that could never send is a socket opened for nothing.

    Raises ``LookupError`` if the Rithmic credentials are not configured, and
    ``ValueError`` for a mode pair that would run the shelf blind or a routing
    request the environment refuses.
    """
    global _current
    from . import harvest
    from .rithmic import RithmicFeed, credentials

    check_modes("rithmic", record, signals)
    creds = credentials()
    broker = None
    if routing:
        from ..config import contract_spec
        from .broker import Broker
        from .routing import policy

        pol = policy()
        refusal = pol.refusal()
        if refusal:
            raise ValueError(refusal)
        broker = Broker(symbol, exchange,
                        day or tickmod.session_date_for(pd.Timestamp.now(tz="UTC")),
                        pol, tick_size=float(contract_spec(symbol)["tick_size"]),
                        point_value=float(contract_spec(symbol)["point_value"]),
                        # Qualifies the account tags: an account id is unique
                        # within a login, so a tag following the id alone could
                        # label a different firm's account of the same name.
                        system=creds.get("system_name", ""))
    day = day or tickmod.session_date_for(pd.Timestamp.now(tz="UTC"))
    with _lock:
        if _current is not None:
            _current.stop()
        live = _new_session(symbol, day, record=record, signals=signals)
        recorded = tickmod.live_day_ticks(symbol, day)
        if backfill:
            # Handed to the feed rather than appended here, and the difference is
            # the whole fix: a recording that begins at 07:08 has the night
            # missing in front of it, and rows replayed for 18:00 cannot be
            # appended *behind* rows already sitting at 07:08. The feed publishes
            # the night, then this, then the rest — in time order, once.
            live.feed = RithmicFeed(
                _Router(symbol), symbol, exchange, creds=creds,
                backfill_from=tickmod.day_bounds_utc(day)[0],
                resume_frame=recorded,
                # Earlier sessions are swept on this connection rather than a
                # second one — Rithmic allows one session per login, and a sweep
                # with its own client would log this feed straight out.
                sweep_days=sweep_days if sweep_days is not None
                else harvest.HARVEST_DAYS,
                broker=broker)
        else:
            _preload(live)
            live.feed = RithmicFeed(_Router(symbol), symbol, exchange, creds=creds,
                                    broker=broker)
        _current = live
    live.feed.start()
    live.shadow.start()
    return live


def resume(symbol: str | None = None) -> Live | None:
    """Re-open the most recent recorded session at startup, without a feed.

    What this is for: the API restarting mid-session. The ticks that arrived
    before the restart are on disk, so the surface can be whole again
    immediately — and the shadow runner can go on saying what the shelf makes of
    the day — while whoever restarts the feed does so separately. A session with
    no feed simply stops growing, which is visible on the status endpoint rather
    than silent.

    Returns None when nothing has been recorded for the current session date;
    only *today's* session is resumed, because an older one is a finished day and
    a finished day has nothing left to do *here*. It is not inert — since decision
    4 was reversed it is replayable in the Simulator — but that is a reader of the
    store, not a session to bring back up.

    ``symbol`` (defaulting to ``LIVE_SYMBOL``) says which contract to pick up when
    more than one has been recorded today. Without it the choice falls to the
    freshest heartbeat, which is the best available guess at "the one that was
    running" — but a host that records a contract deliberately should name it,
    rather than leaving the answer to whichever directory sorted last.

    THE SHADOW MODE IS RESTORED FROM THE MANIFEST. If the shelf was switched off
    before the restart, it comes back off. A restart is not a decision, and a
    process that re-armed the runner because it happened to bounce would be
    changing what the day is being watched by without anybody choosing it.
    """
    global _current
    import os

    from .recorder import read_manifest, recorded_days

    want = (symbol or os.environ.get("LIVE_SYMBOL", "")).strip().upper()
    today = tickmod.session_date_for(pd.Timestamp.now(tz="UTC"))
    candidates = [(s, d) for s, d in recorded_days()
                  if d == today and (not want or s == want)]
    if not candidates:
        return None
    candidates.sort(key=lambda sd: (read_manifest(*sd) or {}).get("updated_at") or "")
    symbol, day = candidates[-1]
    man = read_manifest(symbol, day) or {}
    with _lock:
        if _current is not None:
            _current.stop()
        live = _new_session(symbol, day, record=True,
                            signals=man.get("shadow") != "off")
        _preload(live)
        _current = live
    live.shadow.start()
    return live


# --- the two modes ----------------------------------------------------------


def check_modes(source: str, record: bool, signals: bool) -> None:
    """Raise ``ValueError`` for a (record, signals) pair that would lie.

    Two combinations are refused, for opposite reasons, and neither is a policy
    preference — both are the failure modes this module exists to prevent.

    **Live feed, shelf on, recording off.** Ten ``gx_*`` gate sites and the
    weekly seed read the session's earlier windows **off disk**, keyed by
    (contract, day) — not from the frame the runner injects. With nothing being
    written, those reads find nothing, the gates blind-fail-closed, and seven of
    the thirteen strategies veto everything without saying why. On screen that is
    indistinguishable from a morning in which no setup formed. So the shelf may
    be turned off, and the recording may be turned off, but not the recording
    alone. (Cutting the recorder *process* was always allowed; cutting the
    writes under a running shelf never was — docs/live-shadow-plan.md decision 7.)

    **Fake feed, recording on.** Its source is a cached Databento day. Recording
    it would write a second copy of a file that already exists and, worse, would
    manufacture a "live" day out of a replayed one — precisely the independence
    Phase 6's reference set depends on (decisions 3-4).

    The fake feed may run the shelf with nothing recorded, and that is not an
    exception to the first rule: the day it replays is a cached day, so the
    windows the gates read are already on disk. The rule is about whether the
    reads can be answered, and ``source`` is what decides that.
    """
    if signals and not record and source != "fake":
        raise ValueError(
            "shadow signals need the tape recorded: the Globex gates and the "
            "weekly seed read this session's earlier windows off disk, and with "
            "nothing written they veto everything without saying why. Turn the "
            "signals off too, or leave recording on.")
    if record and source == "fake":
        raise ValueError(
            "the simulated feed records nothing by design — its source is a "
            "cached day, and recording it would manufacture a live day out of a "
            "replayed one.")


def set_modes(record: bool | None = None, signals: bool | None = None) -> Live:
    """Turn recording and the shadow shelf on or off under a running session.

    ``None`` leaves a mode as it is, so either can be set without knowing the
    other. Raises ``LookupError`` with no session running and ``ValueError`` for
    a refused pair (``check_modes``); in both cases nothing has changed.

    Switching recording back on **resumes** rather than restarts — the chunk
    numbering continues, so nothing already written is overwritten — but the
    prints that arrived while it was off were never on disk to begin with. Their
    count is stamped into the new recorder's stats, and from there into the
    day's manifest, because a day with a hole that reads as complete is worse
    than no recording at all.
    """
    global _current
    live = _current
    if live is None:
        raise LookupError("no live session is running")
    want_rec = live.recording if record is None else bool(record)
    want_sig = live.signals if signals is None else bool(signals)
    check_modes(live.source, want_rec, want_sig)
    if (want_rec, want_sig) == (live.recording, live.signals):
        return live

    with _lock:
        if want_rec and live.recorder is None:
            rec = TickRecorder(live.session.symbol, live.session.day)
            if live.unrecorded:
                rec.stats["unrecorded_rows"] = live.unrecorded
            live.recorder = rec
        elif not want_rec and live.recorder is not None:
            # Closed rather than dropped: the buffer holds up to a seal
            # interval of prints, and they are on the tape already, so leaving
            # them unsealed would lose ticks that were successfully recorded.
            live.recorder.close(live.session.last_ts())
            live.recorder = None

    # Outside the lock: `set_enabled` starts a thread that immediately reads the
    # session, and holding the module lock across that would serialise a request
    # handler behind thread startup — the same reason `start` releases it first.
    live.shadow.set_journal(
        SignalJournal(live.session.symbol, live.session.day)
        if (want_rec and want_sig) else None)
    live.shadow.set_enabled(want_sig)
    if live.recorder is not None:
        live.recorder.marks["shadow"] = "on" if want_sig else "off"
        live.recorder.heartbeat(live.session.last_ts())
    return live


def stop() -> bool:
    """Stop the running session. True if there was one."""
    global _current
    with _lock:
        live = _current
        _current = None
    if live is None:
        return False
    live.stop()
    return True
