"""Session-anchored VWAP and deviation bands, computed tick by tick.

This is *the* VWAP definition in this codebase, and the reason it is worth saying
so: accumulating trade prices and accumulating 1-minute bar typical prices are
two different statistics, not two spellings of one. Their mids differ by a tick
or three (hlc3 is only a proxy for a bar's true VWAP) and their sigmas differ by
far more near an anchor — by the law of total variance the tick sigma carries the
within-bar spread as well as the between-bar one, so it is ~40x wider one bar in,
and converges to the bar figure only some tens of bars later. Drawing both under
one name is what made a hand-placed anchor read differently on two charts.

So the bands the engine trades against, the session lines the charts draw, and
every cumulative anchored VWAP on the frontend all come from here or from
``bar_moments`` below — a strategy tested on one sigma and shown on another is
untestable, and a tool that means two things is worse than either.

Volume-weighted, which is the standard (and what ATAS draws):

    vwap = sum(p*v) / sum(v)
    var  = sum(p^2*v) / sum(v) - vwap^2
    dev1 = vwap +/- sqrt(var),  dev2 = vwap +/- 2*sqrt(var)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BAND_COLS = ["mid", "std", "upper1", "upper2", "lower1", "lower2"]


def vwap_bands(ticks: pd.DataFrame,
               seed: tuple[float, float, float] | None = None) -> pd.DataFrame:
    """Running VWAP + 1σ/2σ bands, one row per tick, index-aligned to *ticks*.

    Accumulation starts at the first row, so the caller anchors the session by
    slicing the tick frame (e.g. from the 09:30 ET open) before calling. An
    anchor that predates the frame — the weekly VWAP, anchored at the week's
    Globex open but computed over one session's ticks — is expressed as *seed*:
    the (Σv, Σpv, Σp²v) already accumulated between the anchor and the frame's
    first tick (see ``weekly.weekly_seed``).

    The first few ticks have a near-zero sigma — the bands are degenerate until
    some volume has traded. That is honest rather than a bug: it is exactly what
    a live VWAP looks like seconds after the open. Rules must not lean on them.
    (A seeded call starts with the anchor's volume behind it, so its bands are
    real from the first tick.)
    """
    if ticks.empty:
        return pd.DataFrame(columns=BAND_COLS)

    p = ticks["price"].to_numpy(dtype="float64")
    v = ticks["size"].to_numpy(dtype="float64")

    sv, spv, sp2v = seed if seed is not None else (0.0, 0.0, 0.0)
    cum_v = sv + np.cumsum(v)
    cum_pv = spv + np.cumsum(p * v)
    cum_p2v = sp2v + np.cumsum(p * p * v)

    with np.errstate(divide="ignore", invalid="ignore"):
        mid = cum_pv / cum_v
        var = cum_p2v / cum_v - mid * mid
    # Catastrophic cancellation can push a true-zero variance a hair negative.
    std = np.sqrt(np.clip(var, 0.0, None))

    return pd.DataFrame({
        "mid": mid,
        "std": std,
        "upper1": mid + std,
        "upper2": mid + 2 * std,
        "lower1": mid - std,
        "lower2": mid - 2 * std,
    }, index=ticks.index)


def bar_moments(ticks: pd.DataFrame,
                start_idx: np.ndarray,
                end_idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each bar's own tick VWAP and tick variance, one pair per bar.

    This is what lets a chart draw a *tick-accurate* VWAP anchored anywhere
    without being shipped the tape. Sums are associative, so accumulating these
    forward from any bar reproduces ``vwap_bands`` over the same tick range
    exactly — the frontend rebuilds (Σv, Σpv, Σp²v) from ``(volume, vwap, var)``
    as ``pv = vwap·v`` and ``p²v = (var + vwap²)·v``. Two floats per bar buys
    every anchor the user can place, at bar cost rather than tick cost. Same
    trick ``frame_sums`` plays for the weekly seed, one bar wide.

    ``start_idx``/``end_idx`` are positional into *ticks*, inclusive at both
    ends, exactly as ``bars.BAR_COLS`` carries them.

    The variance is accumulated *about each bar's own mean* rather than as
    E[x²]−E[x]², because the difference-of-cumulative-sums form loses most of its
    significant digits here: p² is ~6e8 on NQ and a bar's true variance is single
    digits, so the naive route subtracts two nearly equal large numbers to get a
    small one. Centring costs one extra pass and is exact.

    NaN for a bar that caught no volume — a time bar over a hole. Callers drop
    those rather than drawing a made-up price; a zeroed moment would silently
    drag an anchored VWAP toward zero.
    """
    n = len(start_idx)
    if ticks.empty or n == 0:
        return np.full(n, np.nan), np.full(n, np.nan)

    p = ticks["price"].to_numpy(dtype="float64")
    v = ticks["size"].to_numpy(dtype="float64")
    s = np.asarray(start_idx, dtype="int64")
    e = np.asarray(end_idx, dtype="int64")

    # Which bar each tick belongs to. `end_idx` is inclusive and ascending, so
    # side="left" lands a tick on the first bar whose end it does not pass —
    # the same mapping session_chart builds for its footprint and CVD lookups.
    bar_of = np.searchsorted(e, np.arange(len(p)), side="left")
    # Ticks before the first bar opens, or past the last one's close, belong to
    # no drawn bar. Both happen: an NY-anchored slice starts mid-frame, and a
    # session can carry recovered ticks past the final bar.
    live = (np.arange(len(p)) >= s[0]) & (bar_of < n)
    bo, pv_p, pv_v = bar_of[live], p[live], v[live]

    vol = np.bincount(bo, weights=pv_v, minlength=n)
    pv = np.bincount(bo, weights=pv_p * pv_v, minlength=n)
    with np.errstate(divide="ignore", invalid="ignore"):
        mid = pv / vol
    d = pv_p - mid[bo]
    var = np.bincount(bo, weights=d * d * pv_v, minlength=n)
    with np.errstate(divide="ignore", invalid="ignore"):
        var = var / vol

    empty = vol <= 0
    mid = np.where(empty, np.nan, mid)
    var = np.where(empty, np.nan, np.clip(var, 0.0, None))
    return mid, var


def frame_sums(ticks: pd.DataFrame) -> tuple[float, float, float]:
    """(Σv, Σpv, Σp²v) over a whole tick frame — one session's contribution to a
    multi-session seed. Summing these across days and passing the total as
    ``vwap_bands(seed=...)`` is exactly equivalent to accumulating over the
    concatenated tick frames."""
    if ticks.empty:
        return (0.0, 0.0, 0.0)
    p = ticks["price"].to_numpy(dtype="float64")
    v = ticks["size"].to_numpy(dtype="float64")
    return (float(v.sum()), float((p * v).sum()), float((p * p * v).sum()))
