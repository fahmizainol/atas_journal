"""Volume shelves: where size is *building*, as opposed to where size has been.

The port of ``frontend/src/lib/volumeShelf.ts``. Read that module first — it
carries the reasoning (why the denominator is the whole thing, why a shelf is a
threshold-crossing run rather than a prominence hump, and what this layer does
and does not claim). This file exists for one reason the TS cannot serve: the
level tagger runs in Python, and it can only measure a fill against a level that
arrives on the ``SessionFrame``.

Two ports of one arithmetic are two things that can drift, so they are pinned
against each other by ``tests/test_vol_shelf.py`` over a fixture the *TypeScript*
generates (``tools/vol-shelf/run.sh``). The TS is the original and this is the
copy; when they disagree, this one is wrong until proven otherwise.

The parity traps that actually bit, so nobody re-derives them:

  rounding    JavaScript's ``Math.round`` is floor(x + 0.5) — it breaks ties
              upward, always. Python's ``round`` is banker's rounding and breaks
              them toward even. On a profile whose row pitch lands a price
              exactly on a boundary the two put it in different rows, silently.
              ``_jsround`` below is the only rounding this module uses.

  odd width   ``Math.trunc(n * smooth) | 1`` coerces through int32 in JS. The
              Python ``int(...) | 1`` matches for the sizes involved, but it is
              the *truncation* that has to be int-toward-zero and not floor, so
              ``int()`` and not ``math.floor()``.

  sd          Population standard deviation (divide by N), not the sample one.
              numpy's ``std`` default already does this; ``statistics.stdev``
              does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: Above this many price levels the rows would be sub-pixel, so ticks get grouped
#: into fatter rows. Mirrors `MAX_LEVELS` in lib/volumeProfile.ts.
MAX_LEVELS = 2000

#: Fewest occupied rows a window may have and still be scored — mirrors
#: ``MIN_OCCUPIED_ROWS``. Not a taste threshold: a z-score over ``n`` points
#: cannot exceed ``(n-1)/sqrt(n)``, so at n=5 the ceiling is 1.79 and a ``z_min``
#: of 2 is unreachable however concentrated the size was. Twenty rows puts the
#: ceiling at 4.25. A thinner window returns *no reading* rather than a quiet
#: zero, because "no shelf" and "cannot tell" must not look the same.
MIN_OCCUPIED_ROWS = 20

DEFAULT_WINDOW_MIN = 30
DEFAULT_Z_MIN = 2.0
DEFAULT_MIN_TICKS = 4
#: How long a band must keep qualifying before it counts. The "for a certain
#: period of time" half of the question, and not an optional refinement: without
#: it a real NQ session yields ~290 bands and something is "a shelf" on 87% of
#: bars. One window catching a burst is an event; a band that keeps reappearing
#: window after window is the thing worth drawing.
DEFAULT_MIN_HOLD_MIN = 10
DEFAULT_SMOOTH = 0.04
#: How often the window is re-read, in seconds of chart time. A reading covers
#: the trailing ``window_min``, so two readings a second apart are the same half
#: hour with one bar swapped — re-reading per bar is cost with no content, and on
#: a tick-bucketed session there are hundreds of bars each wanting their own
#: profile and TPO pass.
DEFAULT_STEP_SEC = 60


def _jsround(x: float) -> int:
    """JavaScript's ``Math.round``: ties break upward, never toward even."""
    return int(math.floor(x + 0.5))


def _jsround_arr(x: np.ndarray) -> np.ndarray:
    """``_jsround`` over an array. Same rule — floor(x + 0.5) — and not
    ``np.round``, which is banker's rounding and would disagree with the
    TypeScript on exactly the boundary prices that decide a row."""
    return np.floor(x + 0.5).astype(np.int64)


@dataclass(frozen=True)
class ShelfParams:
    window_min: int = DEFAULT_WINDOW_MIN
    z_min: float = DEFAULT_Z_MIN
    min_ticks: int = DEFAULT_MIN_TICKS
    min_hold_min: int = DEFAULT_MIN_HOLD_MIN
    smooth: float = DEFAULT_SMOOTH
    step_sec: int = DEFAULT_STEP_SEC


@dataclass(frozen=True)
class Shelf:
    lo: float
    hi: float
    price: float
    z: float


