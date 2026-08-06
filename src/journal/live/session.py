"""The day so far, held in memory.

A live session is an append-only tick stream plus the two things needed to read
it: which contract and which ET session date it belongs to. It is written by one
feed thread and read by the API's request threads, so every mutation and every
slice goes through one lock.

WHY GROWING TYPED ARRAYS AND NOT A LIST OF FRAMES. The two hot operations are
"append a handful of ticks" (many times a second) and "give me rows [since, n)"
(once per client poll). A list of small DataFrames makes the first cheap and the
second a concat of thousands of pieces; one array doubled on demand makes both
O(rows touched). The engine wants a DataFrame in ``get_day_ticks`` shape, and
that is built on demand from the slice being asked for — the shadow runner asks
for the whole day so far, which is the one place a full frame is materialised.

THE SEGMENT SLICING IS LOAD-BEARING. ``ticks.py``'s READ CONTRACT applies to a
live frame exactly as it does to a cached read: hand an RTH strategy a frame that
quietly carries the overnight in front and every tick bar re-phases and the VWAP
anchor moves to 18:00 — silently, with no error, changing every number. So this
module exposes ``frame_for(overnight=...)`` rather than a raw ``frame()``, and
the two windows it cuts are exactly the ones ``get_day_ticks`` returns:

  - ``overnight=False`` -> 09:30 → 16:00 ET
  - ``overnight=True``  -> prev 18:00 → 16:00 ET  (note: no post hour, same as
    ``get_day_ticks(include_overnight=True)`` — a Globex strategy is anchored at
    18:00 and trades to the bell)

The post hour is still *accepted* into the stream, because the chart draws it and
the weekly seed reads it. It is simply never handed to an engine.
"""

from __future__ import annotations

import threading
from datetime import date

import numpy as np
import pandas as pd

from ..sim import ticks as tickmod

# What the tape is materialised as, matching a cached read column for column so a
# frame built here and a frame read off disk are indistinguishable to the engine.
_SIZE_DTYPE = "uint32"

_INITIAL_CAPACITY = 1 << 16


class LiveSession:
    """One trading session, accumulating.

    ``gen`` is the identity of *this* accumulation. A client holds rows it has
    already decoded and asks for what came after them; if the session it was
    reading has been replaced (a restart, a new day, a different contract) those
    row indices mean nothing, and splicing new rows onto them would produce a
    tape that never existed. Comparing the token is how the client finds out —
    it is a value to be checked, not a number to be trusted.
    """

    def __init__(self, symbol: str, day: date, gen: str) -> None:
        self.symbol = symbol
        self.day = day
        self.gen = gen
        self._lock = threading.Lock()
        self._n = 0
        cap = _INITIAL_CAPACITY
        self._ts = np.empty(cap, dtype="int64")  # epoch ns, UTC
        self._price = np.empty(cap, dtype="float64")
        self._size = np.empty(cap, dtype="int64")
        self._side = np.empty(cap, dtype="U1")
        # Set once the feed says the session is over. The tape stops growing;
        # everything already in it stays readable.
        self.closed = False

    # --- writing ------------------------------------------------------------

    def append(self, frame: pd.DataFrame) -> int:
        """Add ticks to the tail. Returns the new row count.

        ``frame`` carries the ``ticks.py`` columns (ts_utc, price, size, side).
        Rows are trusted to be in wall-clock order and to belong to this session:
        a feed that hands over an out-of-order batch produces a tape the client
        reconstructs wrong rather than an error, which is the same contract the
        cached reads run under.
        """
        if frame is None or frame.empty:
            return self.n
        k = len(frame)
        ts = frame["ts_utc"].values.astype("datetime64[ns]").astype("int64")
        price = frame["price"].to_numpy(dtype="float64")
        size = frame["size"].to_numpy(dtype="int64")
        side = frame["side"].fillna("N").astype(str).str[0].to_numpy(dtype="U1")
        with self._lock:
            self._ensure(self._n + k)
            at = self._n
            self._ts[at:at + k] = ts
            self._price[at:at + k] = price
            self._size[at:at + k] = size
            self._side[at:at + k] = side
            # Published last: a reader that sees the new count is guaranteed the
            # rows behind it are already written.
            self._n = at + k
            return self._n

    def close(self) -> None:
        self.closed = True

    def _ensure(self, need: int) -> None:
        """Grow to hold ``need`` rows, doubling so appends stay amortised O(1)."""
        cap = len(self._ts)
        if need <= cap:
            return
        while cap < need:
            cap *= 2
        self._ts = np.resize(self._ts, cap)
        self._price = np.resize(self._price, cap)
        self._size = np.resize(self._size, cap)
        self._side = np.resize(self._side, cap)

    # --- reading ------------------------------------------------------------

    @property
    def n(self) -> int:
        with self._lock:
            return self._n

    def slice(self, start: int = 0, end: int | None = None) -> pd.DataFrame:
        """Rows ``[start, end)`` as a frame in ``get_day_ticks`` shape.

        Out-of-range bounds are clamped rather than raised on: a client polling
        ``since=n`` when nothing has arrived is the ordinary case, and it should
        get an empty frame, not a 400.
        """
        with self._lock:
            n = self._n
            lo = max(0, min(start, n))
            hi = n if end is None else max(lo, min(end, n))
            # Copy under the lock. The arrays are reallocated on growth, so a
            # view handed out here could be pointing at an abandoned buffer by
            # the time the caller reads it.
            ts = self._ts[lo:hi].copy()
            price = self._price[lo:hi].copy()
            size = self._size[lo:hi].copy()
            side = self._side[lo:hi].copy()
        return pd.DataFrame({
            "ts_utc": pd.to_datetime(ts, utc=True),
            "price": price,
            "size": size.astype(_SIZE_DTYPE),
            "side": side.astype(str),
        })

    def frame_for(self, overnight: bool) -> pd.DataFrame:
        """The day so far, cut to the window an engine reads.

        Exactly the windows ``get_day_ticks`` returns — see this module's
        docstring for why handing over anything else is a silent corruption
        rather than an error.
        """
        f = self.slice()
        if f.empty:
            return f
        rth_open, rth_close = tickmod.session_bounds_utc(self.day)
        lo = tickmod.overnight_bounds_utc(self.day)[0] if overnight else rth_open
        ts = f["ts_utc"]
        return f[(ts >= lo) & (ts < rth_close)].reset_index(drop=True)

    def overnight_frame(self) -> pd.DataFrame | None:
        """The night alone — prev 18:00 → 09:30 ET — or None if none arrived.

        Not a window any engine reads (a Globex strategy wants the night *and*
        the session, which is ``frame_for(overnight=True)``). It exists for
        ``regime.compute_regime``, which takes the two segments separately
        because whether the night is there at all is what decides if the session
        has a Globex anchor to describe.
        """
        f = self.slice()
        if f.empty:
            return None
        lo = tickmod.overnight_bounds_utc(self.day)[0]
        hi = tickmod.session_bounds_utc(self.day)[0]
        ts = f["ts_utc"]
        on = f[(ts >= lo) & (ts < hi)].reset_index(drop=True)
        return None if on.empty else on

    def last_ts(self) -> pd.Timestamp | None:
        """The newest tick's instant, or None on an empty tape."""
        with self._lock:
            if self._n == 0:
                return None
            return pd.Timestamp(self._ts[self._n - 1], tz="UTC")
