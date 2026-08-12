"""A fake feed: a cached session, replayed into a LiveSession at wall-clock speed.

This is what Phases 2-4 are built and watched against. It exists because the
live stack's hard parts — a tape that grows under a chart, a blotter that cannot
rewind, prefix re-runs of the engine on a cadence — are all about *arrival*, and
none of them care whether the ticks come from Rithmic or from a parquet on disk.
Developing against a real feed would mean only being able to work while the
market is open, and it would put the recorder (Phase 5) on the critical path for
no reason.

READ-ONLY, CACHE-ONLY. The source is a session already in the Databento cache and
is never fetched — picking a day must never spend money, the same rule the
Simulator's endpoints run under. Nothing is written back: see the package
docstring for the hazard class that not-writing deletes.

WHAT "WALL-CLOCK SPEED" MEANS. The tape keeps its own historical timestamps; what
is paced is their *arrival*. At ``speed=1`` a session takes as long to replay as
it took to happen. The chart's clock comes from the last print received
(``liveSource`` in lib/tapeSource.ts), so it reads as the historical day's clock
advancing in real time — which is exactly the shape a real feed has, with the one
difference that you can pick the day and run it faster than it happened.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime
from datetime import time as dtime

import pandas as pd

from ..config import ET_TZ
from ..sim import ticks as tickmod
from .session import LiveSession

# How often the feed wakes to publish whatever has "arrived". Matched to the
# Rithmic feed's PUBLISH_S so a replayed day and a live one are quantised the
# same — with the tape now *pushed* on every append (session.subscribe), this
# wake is the only grain the chart can see, and a fake feed that published on a
# coarser one would look choppier than the real thing it exists to stand in
# for. At 60x each wake still batches ~1.2s of tape, nowhere near one wake per
# tick.
_WAKE_S = 0.02


def source_frame(symbol: str, day: date) -> pd.DataFrame | None:
    """A cached session's whole tape (on + rth + post), or None if it isn't there.

    The same splice the Simulator's session endpoint makes, for the same reason:
    the three windows partition the trading day contiguously, so concatenating
    them in order is already sorted. RTH is what must be present — a day with
    only an overnight is not a session to replay.
    """
    rth = tickmod.cached_rth(symbol, day)
    if rth is None or rth.empty:
        return None
    parts = [f for f in (tickmod.cached_overnight(symbol, day), rth,
                         tickmod.cached_post(symbol, day))
             if f is not None and not f.empty]
    return pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]


class FakeFeed:
    """Streams ``frame`` into ``session`` on a background thread.

    ``speed`` multiplies tape time against wall time. ``start_at`` is an ET
    wall-clock time to open at: everything before it is published in one batch
    before pacing begins, which is what a live session looks like when you load
    the page at eleven in the morning — the day up to now is simply there.
    """

    def __init__(self, session: LiveSession, frame: pd.DataFrame,
                 speed: float = 1.0, start_at: dtime | None = None) -> None:
        self.session = session
        self._frame = frame
        self.speed = max(0.01, float(speed))
        self.start_at = start_at
        self._ts = frame["ts_utc"].values.astype("datetime64[ns]").astype("int64")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="live-fake-feed",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the thread to finish and wait briefly for it.

        Bounded rather than joined outright: this is called from a request
        handler (and from app shutdown), and a feed thread that has wedged must
        not take the API down with it. The thread is a daemon, so a straggler
        dies with the process either way.
        """
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
        self._thread = None

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # --- the loop -----------------------------------------------------------

    def _prime_index(self) -> int:
        """How many rows count as "already happened" when the feed opens."""
        if self.start_at is None:
            return 0
        cut = pd.Timestamp(datetime.combine(self.session.day, self.start_at),
                           tz=ET_TZ).tz_convert("UTC")
        return int(self._ts.searchsorted(cut.value, side="left"))

    def _run(self) -> None:
        ts = self._ts
        n = len(ts)
        i = self._prime_index()
        if i > 0:
            self.session.append(self._frame.iloc[:i])
        if i >= n:
            self.session.close()
            return
        # The pacing origin: tape time and wall time are pinned together here,
        # and every publish since is measured off this pair rather than off the
        # previous wake. Accumulating per-wake deltas would let scheduling jitter
        # drift the tape clock away from the wall clock over a session.
        tape0 = ts[i]
        wall0 = time.monotonic()
        while not self._stop.is_set():
            elapsed_ns = (time.monotonic() - wall0) * self.speed * 1e9
            j = int(ts.searchsorted(tape0 + elapsed_ns, side="right"))
            if j > i:
                self.session.append(self._frame.iloc[i:j])
                i = j
            if i >= n:
                self.session.close()
                return
            self._stop.wait(_WAKE_S)