@dataclass
class ShelfBox:
    """A shelf followed across bars, and what has happened to it since.

    A box outlives its detection: the band stops qualifying once the window has
    moved past the size that made it, but the claim it makes stands until price
    comes back. ``to_time`` therefore keeps running while it is untested and
    freezes on the bar price returns, so an untested shelf always reaches the
    live edge. See ``ShelfBox`` in the TS for the full reasoning.
    """
    lo: float
    hi: float
    from_time: int
    #: Right edge as drawn — detection, then standing, then frozen on the return.
    to_time: int
    #: Last time the band was actually detected. The hold gate reads this and
    #: never ``to_time``: otherwise a band that qualified for two minutes would
    #: satisfy a five-minute hold just by standing untested for three more.
    detected_to: int
    z: float
    live: bool
    #: A bar has traded wholly clear of the band — detected or not. Nothing can
    #: clear a shelf before this — price is *inside* the band while it forms, so
    #: testing straight away would clear almost every shelf against price that
    #: had never gone anywhere; and the whole bar rather than its close, because
    #: near a band still building, closes hop off it while the bar goes on
    #: trading in it. Indifferent to detection on purpose: departure is a fact
    #: about price, detection a fact about the trailing window, and gating on
    #: detection lapsing left a window-long blind stretch in which a revisit
    #: counted for nothing (see ``armed`` in the TS for the full reasoning).
    armed: bool = False
    #: Price has traded back into the band since arming. A live box this happens
    #: to is retired on the spot — what the next window re-detects there is a new
    #: claim, owing the hold gate from scratch.
    cleared: bool = False


@dataclass(frozen=True)
class Profile:
    """The bit of a volume profile a shelf reads: row edges and row volume.

    Deliberately not the full ``VolumeProfile`` the frontend builds — POC, value
    area and the delta lane are all irrelevant here, and mirroring them would be
    three more things to keep in parity for no reader.
    """
    low: np.ndarray
    high: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return len(self.volume)


def tick_profile(entries: list[list[float]], tick_size: float) -> Profile | None:
    """The exact profile off a footprint slice — the port of
    ``computeTickProfile``, minus the fields a shelf never reads.

    ``entries`` is the flattened ``[price, size, ...]`` rows of every trade in
    the window (``api.session_chart._footprint``'s per-bar lists, concatenated).
    """
    if not entries or not (tick_size > 0):
        return None
    prices = np.asarray([e[0] for e in entries], dtype=float)
    sizes = np.asarray([e[1] for e in entries], dtype=float)
    return tick_profile_arrays(prices, sizes, tick_size)


def tick_profile_arrays(
    prices: np.ndarray, sizes: np.ndarray, tick_size: float,
) -> Profile | None:
    """:func:`tick_profile` over columns that are already arrays.

    The arithmetic lives here and the list form delegates, because the caller
    that matters — ``shelf_series``, which profiles one window per bar — holds
    the whole session's footprint as two flat arrays and slices it. Rebuilding
    Python lists per window and rounding each price in a comprehension was most
    of this module's cost: about 2.9 million ``_jsround`` calls on a single
    session, which came to roughly the price of building the entire SessionFrame.
    """
    if len(prices) == 0 or not (tick_size > 0):
        return None
    lo = float(prices.min())
    hi = float(prices.max())
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None

    levels = _jsround((hi - lo) / tick_size) + 1
    group = max(1, math.ceil(levels / MAX_LEVELS))
    step = tick_size * group
    # Round, not floor, and the same rounding as the row lookup below — see the
    # note in computeTickProfile: flooring sizes the array one row short whenever
    # (max - min) / step lands on a half, and the top price indexes off the end.
    row_count = _jsround((hi - lo) / step) + 1

    centre = lo + np.arange(row_count) * step
    volume = np.zeros(row_count, dtype=float)
    np.add.at(volume, _jsround_arr((prices - lo) / step), sizes)

    if volume.sum() <= 0:
        return None
    return Profile(low=centre - step / 2, high=centre + step / 2, volume=volume)


