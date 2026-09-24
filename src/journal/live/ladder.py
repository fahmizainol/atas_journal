"""The trailing ladder, as arithmetic. No broker, no clock, no I/O.

This is the rule the Simulator practises and the backtest engine trades —
`replaySim.ts:433` and `sim/rules.py`'s `trail_stop_ticks` and friends — ported
so that a **live** position can be trailed by the same four knobs. Until now
/live could only trail Rithmic's way (one free variable, riding at the stop's own
distance, recomputed absolutely on every new extreme, drags refused), which meant
the rule you practised was not the rule you traded.

WHY THIS FILE IMPORTS ALMOST NOTHING. The rule is the half of the feature that
can be tested without a socket, a thread, or money — so it is pure, and it is
compared tick-for-tick against the TypeScript in `tests/test_live_ladder.py`.
`LadderRunner` at the foot of this file is the part with a thread in it, and it
reaches a broker through two injected callables rather than importing one, so the
whole module still tests without a Rithmic login.

THE PARITY IS THE POINT, AND IT IS ALSO THE HAZARD. Two implementations of one
rule is exactly the "blotter that quietly disagrees with the broker by a rung"
that kept the trail off the paper account for months. What makes it safe here is
that the disagreement is *checkable*: both are pure functions of (config, entry,
high-water mark), so a fixed sequence driven through both must produce the same
rungs in the same order. If you change the arithmetic in this file, change it in
`frontend/src/lib/replaySim.ts` in the same commit, and let the fixture fail if
you didn't.

DISTANCES ARE PRICES IN HERE, TICKS AT THE EDGE. The ticket speaks ticks and the
wire speaks prices; the conversion happens once, in `TrailCfg.from_ticks`, for
the same reason `replaySim` does it at placement — nothing downstream should have
to carry a tick size around, and a rung computed in tick space and rounded twice
is a rung in the wrong place.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TrailCfg:
    """The four knobs, resolved to prices.

    Frozen because a ladder whose settings changed under it mid-position would
    make the rung sequence unreproducible — and reproducibility from
    (config, entry, high-water mark) is the whole basis of the parity test. The
    ticket's settings are snapshotted onto the position at placement, exactly as
    `OrderRec.trail` is in the replay.
    """

    #: How far behind the high-water price the stop rides. 0 = the trail is off,
    #: and the master switch for all of it.
    dist: float
    #: The grid the stop is allowed to rest on. 0 = one rung per `dist`.
    step: float = 0.0
    #: How far past the entry the first rung lands. Zero is breakeven *gross* —
    #: the round trip still owes commission — so a few ticks here is what makes a
    #: scratch really a scratch.
    be: float = 0.0
    #: Take the first rung and no other. That is a breakeven stop rather than a
    #: trail, and it is its own rule: a trail hands back open profit on every
    #: pullback, which is exactly what a breakeven stop refuses to do.
    be_only: bool = False

    @classmethod
    def from_ticks(cls, tick_size: float, *, dist: int, step: int = 0,
                   be: int = 0, be_only: bool = False) -> "TrailCfg | None":
        """Ticks to prices, once. ``None`` when the trail is off.

        Returning None rather than a zeroed config on ``dist <= 0`` keeps the
        "is there a ladder here" question a single ``is None`` everywhere above
        this, instead of a `dist > 0` test that one call site will eventually
        forget.
        """
        if dist <= 0:
            return None
        return cls(dist=dist * tick_size, step=step * tick_size,
                   be=be * tick_size, be_only=be_only)


@dataclass
class LadderState:
    """One position's ladder, mid-flight.

    Mirrors the subset of `replaySim`'s `Position` the ladder actually reads, and
    nothing else: this deliberately knows nothing about size, fees, or PnL, so
    there is no second place where a live position's economics could be
    described and get it wrong.
    """

    #: "long" | "short". The vocabulary the ladder thinks in — a broker's
    #: buy/sell is an instruction, and the ladder is asking about a position.
    side: str
    entry_price: float
    cfg: TrailCfg
    #: The best price the trade has seen. Seeded at the entry, so a position that
    #: never goes in front never takes a rung.
    hwm: float
    #: Where the stop currently is, as far as this ladder knows. None until one
    #: is read back from the broker.
    stop: float | None = None
    #: Set by a hand drag: the grid re-pins here instead of on the breakeven
    #: rung. None while the ladder still owns the origin.
    ladder: float | None = None
    #: Has the ladder moved this stop? Decides whether a stop-out is booked as a
    #: trail or as the stop the position opened with, and is cleared by a drag —
    #: the level is yours again until the next rung claims it back.
    armed: bool = False
    #: Every level this ladder has actually asked the broker for, in order. Kept
    #: because the rung sequence is what the parity fixture compares and what
    #: `orders.jsonl` is read against after a live run — a ladder that ends in
    #: the right place having taken the wrong path is still wrong.
    rungs: list[float] = field(default_factory=list)

    @property
    def dir(self) -> int:
        return 1 if self.side == "long" else -1

    def mark(self, price: float) -> None:
        """Take a print. Only ever moves the high-water mark forward."""
        if (price - self.hwm) * self.dir > 0:
            self.hwm = price


def trail_stop(st: LadderState) -> float | None:
    """Where the ladder wants the stop, or None while it hasn't reached far
    enough in front to take its first rung.

    The grid is pinned at the breakeven rung and rungs sit `step` apart from
    there; the stop rides `dist − be` behind the high-water price, dropping onto
    the highest rung that distance clears. So with a 40-tick trail, a 20-tick
    step and a 4-tick breakeven, a long from 21000 puts its stop on 21001 once
    the trade has printed 21010, on 21006 at 21015, and so on — always 36 to 56
    ticks behind the best the trade has seen.

    A manual drag re-pins the grid wherever you put it, in whichever direction
    you moved it — `ladder` simply replaces the breakeven rung as the origin.
    See `repin`, which is the other half of making that stick.

    Ported line for line from `replaySim.ts:433`, including the order of the
    arithmetic: both languages are IEEE-754 doubles, so evaluating the same
    expression the same way is what makes the rungs identical rather than merely
    close. Do not "simplify" the expression.
    """
    tc = st.cfg
    if tc.dist <= 0:
        return None
    d = st.dir
    step = tc.step if tc.step > 0 else tc.dist
    back = max(0.0, tc.dist - tc.be)
    origin = st.ladder if st.ladder is not None else st.entry_price + d * tc.be
    # How many whole rungs past the origin the high-water price has carried it.
    k = math.floor(((st.hwm - origin) * d - back) / step)
    if k < 0:
        return None
    return origin if tc.be_only else origin + d * k * step


def tighten(st: LadderState, lvl: float) -> bool:
    """Move the stop onto `lvl`, but only ever toward the trade.

    Everything the ladder does goes through here, so "never loosens" is one line
    rather than an invariant spread across the call sites — and on a live account
    that invariant is the difference between a ratchet and a stop that wanders
    outward while the chart says otherwise.

    Returns whether it moved, because the caller is about to spend a wire round
    trip on the answer.
    """
    if st.stop is not None and (lvl - st.stop) * st.dir <= 0:
        return False
    st.stop = lvl
    st.armed = True
    st.rungs.append(lvl)
    return True


def repin(st: LadderState, dragged: float) -> None:
    """A hand drag wins outright, whichever way it went.

    The ladder is a tool for managing the stop, not a lock on it. Two things have
    to happen for a drag to hold, because a level alone would not survive the
    next print:

      - the grid re-pins on where you put it, so the rungs from here are spaced
        off your level rather than off the entry;
      - the high-water mark walks back to the last high your level is consistent
        with. The ladder reads the high, so leaving a high that already justifies
        a tighter stop would have it snap straight back — the drag would appear
        to take, then undo itself a tick later.

    Only ever walked *back*: a drag can make the trail forget a high it has been
    given, never invent one it hasn't seen.

    Mirrors the bracket branch of `replaySim`'s `admin` (`replaySim.ts:685`).
    """
    d = st.dir
    st.stop = dragged
    st.ladder = dragged
    cap = dragged + d * max(0.0, st.cfg.dist - st.cfg.be)
    if (cap - st.hwm) * d < 0:
        st.hwm = cap
    # The level is yours now, so being taken out on it is a stop rather than a
    # trail. The next rung the ladder takes claims it back.
    st.armed = False


# --- the ratchet ------------------------------------------------------------
#
# Everything above decides where the stop goes. This decides when to spend a wire
# round trip saying so.
#
# **THE RATCHET MUST NOT RUN ON THE FEED'S ASYNCIO LOOP.** `Broker._call` submits
# to that loop with `run_coroutine_threadsafe(...).result(timeout)`; called from
# the loop it is already on, it blocks the loop waiting for itself — deadlocking
# the order plant *and freezing the tick feed*, which is the one failure here
# that takes the market data down with it.
#
# It must not run inline in the tick delivery either. `RithmicFeed._drain` does
# `await loop.run_in_executor(None, self.route, frame)`, so a ~239 ms modify
# inside that call is backpressure on the feed: the tape would stutter every time
# a rung fired.
#
# Hence a thread of its own. The tick path does arithmetic under a lock and
# signals; this thread wakes, decides, and blocks on the wire where blocking is
# free. Single-flight falls out of there being one of it — which also keeps
# Rithmic from answering 'Atomic order operation in progress' to two overlapping
# modifies of the same leg.

#: Consecutive failed modifies before the ladder gives up on a position. A stop
#: that cannot be moved is a fact about the plant, not about this tick, and
#: retrying it once a print is how a wedged session turns into thousands of
#: journal lines and an order plant nobody can use by hand either.
MAX_CONSECUTIVE_FAILURES = 3

#: How long the thread sleeps when nothing has printed. Only bounds how quickly
#: a `stop()` is noticed — every real wake-up comes from `on_prices`.
_IDLE_WAIT_S = 0.5

#: How long to wait after a rung failed before asking for it again.
#:
#: Load-bearing rather than polite. Nothing about a failure changes what
#: `trail_stop` wants, so without a backoff the loop would recompute the same
#: level and try again immediately, at whatever rate the tape is arriving —
#: spending the whole give-up budget inside a millisecond, on a condition that
#: had not been given time to clear.
_RETRY_BACKOFF_S = 0.5


class LadderRunner:
    """Turns marks into modifies, on a thread of its own.

    Injected rather than wired to a `Broker` directly:

      - ``move(level)`` sends the stop to ``level`` and **raises** if it did not
        go. The runner treats a raise as "the stop is still where it was", which
        is the only assumption that is safe in both directions.
      - ``log(event, **fields)`` writes a line to the order journal.

    That keeps this testable with two closures and no login, and it keeps the
    broker's own vocabulary (baskets, legs, templates) out of a file whose
    subject is geometry.
    """

    def __init__(self, *, move: Callable[[float], None],
                 log: Callable[..., None] | None = None) -> None:
        self._move = move
        self._log = log or (lambda *a, **k: None)
        self._cv = threading.Condition()
        self._state: LadderState | None = None
        #: Bumped by every arm and disarm. Captured before a modify is sent and
        #: re-checked after: a round trip that started under one position must
        #: not land on the next one, and 239 ms is long enough for a stop-out
        #: and a re-entry to have happened in between.
        self._epoch = 0
        self._failures = 0
        #: `time.monotonic()` before which no rung is attempted. See
        #: `_RETRY_BACKOFF_S`.
        self._retry_after = 0.0
        self._thread: threading.Thread | None = None
        self._running = False
        #: Counters for the routing snapshot. A ladder that is quietly doing
        #: nothing and one that is quietly failing look identical without these.
        self.stats = {"rungs": 0, "failed": 0, "given_up": 0}

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ladder",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread. The stop *order* stays exactly where it is — it is a
        working order at the broker, not something this process holds up."""
        with self._cv:
            self._running = False
            self._cv.notify_all()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=2.0)

    def arm(self, *, side: str, entry_price: float, cfg: TrailCfg,
            stop: float | None, hwm: float | None = None) -> None:
        """Begin trailing a position.

        ``hwm`` is for the case this process did not watch the position open — a
        restart mid-trade. Seeded from the entry otherwise, so a trade that never
        goes in front never takes a rung.
        """
        with self._cv:
            self._epoch += 1
            self._failures = 0
            self._retry_after = 0.0
            self._state = LadderState(
                side=side, entry_price=entry_price, cfg=cfg, stop=stop,
                hwm=entry_price if hwm is None else hwm)
            self._cv.notify_all()
        self._log("ladder_armed", side=side, entry=entry_price, stop=stop,
                  dist=cfg.dist, step=cfg.step, be=cfg.be, be_only=cfg.be_only)

    def disarm(self) -> None:
        """The position is closed. Idempotent — the flat reading repeats."""
        with self._cv:
            if self._state is None:
                return
            self._epoch += 1
            rungs = len(self._state.rungs)
            self._state = None
        self._log("ladder_disarmed", rungs=rungs)

    @property
    def armed(self) -> bool:
        with self._cv:
            return self._state is not None

    # --- inputs ------------------------------------------------------------

    def on_prices(self, high: float, low: float) -> None:
        """A batch of prints, as its two extremes.

        **The extremes, not the last print.** A drain can carry a spike the
        closing tick does not show, and the ladder is a function of the high —
        reading only the last price would hand back rungs the trade had earned.
        Both ends are offered because `LadderState.mark` only ever moves the mark
        in the trade's favour, so which one matters is the position's business
        rather than the caller's.

        Cheap on purpose: this runs on the tick path.
        """
        with self._cv:
            if self._state is None:
                return
            before = self._state.hwm
            self._state.mark(high)
            self._state.mark(low)
            if self._state.hwm != before:
                self._cv.notify_all()

    def note_drag(self, level: float) -> None:
        """A stop moved by hand. Re-pins the grid and walks the mark back.

        Without this the drag would survive exactly until the next print: the
        ladder reads the mark, and a mark that already justifies the old rung
        puts the stop straight back — the drag would appear to take, then undo
        itself a tick later.
        """
        with self._cv:
            if self._state is None:
                return
            repin(self._state, level)
        self._log("ladder_repinned", stop=level)

    def snapshot(self) -> dict | None:
        """What the panel draws. None when nothing is being trailed."""
        with self._cv:
            st = self._state
            if st is None:
                return None
            return {"side": st.side, "entry": st.entry_price, "hwm": st.hwm,
                    "stop": st.stop, "armed": st.armed, "ladder": st.ladder,
                    "rungs": len(st.rungs), "dist": st.cfg.dist,
                    "step": st.cfg.step, "be": st.cfg.be,
                    "be_only": st.cfg.be_only, "failures": self._failures,
                    "stats": dict(self.stats)}

    # --- the loop ----------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cv:
                if not self._running:
                    return
                # Decide FIRST and wait only when there is nothing to do. Waiting
                # at the top of the loop instead would drop the notification that
                # arrived while the last modify was in the air, and the ladder
                # would sit out a rung until the idle timeout happened to expire.
                todo = self._pending()
                if todo is None:
                    self._cv.wait(timeout=_IDLE_WAIT_S)
                    continue
                epoch, want = todo
            # Off the lock: this is the 239 ms, and holding the lock across it
            # would make every print on the tick path wait for Chicago.
            self._send(epoch, want)

    def _pending(self) -> tuple[int, float] | None:
        """The rung worth a round trip, or None. Call with the lock held."""
        st = self._state
        if st is None or self._failures >= MAX_CONSECUTIVE_FAILURES:
            return None
        if time.monotonic() < self._retry_after:
            return None
        want = trail_stop(st)
        # `tighten` would answer this too, but it answers by *mutating*, and the
        # rung is not taken until the wire says it was.
        if want is None:
            return None
        if st.stop is not None and (want - st.stop) * st.dir <= 0:
            return None
        return self._epoch, want

    def _send(self, epoch: int, want: float) -> None:
        try:
            self._move(want)
        except LookupError as e:
            # **Not counted against the give-up budget**, and the distinction is
            # the difference between working and not: "there is no leg to move"
            # is what a ladder sees for the moment between its position opening
            # and Rithmic's notification for the stop it attached. Counting that
            # would spend all three lives inside a millisecond and leave the
            # trade untrailed for the rest of its life. Nothing is wrong, the
            # answer is *not yet* — so it backs off and asks again.
            with self._cv:
                if epoch != self._epoch:
                    return
                self._retry_after = time.monotonic() + _RETRY_BACKOFF_S
            self._log("ladder_waiting", level=want,
                      why=f"{type(e).__name__}: {e}")
            return
        except Exception as e:  # noqa: BLE001 — a refused modify is not a crash
            with self._cv:
                if epoch != self._epoch:
                    return
                self._failures += 1
                self.stats["failed"] += 1
                self._retry_after = time.monotonic() + _RETRY_BACKOFF_S
                spent = self._failures
                if spent >= MAX_CONSECUTIVE_FAILURES:
                    self.stats["given_up"] += 1
            self._log("ladder_error", level=want,
                      error=f"{type(e).__name__}: {e}", consecutive=spent)
            if spent >= MAX_CONSECUTIVE_FAILURES:
                # Loud, and on its own line: from here the stop is whatever the
                # broker last accepted and this process will not move it again.
                self._log("ladder_gave_up", level=want, after=spent)
            return
        with self._cv:
            # The position this rung was computed for may have closed while the
            # modify was in the air. Applying it now would put the *next*
            # position's ladder on a level from the last one's tape.
            if epoch != self._epoch or self._state is None:
                self._log("ladder_stale", level=want)
                return
            self._failures = 0
            self._retry_after = 0.0
            moved = tighten(self._state, want)
            hwm = self._state.hwm
            if moved:
                self.stats["rungs"] += 1
        if moved:
            self._log("ladder_rung", level=want, hwm=hwm, at=time.time())
