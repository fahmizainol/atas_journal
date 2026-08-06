"""Developing volume profile — POC / VAH / VAL, from the true tape.

Unlike ``frontend/src/lib/volumeProfile.ts``, which has to *reconstruct* a
profile by spreading each bar's volume across its high-low range, this bins the
actual traded size at the actual traded price. We have the tick stream, so no
reconstruction is needed and none is done: every contract lands on the price it
printed at. The two therefore do not agree to the cent, and the engine trades
this one.

"Developing" means session-anchored and cumulative: the profile at bar *k* is
built from every tick from the session open through the close of bar *k*, which
is what a live developing-profile indicator shows. Levels are recomputed once
per bar close, not once per tick — the value-area scan is O(levels) and a fill
is judged against the last *closed* bar's levels anyway (see engine.py), so a
per-tick profile would cost real time to produce a number nothing may read.

Value area: the classic Market Profile expansion — start at the POC and keep
annexing whichever neighbouring *pair* of levels carries more volume until 70%
of the volume traded so far is enclosed. Comparing pairs rather than single
levels is what stops the area creeping up one thin level at a time on a
lopsided distribution.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

VALUE_AREA_PCT = 0.70

# How many bars back the gap-closer attribution looks. Contact with a level is a
# *drift* touch when, over this window, price's net move toward the level plus the
# level's net move toward price is <= 0 — the two never actually converged, so the
# touch came from tolerance/wiggle inside the zone rather than an approach. The
# Interactions Lab (interactions.py) measures it per minute; the engine's
# drift-touch-fade measures it per bar. Both call ``gap_closer`` so the definition
# lives in exactly one place (the chart/study/engine agreement rule).
GAP_LOOKBACK_BARS = 5


def gap_closer(values: np.ndarray, close: np.ndarray, i: int,
               lookback: int = GAP_LOOKBACK_BARS,
               ) -> tuple[str, float | None, float | None]:
    """Attribute a touch's closing distance: price's move toward the level vs the
    level's move toward price, over the last ``lookback`` bars ending at ``i``.

    ``values`` is the level's path (one entry per bar/minute) and ``close`` the
    matching price closes. Returns ``(cls, price_closed, level_closed)``:

    - ``"drift"`` — the two never converged over the window (their combined move
      toward each other is <= 0). Price was already loitering by the level and
      wiggled into contact: a slow re-test of a hugged zone, the fade signal.
    - ``"level"`` — a falling band chased by price "touches" without price testing
      anything; the level did the closing (share >= 0.6).
    - ``"price"`` — price did the closing (share <= 0.4): a momentum test.
    - ``"both"`` — they met in the middle.
    - ``"unknown"`` — not enough history yet, or a NaN level in the window.

    Lifted out of interactions._gap_closer so the Lab, the charts and the engine
    all read the same arithmetic.
    """
    j = i - min(lookback, i)
    if j >= i or np.isnan(values[j]) or np.isnan(values[i]):
        return "unknown", None, None
    toward = np.sign(values[j] - close[j])  # +1: level overhead, -1: level below
    price_closed = float((close[i] - close[j]) * toward)
    level_closed = float(-(values[i] - values[j]) * toward)
    total = price_closed + level_closed
    if total <= 0:
        return "drift", round(price_closed, 2), round(level_closed, 2)
    share = level_closed / total
    cls = "level" if share >= 0.6 else "price" if share <= 0.4 else "both"
    return cls, round(price_closed, 2), round(level_closed, 2)


@dataclass(frozen=True)
class DevelopingProfile:
    """Per-bar developing levels, positionally aligned to a bar frame.

    ``poc``/``vah``/``val`` are real price levels (a level the tape actually
    printed at), not row edges — there are no rows here to have edges.
    """

    poc: np.ndarray  # (n_bars,)
    vah: np.ndarray
    val: np.ndarray

    def __len__(self) -> int:
        return len(self.poc)


def _value_area(hist: np.ndarray, poc: int, total: float, pct: float) -> tuple[int, int]:
    """(lo, hi) level indices enclosing ``pct`` of ``total``, expanding from the POC.

    The expansion is bounded by the levels that have actually *traded*, not by the
    length of ``hist``. That distinction is load-bearing for the developing case:
    ``developing_profile`` sizes its array from the whole session's price range up
    front, so at bar *k* every level above the running high (and below the running
    low) is a zero the area could still walk into. The pair-step hops over runs of
    empty levels, so once both neighbouring pairs are zero it used to march to the
    array edge and report a VAH/VAL at the session's *eventual* extreme — a price
    nothing had traded at yet, chosen by data from the future.

    Measured on NQU6 2026-06-30 that put the developing VAH at 30599.75 (the day's
    final high) from bar 403, while nothing above 30197.75 had printed. It moved
    ~3.7% of all bars / 1.7% of RTH bars, by a median 82 points. Because the level
    lands *away* from price it can only ever suppress a touch, never invent one —
    so it silently dropped VAH/VAL entries on exactly the days that later ran far,
    which flattered the drift-fade variants by ~4-5% of net.
    """
    nz = np.flatnonzero(hist)
    if nz.size == 0:
        return poc, poc
    bot, top = int(nz[0]), int(nz[-1])

    target = total * pct
    acc = float(hist[poc])
    lo = hi = poc

    while acc < target and (lo > bot or hi < top):
        # Volume of the next two levels beyond each edge; -1 marks an edge that
        # has run out of levels, so the other side always wins the comparison.
        up = float(hist[hi + 1] + (hist[hi + 2] if hi + 2 <= top else 0.0)) if hi < top else -1.0
        down = float(hist[lo - 1] + (hist[lo - 2] if lo - 2 >= bot else 0.0)) if lo > bot else -1.0
        if up < 0 and down < 0:
            break
        if up >= down:
            for _ in range(2):
                if hi >= top:
                    break
                hi += 1
                acc += float(hist[hi])
        else:
            for _ in range(2):
                if lo <= bot:
                    break
                lo -= 1
                acc += float(hist[lo])
    return lo, hi


def developing_profile(
    ticks: pd.DataFrame,
    bars: pd.DataFrame,
    tick_size: float,
    pct: float = VALUE_AREA_PCT,
) -> DevelopingProfile:
    """Cumulative POC/VAH/VAL as of each bar's close, one row per bar.

    ``bars`` must carry ``end_idx`` (inclusive tick positions into ``ticks``), as
    ``bars.tick_bars`` produces. Accumulation starts at the first tick, so the
    caller anchors the session by slicing the tick frame — same contract as
    ``vwap.vwap_bands``.
    """
    if ticks.empty or bars.empty:
        return DevelopingProfile(np.array([]), np.array([]), np.array([]))

    price = ticks["price"].to_numpy(dtype="float64")
    size = ticks["size"].to_numpy(dtype="float64")

    # Price -> integer level index. Rounding to the instrument's tick grid is what
    # makes the histogram dense: raw floats would scatter one bin per distinct
    # price and the pair-expansion would step over holes that aren't really there.
    lv = np.rint(price / tick_size).astype("int64")
    base = int(lv.min())
    idx = lv - base
    n_levels = int(lv.max()) - base + 1

    hist = np.zeros(n_levels, dtype="float64")
    ends = bars["end_idx"].to_numpy(dtype="int64")

    poc = np.full(len(bars), np.nan)
    vah = np.full(len(bars), np.nan)
    val = np.full(len(bars), np.nan)

    total = 0.0
    cursor = 0
    for k, end in enumerate(ends):
        stop = int(end) + 1  # end_idx is inclusive
        if stop > cursor:
            hist += np.bincount(idx[cursor:stop], weights=size[cursor:stop], minlength=n_levels)
            total += float(size[cursor:stop].sum())
            cursor = stop
        if total <= 0:
            continue  # a whole bar of zero-size prints: no profile to speak of yet
        p = int(np.argmax(hist))
        lo, hi = _value_area(hist, p, total, pct)
        poc[k] = (base + p) * tick_size
        vah[k] = (base + hi) * tick_size
        val[k] = (base + lo) * tick_size

    return DevelopingProfile(poc=poc, vah=vah, val=val)


def levels_in_force(
    profile: DevelopingProfile, bars: pd.DataFrame, n_ticks: int, edge: str = "vah"
) -> np.ndarray:
    """One profile level per *tick*, as known to a trader standing at that tick.

    ``edge`` selects which level ("vah" | "val" | "poc"): a long from above value
    is judged against VAH, its short mirror against VAL.

    The value at tick ``i`` is the level of the last bar to have **closed strictly
    before** ``i``. A bar that closes on ``i`` does not count: the engine settles
    fills before it processes that bar, so letting its level apply at ``i`` would
    hand a fill a number derived partly from ticks it hadn't seen yet.

    Ticks before the first bar close get NaN — there is genuinely no profile yet,
    and every caller must decide what that means rather than inherit a zero.
    """
    if edge not in ("vah", "val", "poc"):
        raise ValueError(f"edge must be vah|val|poc, got {edge!r}")
    out = np.full(n_ticks, np.nan)
    if len(profile) == 0 or bars.empty:
        return out
    level = getattr(profile, edge)
    ends = bars["end_idx"].to_numpy(dtype="int64")
    for k, end in enumerate(ends):
        start = int(end) + 1  # in force from the tick *after* the close
        stop = int(ends[k + 1]) + 1 if k + 1 < len(ends) else n_ticks
        if start < n_ticks:
            out[start:min(stop, n_ticks)] = level[k]
    return out