def time_at_price(p: Profile, bars) -> np.ndarray:
    """How many *seconds* of these bars traded each row — the port of
    ``timeAtPrice``.

    Duration-weighted rather than a bar count, because a bar is only a time
    slice on a time-bucketed chart and most of these charts are tick- or
    volume-bucketed — activity-clocked, so counting bars puts activity in the
    denominator of the very ratio the numerator measures and ``volume / TPO``
    collapses toward the bucket size. On uniformly-spaced bars the weights are
    one constant factor and the z-scores, being scale-invariant, are identical
    to the bar-count version. See ``timeAtPrice`` in the TS for the full
    reasoning; the duration rule (forward gap, last bar reuses the previous
    gap, non-positive gaps fall back likewise) must match it step for step.

    ``bars`` is anything with ``high``/``low``/``time`` columns or keys per
    bar; a DataFrame slice and a list of dicts both work.
    """
    highs, lows, times = _bar_bounds(bars)
    n = len(times)
    durs = np.empty(n, dtype=float)
    prev = 1.0
    for k in range(n):
        gap = times[k + 1] - times[k] if k + 1 < n else 0.0
        durs[k] = gap if gap > 0 else prev
        if gap > 0:
            prev = gap
    # Overlap test broadcast over (bars x rows): a bar counts for a row when
    # their price spans touch at all, which is the classic Market Profile rule.
    #
    # Broadcast rather than looped because this is the hot path — it runs once
    # per bar over a whole session's worth of windows, and the Python-level loop
    # it replaces cost about as much as building the entire SessionFrame. The
    # array is bars x rows, a few tens of thousands of floats for a 30-minute
    # window, so there is nothing to stream. (The duration loop above is over
    # bars alone — one axis, not the product — and is not worth vectorising past
    # its forward-fill.)
    hit = (lows[:, None] <= p.high[None, :]) & (highs[:, None] >= p.low[None, :])
    return (hit * durs[:, None]).sum(axis=0)


def _bar_bounds(bars) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if hasattr(bars, "columns"):           # DataFrame
        return (
            bars["high"].to_numpy(dtype=float),
            bars["low"].to_numpy(dtype=float),
            bars["time"].to_numpy(dtype=float),
        )
    highs = np.asarray([b["high"] for b in bars], dtype=float)
    lows = np.asarray([b["low"] for b in bars], dtype=float)
    times = np.asarray([b["time"] for b in bars], dtype=float)
    return highs, lows, times


def _smoothed(vol: np.ndarray, k: int) -> np.ndarray:
    """Centred rolling mean, partial at the edges — the port of ``smoothed``,
    which is pandas' ``rolling(k, center=True, min_periods=1).mean()``."""
    n = len(vol)
    csum = np.concatenate(([0.0], np.cumsum(vol)))
    half = (k - 1) >> 1
    a = np.maximum(0, np.arange(n) - half)
    b = np.minimum(n, np.arange(n) + half + 1)
    return (csum[b] - csum[a]) / (b - a)


def shelf_residual(
    p: Profile, time_at: np.ndarray, smooth: float = DEFAULT_SMOOTH,
) -> tuple[np.ndarray, np.ndarray, int]:
    """``(residual, z, occupied)`` — the port of ``shelfResidual``.

    ``residual`` is smoothed volume per second-at-price; ``z`` is NaN on rows
    nothing reached, which is the population the scores were taken over.
    """
    n = len(p)
    raw = np.where(time_at > 0, p.volume / np.where(time_at > 0, time_at, 1.0), 0.0)

    # Zero-padded before smoothing: `_smoothed` takes a *partial* mean at the
    # edges, so a tall block at the last row averages over fewer and fewer terms
    # and the curve rises into the boundary instead of decaying. A profile's rows
    # stop exactly where the session's range does, which is where a spike high
    # lives — the shape this layer most wants to catch.
    k = max(3, int(n * smooth) | 1)
    pad = max(2, (k - 1) >> 1)
    wide = np.zeros(n + 2 * pad, dtype=float)
    wide[pad:pad + n] = raw
    residual = _smoothed(wide, k)[pad:pad + n]

    occ = time_at > 0
    occupied = int(occ.sum())
    z = np.full(n, np.nan)
    if occupied < 2:
        return residual, z, occupied

    mean = float(residual[occ].mean())
    sd = float(np.sqrt(((residual[occ] - mean) ** 2).sum() / occupied))
    # Every occupied row carrying the same size per visit has no standout by
    # construction. NaN says that; dividing by zero would say every row was
    # infinitely remarkable.
    if not sd > 0:
        return residual, z, occupied

    z[occ] = (residual[occ] - mean) / sd
    return residual, z, occupied


