"""The Modern VWAP's POC anchor, computed server-side.

The indicator itself lives in the frontend (``frontend/src/lib/modernVwap.ts``)
because it is a *drawing*: bands, a two-axis regime shade, MR/TC signals, all of
it read off the bars the chart already holds. None of that is needed here. What
is needed is the one number the anchor produces — the anchored VWAP's mid line —
because ``journal.level_tag`` scores fills against *levels*, and a level is a
price series with a time axis. So this is a narrow port: the anchor rule and the
accumulator, no regime, no bands, no signals.

**Cross-referenced with ``computeModernVwap`` in
``frontend/src/lib/modernVwap.ts`` (the ``p.anchor === "poc"`` branch and the
accumulator directly under it), and it must stay equal to it.** The chart draws
one line and the tagger measures another the moment these disagree, and the
disagreement would be invisible: both sides produce a plausible VWAP.

Both POC re-arm modes are ported, at the ``rearmTicks`` the backlog settled on.
The swing and clock anchors are not: they are drawing modes with no level behind
them worth measuring, and porting a mode nobody asked for would be a second
thing to keep in sync for free.

WHAT THE ANCHOR IS. A bar whose range spans the developing POC, while armed,
anchors a fresh VWAP there. What differs is what has to happen before it can
anchor again, and the two rules ask different questions of the same level:

``pocMove`` — the *level* must move ("naked POC"). The anchor **disarms
unconditionally** after firing: whipsawing across the same price does not anchor
again. It re-arms once the developing POC has itself migrated at least
``REARM_TICKS`` from the price the last anchor fired at, and then the first bar
to touch the new level fires. One anchor per migration, not one per revisit.

``distance`` — *price* must leave. The anchor disarms after firing and re-arms
on any bar whose close is at least ``REARM_TICKS`` from the POC *as of that bar*,
so a wide bar can re-arm and touch in one go. This counts genuine returns to a
level price had walked away from, including returns to a level the accumulator
has already used — which is exactly the half ``pocMove`` throws out.

CAUSAL. Every value at bar *i* is built from bars at or before *i*: the anchor
fires on the bar that touches (a touch is known on its own bar — no confirmation
lag, unlike a swing pivot), and there is no backfill on this anchor. The
developing POC handed in is itself the engine's own developing accumulation, so
it carries no lookahead either.

UNVALIDATED. Nothing here is evidence that the level works. It is drawn on the
charts and now measured against fills; whether fills cluster on it is the
question, and this module only makes the question askable.
"""

from __future__ import annotations

import numpy as np

#: Ticks the re-arm rule measures — how far the POC must migrate on ``pocMove``,
#: how far price must be from it on ``distance``. The same number for both, so
#: the two families differ by rule and not by threshold.
#:
#: **Cross-referenced with ``MV_REARM_OPTIONS`` in
#: ``frontend/src/lib/modernVwap.ts``**, which offers 0/10/25/50; this is the 50.
#: A chart set to any of the others draws a different line than the one measured
#: here, which is why the measured configuration is pinned in code rather than
#: read from a user setting: a level family whose definition moves with a chart
#: knob cannot be compared across trades.
REARM_TICKS = 50


#: The two re-arm rules, spelled as the chart's ``rearmMode`` values so a slot
#: name, a family and a knob setting can be read against each other.
MODES = ("pocMove", "distance")