def detect_shelves(
    p: Profile | None, bars, exact: bool, tick_size: float,
    params: ShelfParams = ShelfParams(),
) -> tuple[list[Shelf], np.ndarray | None]:
    """The shelves in one window — the port of ``detectShelves``.

    ``exact`` is the caller's statement that this profile came off real
    volume-at-price rather than off a bar-spreading estimate; under the estimate
    ``volume / TPO`` is near-constant by construction and every reading would be
    an artefact of the estimator. Required, and not sniffed for, because there is
    no honest structural signal to sniff.
    """
    if p is None or not exact or len(p) == 0 or not (tick_size > 0):
        return [], None
    highs, _, _ = _bar_bounds(bars)
    if len(highs) == 0:
        return [], None

    time_at = time_at_price(p, bars)
    residual, z, occupied = shelf_residual(p, time_at, params.smooth)
    # Too thin to score — see MIN_OCCUPIED_ROWS. The z curve still goes back so a
    # caller can draw what little there was; only the thresholded read is withheld.
    if occupied < MIN_OCCUPIED_ROWS:
        return [], z

    n = len(p)
    # A row is not always one tick: rows get grouped when a session's range is
    # wide (MAX_LEVELS), so a width in ticks converts through the actual pitch.
    ticks_per_row = max((p.high[0] - p.low[0]) / tick_size, 1e-9)
    min_rows = max(1, _jsround(params.min_ticks / ticks_per_row))

    shelves: list[Shelf] = []
    start = -1
    for i in range(n + 1):
        over = i < n and math.isfinite(z[i]) and z[i] >= params.z_min
        if over and start < 0:
            start = i
        if over or start < 0:
            continue
        if i - start >= min_rows:
            seg = slice(start, i)
            best = start + int(np.argmax(z[seg]))
            shelves.append(Shelf(
                lo=float(p.low[seg].min()),
                hi=float(p.high[seg].max()),
                price=float((p.low[best] + p.high[best]) / 2),
                z=float(z[best]),
            ))
        start = -1
    return shelves, z


class ShelfTracker:
    """Shelves followed across bars — the port of ``ShelfTracker``.

    A reading is per-window; a *shelf* is the thing that keeps showing up in
    consecutive windows. Fed one window's detection per bar close, in bar order.
    """

    def __init__(
        self, grace_bars: int = 2,
        min_hold_sec: int = DEFAULT_MIN_HOLD_MIN * 60,
    ) -> None:
        self.grace_bars = grace_bars
        self.min_hold_sec = min_hold_sec
        self._active: list[ShelfBox] = []
        self._done: list[ShelfBox] = []
        self._missed: list[int] = []
        self._last_time = -math.inf

    def _held(self, b: ShelfBox) -> bool:
        return b.detected_to - b.from_time >= self.min_hold_sec

    def push(self, time: int, shelves: list[Shelf], window=()) -> None:
        """One window's detection, plus the bars it was taken over.

        ``window`` is the same slice handed to :func:`detect_shelves`. Only the
        bars in it newer than the previous push are swept: consecutive readings'
        slices overlap almost entirely, and re-walking them would let a bar from
        before a shelf existed clear it. The whole slice and not just the
        reading's own bar, because readings are taken on a clock and bars are
        not — a wick that entered a band and left again between two readings is
        exactly the revisit this is for.
        """
        matched: set[int] = set()
        for s in shelves:
            at = next(
                (i for i, b in enumerate(self._active)
                 if i not in matched and b.lo <= s.hi and b.hi >= s.lo),
                None,
            )
            if at is not None:
                b = self._active[at]
                # The span tracks the latest reading — a shelf that thickens or
                # drifts is still that shelf.
                b.lo, b.hi, b.to_time = s.lo, s.hi, time
                b.detected_to = time
                b.z = max(b.z, s.z)
                b.live = True
                self._missed[at] = 0
                matched.add(at)
                continue
            self._active.append(ShelfBox(s.lo, s.hi, time, time, time, s.z, True))
            self._missed.append(0)
            matched.add(len(self._active) - 1)

        keep: list[ShelfBox] = []
        kept_miss: list[int] = []
        for i, b in enumerate(self._active):
            if i in matched:
                keep.append(b)
                kept_miss.append(0)
                continue
            miss = self._missed[i] + 1
            if miss > self.grace_bars:
                b.live = False
                self._done.append(b)
            else:
                keep.append(b)
                kept_miss.append(miss)
        self._active, self._missed = keep, kept_miss

        # Arm and clear, closed and still-detected boxes alike — departure and
        # return are facts about price, and whether the trailing window still
        # remembers the size has no bearing on either. An arming bar trades
        # wholly clear of the band, so it cannot also be the return — the clear
        # always comes from a later bar, by construction rather than by rule.
        def sweep(box: ShelfBox, bar: dict, t: int) -> None:
            if box.cleared:
                return
            if not box.armed:
                if bar["high"] < box.lo or bar["low"] > box.hi:
                    box.armed = True
                return
            if bar["low"] <= box.hi and bar["high"] >= box.lo:
                box.cleared = True
                box.to_time = t

        for bar in window:
            t = int(bar["time"])
            if t <= self._last_time:
                continue
            for box in self._done:
                sweep(box, bar, t)
            for box in self._active:
                sweep(box, bar, t)
        # A still-detected box that just got cleared retires now: what the next
        # window re-detects there is a new claim owing the hold gate from
        # scratch, not this box growing past the bar that tested it.
        for i in range(len(self._active) - 1, -1, -1):
            if not self._active[i].cleared:
                continue
            box = self._active.pop(i)
            self._missed.pop(i)
            box.live = False
            self._done.append(box)
        self._last_time = time
        # Whatever is still standing runs to the live edge.
        for box in self._done:
            if not box.cleared:
                box.to_time = time

    def boxes(self) -> list[ShelfBox]:
        """Every shelf that held long enough, oldest first. Bands that qualified
        for a window or two and vanished are dropped here rather than by the
        caller, so a reader of ``boxes`` and a reader of ``strongest`` cannot
        disagree about what a shelf is."""
        return sorted(
            (b for b in [*self._done, *self._active] if self._held(b)),
            key=lambda b: b.from_time,
        )

    def nearest(self, price: float) -> ShelfBox | None:
        """The shelf closest to ``price`` that has held and that price has not
        been back to — what the level tagger measures fills against. None when
        none qualifies.

        Standing, not merely detected: a band whose size the window has forgotten
        is still a band price has not returned to, and "did I fill on a shelf
        nobody had come back to?" is the question this measurement exists to make
        askable. Restricting it to actively-detected shelves made that question
        unposable, because the window forgets a shelf long before price deals
        with it.

        Nearest and not strongest, which is what this returned first and which
        those two changes together made wrong. While only *live* shelves counted,
        the strongest was also near price by construction — a detected shelf sits
        inside a window of recent bars. An untested one does not: on a real
        session the day's strongest untested band stayed named for 72% of the
        readings, from over a hundred points away, so a fill sitting exactly on a
        weaker untested shelf was scored against the far one and came back "not
        at a level". The tagger's whole job is a distance, so the band it is
        handed has to be the one price was actually near.
        """
        best: ShelfBox | None = None
        best_gap = math.inf
        for b in [*self._active, *self._done]:
            if b.cleared or not self._held(b):
                continue
            gap = 0.0 if b.lo <= price <= b.hi else min(
                abs(price - b.lo), abs(price - b.hi),
            )
            # Ties go to the stronger band — two shelves the same distance away
            # is a coin flip otherwise, and a coin flip is not a reading.
            if gap < best_gap or (gap == best_gap and best is not None and b.z > best.z):
                best, best_gap = b, gap
        return best