def poc_anchor_events(times: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                      closes: np.ndarray, poc: dict[int, float], tick: float,
                      mode: str,
                      rearm_ticks: int = REARM_TICKS) -> np.ndarray:
    """Which bars anchor, under ``mode``. Boolean, one entry per bar.

    ``poc`` maps a bar time to the developing POC as of that bar; bars missing
    from it are skipped entirely (the level does not exist yet, so it can be
    neither touched nor migrated away from). Bar 0 always anchors — Pine's
    ``barstate.isfirst``, and the reason the line exists before the first touch.

    ``mode`` is required rather than defaulted: the two rules produce different
    lines, each of which is a separate level family, and a default would let a
    caller measure fills against whichever one this file happened to prefer.
    """
    if mode not in MODES:
        raise ValueError(f"unknown re-arm mode {mode!r}, expected one of {MODES}")
    by_move = mode == "pocMove"
    n = len(times)
    ev = np.zeros(n, dtype=bool)
    if n == 0:
        return ev
    armed = True
    fired_at = np.nan          # the POC price the last anchor fired at
    for i in range(n):
        v = poc.get(int(times[i]))
        if v is None or not np.isfinite(v):
            continue
        if not armed:
            armed = bool(
                # A move, and one big enough. Both halves matter: the inequality
                # alone would make a 0-tick threshold mean "no debounce at all"
                # rather than "any migration".
                (v != fired_at and abs(v - fired_at) >= rearm_ticks * tick)
                if by_move
                # Price away from the level *now* — measured against the current
                # POC, not the one anchored at, so a migration toward price can
                # re-arm the anchor as surely as price walking off can.
                else abs(closes[i] - v) >= rearm_ticks * tick
            )
        if armed and lows[i] <= v <= highs[i]:
            ev[i] = True
            fired_at = v
            # On pocMove a fired anchor always disarms: the way back is a
            # migration, and no threshold makes touching the same level again
            # mean something. On distance it also disarms at any real threshold —
            # only a 0-tick one (which this module does not use) would leave it
            # armed, and that is the no-debounce case the chart offers.
            armed = not by_move and rearm_ticks == 0
    ev[0] = True
    return ev


def anchored_mid(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 volumes: np.ndarray, events: np.ndarray,
                 bar_vwap: np.ndarray | None = None) -> np.ndarray:
    """The anchored VWAP mid, one value per bar, NaN until volume has traded.

    Each bar's price weighted by its volume, sums reset at every anchor. A bar
    with no volume contributes nothing and does not move the average rather than
    zeroing it. No backfill: that belongs to the swing anchor, which starts its
    line at a pivot it only learned about later.

    ``bar_vwap`` is each bar's own tick VWAP (``vwap.bar_moments``). Pass it and
    this is the tape's own average, which is what the chart draws and therefore
    what a level family must be scored against — the two differ by a tick or
    three, and a family measured against a line nobody is looking at is worse
    than no family. Without it the mid falls back to ``(h+l+c)/3``, the
    bar-domain approximation, for callers that have no tape (the parity fixture
    in tests, and any bar-store payload).
    """
    n = len(closes)
    mid = np.full(n, np.nan)
    tp = (highs + lows + closes) / 3.0
    if bar_vwap is not None:
        # NaN only where the bar caught no volume, and there the weight is zero —
        # but NaN×0 is NaN, not 0, so it has to be substituted rather than relied
        # on to drop out.
        bv = np.asarray(bar_vwap, dtype="float64")
        tp = np.where(np.isfinite(bv), bv, tp)
    vol = np.where(volumes > 0, volumes, 0.0)
    s_pv = s_v = 0.0
    for i in range(n):
        if events[i]:
            s_pv = s_v = 0.0
        s_pv += tp[i] * vol[i]
        s_v += vol[i]
        if s_v > 0:
            mid[i] = s_pv / s_v
    return mid


def poc_mid_rows(times: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                 closes: np.ndarray, volumes: np.ndarray,
                 poc_rows: list[dict], tick: float, mode: str,
                 rearm_ticks: int = REARM_TICKS,
                 bar_vwap: np.ndarray | None = None) -> list[dict]:
    """``{time, value}`` rows for the POC-anchored mid — the chart series shape.

    ``poc_rows`` is a developing-profile payload (``session_chart._profile_rows``:
    ``{time, poc, vah, val}``), of which only ``poc`` is read.

    Empty when that payload is empty. The frontend degrades a POC anchor with no
    POC series to a plain session anchor and says so in the legend, which is
    right for a drawing and wrong for a level: the degraded line is the session
    VWAP under another name, and handing it to the tagger as "the POC anchor"
    would score fills against a level that never existed on that day.
    """
    if len(times) == 0 or not poc_rows:
        return []
    poc = {int(r["time"]): float(r["poc"]) for r in poc_rows
           if np.isfinite(r.get("poc", np.nan))}
    if not poc:
        return []
    ev = poc_anchor_events(times, highs, lows, closes, poc, tick, mode, rearm_ticks)
    mid = anchored_mid(highs, lows, closes, volumes, ev, bar_vwap)
    return [{"time": int(t), "value": round(float(v), 2)}
            for t, v in zip(times, mid) if np.isfinite(v)]