def shelf_series(
    bars: list[dict], footprint: list[list[list[float]]], tick_size: float,
    params: ShelfParams = ShelfParams(),
) -> list[dict]:
    """The level series the tagger reads: the nearest *untested* shelf's edges,
    per bar, as ``{time, hi, lo}``.

    Untested rather than actively-detected, which is what this emitted at first.
    A two-hour window forgets a band long before price comes back to deal with
    it, so scoring fills only against live detections meant the interesting case
    — a fill on a zone nobody had returned to — could never be measured at all.
    A shelf leaves this series when price trades back into it, not when the
    window stops noticing it.

    One row per *reading* (see :func:`eval_bars` — every ``step_sec`` of chart
    time), and a row at every one of them, with ``nan`` edges when no shelf
    qualifies. Emitting a row even when there is no shelf is load-bearing rather
    than tidy: ``level_tag.DayLevels.at`` answers with the last row at or before
    an instant and has no staleness bound, which is right for the levels it was
    built for — a VWAP, an EMA and a developing value area exist on every bar, so
    "the last one" is always "the current one". A shelf does not. Skipping the
    empty readings made the tagger hand back a band that had died hours earlier,
    and nothing anywhere raised a word, because a stale float and a live float
    are the same float. With a row at every reading the worst staleness a fill
    can be scored against is ``step_sec``.

    ``nan`` is the spelling of absent that the rest of the module already
    understands: ``member_distance`` and ``family_distance`` both test
    ``isfinite`` and skip, so a bar with no shelf contributes no distance and no
    rank rather than a wrong one.

    Only *one* shelf, though the chart draws them all: a family collapses to its
    nearest member, and emitting three would put three identically-labelled
    options in the review picker, which makes "which level were you watching?"
    unanswerable. Stated in the plan and repeated here because it is the kind of
    scope limit that later reads as an oversight.
    """
    if not bars or not footprint or len(footprint) != len(bars):
        return []

    # The whole session's volume-at-price as two flat arrays plus per-bar
    # offsets, so a window is a contiguous slice rather than a rebuilt list. The
    # windows overlap heavily — consecutive bars share nearly all their rows — so
    # rebuilding one per bar re-did the same work a few hundred times over.
    prices = np.asarray([e[0] for bar in footprint for e in bar], dtype=float)
    sizes = np.asarray([e[1] for bar in footprint for e in bar], dtype=float)
    offset = np.zeros(len(footprint) + 1, dtype=np.int64)
    np.cumsum([len(bar) for bar in footprint], out=offset[1:])

    tracker = ShelfTracker(min_hold_sec=params.min_hold_min * 60)
    out: list[dict] = []
    for i in eval_bars(bars, params.step_sec):
        j = window_start(bars, i, params.window_min)
        a, b = int(offset[j]), int(offset[i + 1])
        prof = tick_profile_arrays(prices[a:b], sizes[a:b], tick_size)
        win = bars[j:i + 1]
        shelves, _ = detect_shelves(prof, win, True, tick_size, params)
        tracker.push(int(bars[i]["time"]), shelves, win)
        best = tracker.nearest(float(bars[i]["close"]))
        out.append({
            "time": int(bars[i]["time"]),
            "hi": best.hi if best is not None else math.nan,
            "lo": best.lo if best is not None else math.nan,
        })
    return out


def eval_bars(bars, step_sec: int) -> list[int]:
    """Which bars to take a reading at — the port of ``evalBars``.

    The first, the last, and one every ``step_sec`` of chart time between. The
    last is always included whatever the spacing: it is the live edge, and on a
    replay or a live chart a shelf that appears a minute late is one that
    appeared after the decision it was meant to inform.
    """
    n = len(bars)
    if n == 0:
        return []
    out = [0]
    last = int(bars[0]["time"])
    for i in range(1, n - 1):
        t = int(bars[i]["time"])
        if t - last < step_sec:
            continue
        out.append(i)
        last = t
    if n > 1:
        out.append(n - 1)
    return out


def window_start(bars, i: int, window_min: int) -> int:
    """First bar index of the trailing window ending at bar ``i`` — the port of
    ``windowStart``.

    Walked back by each bar's own timestamp rather than by a bar count, because
    none of these charts is reliably one bar a minute: they are tick-, volume-
    and custom-interval bucketed, so a 30-minute window is a handful of bars
    overnight and dozens through the open. Estimating one bar count for the whole
    day off the spacing of the first two puts most of the session on the wrong
    window, quietly, since every window still produces *a* reading.
    """
    cutoff = int(bars[i]["time"]) - window_min * 60
    j = i
    while j > 0 and int(bars[j - 1]["time"]) >= cutoff:
        j -= 1
    return j
