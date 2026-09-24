"""The volume-shelf port, and the check that pins it.

``frontend/src/lib/volumeShelf.ts`` is the original — it is what /charts, the
journal, the replay and live all draw — and ``journal.sim.vol_shelf`` is a copy
that exists for one reason: the level tagger runs in Python and can only measure
a fill against a level that arrives on the ``SessionFrame``. Two ports of one
arithmetic drift silently, because both sides go on returning plausible-looking
bands, so the load-bearing test here is parity against a fixture the TypeScript
generates (``tools/vol-shelf/run.sh``).

The fixture carries its own *input* — the bars and the footprint — rather than
each side inventing a session, because two implementations agreeing about
different data is not agreement. Its session is built to trip the four things
that actually broke while this was written, and each gets its own test below so
a failure says which one:

  camp          a price price *sat* at, carrying far more raw volume than
                anywhere else at the same size per visit. It is the session POC
                by a mile and must NOT be a shelf. This is the whole claim of the
                module; if it ever fires, the normalisation has stopped working
                and every other green test is measuring nothing.
  burst         a band with real size over few visits. Must be a shelf.
  edge          the same, at the very top of the session's range. The smoothing
                window runs off the end there, and an unpadded implementation
                cannot see it at all.
  blip          a band that qualifies for two windows and dies. Must be dropped
                by the duration gate — "building up over a period" is the ask.

Run directly:  ``.venv/bin/python -m pytest tests/test_vol_shelf.py -q``
Regenerate:    ``tools/vol-shelf/run.sh``
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.sim import vol_shelf as vs  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "vol_shelf_parity.json"

#: Both sides are float; the fixture is rounded to six places. A shelf edge is a
#: price, so this is far tighter than a tick and still immune to last-bit noise.
TOL = 1e-6


@pytest.fixture(scope="module")
def fx() -> dict:
    if not FIXTURE.exists():
        pytest.skip(f"{FIXTURE} missing — run tools/vol-shelf/run.sh")
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def params(fx) -> vs.ShelfParams:
    p = fx["params"]
    return vs.ShelfParams(
        window_min=p["windowMin"], z_min=p["zMin"], min_ticks=p["minTicks"],
        min_hold_min=p["minHoldMin"], smooth=p["smooth"],
        step_sec=p["stepSec"],
    )


def _walk(fx, params):
    """Step the Python port over the fixture's session exactly as a host does,
    yielding ``(i, shelves)`` per bar, and return the tracker it fed."""
    bars, fp, tick = fx["bars"], fx["footprint"], fx["tickSize"]
    tracker = vs.ShelfTracker(min_hold_sec=params.min_hold_min * 60)
    per_bar = []
    for i in vs.eval_bars(bars, params.step_sec):
        j = vs.window_start(bars, i, params.window_min)
        entries = [e for k in range(j, i + 1) for e in fp[k]]
        prof = vs.tick_profile(entries, tick)
        win = bars[j:i + 1]
        shelves, _ = vs.detect_shelves(prof, win, True, tick, params)
        tracker.push(int(bars[i]["time"]), shelves, win)
        per_bar.append((j, shelves))
    return per_bar, tracker


# -- parity ------------------------------------------------------------------


def test_window_start_matches(fx, params):
    """The window is walked by timestamp, not bar count. The fixture's bars are
    deliberately unevenly spaced, so a bar-count implementation diverges here
    and nowhere else."""
    per_bar, _ = _walk(fx, params)
    for i, (j, _) in enumerate(per_bar):
        assert j == fx["perBar"][i]["from"], f"window start differs at bar {i}"


def test_per_window_shelves_match(fx, params):
    """Every window's shelves, edge for edge and z for z."""
    per_bar, _ = _walk(fx, params)
    for i, (_, shelves) in enumerate(per_bar):
        want = fx["perBar"][i]["shelves"]
        assert len(shelves) == len(want), (
            f"bar {i}: python found {len(shelves)} shelves, TS found {len(want)}"
        )
        for got, exp in zip(shelves, want):
            assert got.lo == pytest.approx(exp["lo"], abs=TOL), f"bar {i} lo"
            assert got.hi == pytest.approx(exp["hi"], abs=TOL), f"bar {i} hi"
            assert got.price == pytest.approx(exp["price"], abs=TOL), f"bar {i} price"
            assert got.z == pytest.approx(exp["z"], abs=TOL), f"bar {i} z"


def test_boxes_match(fx, params):
    """The tracked shelves — merge, grace and duration gate all included."""
    _, tracker = _walk(fx, params)
    got, want = tracker.boxes(), fx["boxes"]
    assert len(got) == len(want), f"python {len(got)} boxes, TS {len(want)}"
    for g, w in zip(got, want):
        assert g.lo == pytest.approx(w["lo"], abs=TOL)
        assert g.hi == pytest.approx(w["hi"], abs=TOL)
        assert g.from_time == w["from"]
        assert g.to_time == w["to"]
        assert g.detected_to == w["detectedTo"]
        assert g.z == pytest.approx(w["z"], abs=TOL)
        assert g.live == w["live"]
        assert g.armed == w["armed"]
        assert g.cleared == w["cleared"]


def test_fixture_pins_both_a_cleared_and_a_standing_shelf(fx):
    """A guard on the fixture, not on the port.

    ``test_boxes_match`` compares whatever the fixture happens to contain, so it
    would go on passing if a change to the synthetic session quietly stopped
    exercising the extension at all — both sides would agree about a session in
    which no shelf is ever cleared and none ever outlives its detection. That is
    the failure mode this catches, and it is the one that leaves a whole branch
    unpinned while every test is green.
    """
    boxes = fx["boxes"]
    cleared = [b for b in boxes if b["cleared"]]
    standing = [
        b for b in boxes
        if not b["live"] and not b["cleared"] and b["to"] > b["detectedTo"]
    ]
    assert cleared, "fixture no longer clears any shelf"
    assert standing, "fixture no longer leaves a shelf standing past its detection"
    # The standing one reaches the live edge — that IS the feature.
    assert standing[-1]["to"] == fx["bars"][-1]["time"]
    # A cleared box froze: it stopped before the session did.
    assert cleared[0]["to"] < fx["bars"][-1]["time"]
    # And it froze *mid-detection*: price crossed the band while the trailing
    # window still detected it, so its edge must not outrun the crossing. This
    # pins the branch where departure and return are read off price alone —
    # gating the sweep on detection lapsing would leave a window-long stretch in
    # which this fixture's clear never happens.
    assert any(b["to"] <= b["detectedTo"] for b in cleared), (
        "fixture no longer clears any shelf while it is still detected"
    )


def test_series_agrees_with_the_tracked_boxes(fx, params):
    """``shelf_series`` is Python-only — nothing in the TypeScript builds a
    per-bar level series — so it is pinned against this module's own boxes
    rather than against the fixture.

    Every finite row must fall inside some box's span, and every row outside all
    of them must be ``nan`` — the series names a shelf exactly when one is being
    drawn.

    The bands themselves are deliberately not compared. A box's ``lo``/``hi``
    track the latest reading (a shelf that thickens or drifts is still that
    shelf), so a box ends up carrying its *final* edges while the series recorded
    the edges it had at each bar; over half an hour those need not even overlap.
    Asserting they match would be asserting that shelves never drift, which is
    not a property this has or wants.
    """
    series = vs.shelf_series(fx["bars"], fx["footprint"], fx["tickSize"], params)
    _, tracker = _walk(fx, params)
    boxes = tracker.boxes()

    finite = 0
    for row in series:
        inside = any(b.from_time <= row["time"] <= b.to_time for b in boxes)
        if math.isnan(row["hi"]):
            continue
        finite += 1
        assert inside, f"series named a shelf at {row['time']} with no box alive"
    assert finite, "fixture drifted: the series never named a shelf"


# -- the four behaviours the fixture was built to trip ------------------------


def _covers(box, lo: float, hi: float) -> bool:
    return box.lo <= hi and box.hi >= lo


def test_camp_is_not_a_shelf(fx, params):
    """The claim the whole module rests on.

    The camp is where price sat longest and it is the session's POC by raw
    volume, at the *same* size per visit as everywhere else. A profile picks it;
    this must not. If this test ever goes red the denominator has stopped
    working, and every other green test here is measuring nothing.
    """
    _, tracker = _walk(fx, params)
    camp_lo, camp_hi = 20002.50, 20003.50
    hits = [b for b in tracker.boxes() if _covers(b, camp_lo, camp_hi)]
    assert not hits, f"the camp became a shelf: {hits}"

    # ...and it really is the POC, or the test above proves nothing.
    entries = [e for bar in fx["footprint"] for e in bar]
    prof = vs.tick_profile(entries, fx["tickSize"])
    poc_i = int(np.argmax(prof.volume))
    poc = (prof.low[poc_i] + prof.high[poc_i]) / 2
    assert camp_lo <= poc <= camp_hi, f"fixture drifted: POC {poc} is not the camp"


def test_burst_is_a_shelf(fx, params):
    """Real size over few visits, mid-range."""
    _, tracker = _walk(fx, params)
    hits = [b for b in tracker.boxes() if _covers(b, 20011.00, 20012.25)]
    assert hits, "the burst was not detected"
    assert max(b.z for b in hits) >= params.z_min


def test_edge_shelf_is_found(fx, params):
    """The same band at the very top of the range, where the smoothing window
    runs off the end of the rows.

    Zero-padding before smoothing is what makes this visible: without it the
    partial centred mean divides by fewer terms at the boundary, the curve rises
    into the edge instead of decaying, and the band never reads as remarkable.
    """
    _, tracker = _walk(fx, params)
    hits = [b for b in tracker.boxes() if _covers(b, 20017.50, 20018.75)]
    assert hits, "the shelf at the top of the range was missed"


def test_duration_gate_drops_a_flickering_band():
    """A band that stops qualifying before it has held is not a shelf.

    Driven straight at ``ShelfTracker`` rather than through a synthetic session,
    on purpose. The window is *trailing*, so a one-off burst keeps being detected
    for as long as it stays inside the window — a "brief spike" in a generated
    session does not express what this gate filters, which is a band whose score
    flickers around the threshold as the window slides.
    """
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)

    # Seen for 2 minutes, then gone past the grace: under the hold, dropped.
    for t in range(0, 121, 60):
        tr.push(t, [band])
    for t in (180, 240, 300):
        tr.push(t, [])
    assert tr.boxes() == []
    assert tr.nearest(100.5) is None


def test_duration_gate_keeps_a_band_that_holds():
    """The same band, held past the gate, is reported — and as ONE box."""
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    for t in range(0, 901, 60):
        tr.push(t, [band])
    boxes = tr.boxes()
    assert len(boxes) == 1
    assert boxes[0].from_time == 0 and boxes[0].to_time == 900
    assert boxes[0].live is True
    assert tr.nearest(100.5) is boxes[0]


def test_grace_keeps_one_shelf_whole():
    """A band that misses a window and returns is one shelf, not two — otherwise
    a band dipping under the threshold for a bar shatters into a stack of boxes.
    """
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    for t in range(0, 481, 60):
        tr.push(t, [band])
    tr.push(540, [])                       # one miss, inside grace
    for t in range(600, 901, 60):
        tr.push(t, [band])
    boxes = tr.boxes()
    assert len(boxes) == 1, f"grace failed to bridge the gap: {boxes}"
    assert boxes[0].from_time == 0 and boxes[0].to_time == 900


def test_box_keeps_its_best_z_not_its_last():
    """A shelf is worth what it was at its strongest, not what it had decayed to
    on the bar it happened to die."""
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=0)
    for t, z in ((0, 2.1), (60, 4.4), (120, 2.2)):
        tr.push(t, [vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=z)])
    assert tr.boxes()[0].z == pytest.approx(4.4)


# -- standing past detection --------------------------------------------------
#
# Driven straight at the tracker for the same reason the duration gate is: with a
# trailing window a band goes on being detected for the whole window after price
# leaves, so expressing "detection has lapsed AND price has since returned" in a
# generated session costs a window's worth of bars per case. The fixture carries
# one of each end to end; these say precisely which rule produced them.


def _bar(t: int, lo: float, hi: float, close: float) -> dict:
    return {"time": t, "low": lo, "high": hi, "close": close}


def _away(t: int) -> dict:
    """A bar nowhere near the 100–101 band the tests below use."""
    return _bar(t, 108.0, 109.0, 108.5)


def _held_band(tr, band, upto: int = 900) -> None:
    for t in range(0, upto + 1, 60):
        tr.push(t, [band], [_bar(t, 100.0, 101.0, 100.5)])


def test_untested_shelf_runs_to_the_live_edge():
    """Detection stops, price leaves, and the box keeps reaching the newest bar.

    This is the whole feature: a box that touches the right-hand edge is a zone
    price has not been back to since it stopped being a shelf.
    """
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    _held_band(tr, band)
    for t in range(960, 1501, 60):
        tr.push(t, [], [_away(t)])

    box = tr.boxes()[0]
    assert box.live is False
    assert box.detected_to == 900, "detection end moved"
    assert box.to_time == 1500, "box did not follow the live edge"
    assert box.armed is True and box.cleared is False
    assert tr.nearest(100.5) is box, "an untested shelf must still reach the tagger"


def test_price_returning_freezes_the_box():
    """Back into the band, and the box stops there rather than at the live edge."""
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    _held_band(tr, band)
    for t in (960, 1020, 1080):
        tr.push(t, [], [_away(t)])
    tr.push(1140, [], [_bar(1140, 100.4, 102.0, 101.5)])   # wicks back in
    tr.push(1200, [], [_away(1200)])

    box = tr.boxes()[0]
    assert box.cleared is True
    assert box.to_time == 1140, "box did not freeze on the bar price returned"
    assert tr.nearest(100.5) is None, "a cleared shelf must leave the tagger's series"


def test_a_shelf_price_never_left_does_not_clear_itself():
    """Arming first, and it is what makes the feature usable rather than a no-op.

    Price sits *inside* a band while the band forms. Testing for a touch the
    moment detection stops would clear almost every shelf on the very next bar,
    against price that had not gone anywhere.
    """
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    _held_band(tr, band)
    for t in range(960, 1501, 60):                     # never leaves the band
        tr.push(t, [], [_bar(t, 100.0, 101.0, 100.5)])

    box = tr.boxes()[0]
    assert box.armed is False and box.cleared is False
    assert box.to_time == 1500


def test_arming_needs_a_bar_wholly_clear_of_the_band():
    """The bar price leaves on usually still spans the band — it has been *in*
    it, and a bar that has been in the band cannot say price left. Arming on its
    close was the first rule here, and with still-detected boxes swept it
    cleared shelves against the very trading that was building them: near a
    forming band, closes hop off it constantly while the bars go on trading in
    it, and the wick after any close that happened to land outside read as
    "price came back". Only a bar that never touched the band arms; the overlap
    after THAT is a return — which also makes it impossible for one bar to arm
    and clear, by construction rather than by rule."""
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    _held_band(tr, band)
    # Detection lapses while price is still sitting in the band: closed, unarmed.
    for t in (960, 1020, 1080):
        tr.push(t, [], [_bar(t, 100.0, 101.0, 100.5)])
    assert tr.boxes()[0].live is False and tr.boxes()[0].armed is False

    # A wide bar out of the top: spans the band and closes well clear of it. It
    # has been in the band, so it arms nothing — and can clear nothing.
    tr.push(1140, [], [_bar(1140, 100.2, 106.0, 105.5)])
    box = tr.boxes()[0]
    assert box.armed is False and box.cleared is False, "an overlapping bar armed"

    # A bar wholly above the band: price has left.
    tr.push(1200, [], [_bar(1200, 103.0, 106.0, 104.0)])
    box = tr.boxes()[0]
    assert box.armed is True and box.cleared is False

    # And the trade back into the band after that is the return.
    tr.push(1260, [], [_bar(1260, 100.5, 103.0, 102.0)])
    assert tr.boxes()[0].cleared is True
    assert tr.boxes()[0].to_time == 1260


def test_hold_gate_reads_detection_not_the_extension():
    """A band that qualified for two minutes must not satisfy a ten-minute hold
    by standing untested for eight more.

    The gate is about how long size took to build. Measuring it on the drawn
    right edge — which now outlives detection — would silently turn every brief
    band into a shelf, and every other test here would stay green while it did.
    """
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    for t in (0, 60, 120):                              # 2 minutes of detection
        tr.push(t, [band], [_bar(t, 100.0, 101.0, 100.5)])
    for t in range(180, 1801, 60):                      # 27 more standing away
        tr.push(t, [], [_away(t)])
    assert tr.boxes() == [], "the extension satisfied the duration gate"
    assert tr.nearest(100.5) is None


def test_a_touch_between_readings_is_not_missed():
    """Readings are taken on a clock; bars are not. A wick that entered the band
    and left again between two readings is exactly the revisit this is for, and
    testing only the bar a reading landed on would never see it."""
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    _held_band(tr, band)
    for t in (960, 1020, 1080):
        tr.push(t, [], [_away(t)])
    # One reading at 1140, but five bars happened since 1080 and the middle one
    # dipped into the band. The reading's own bar (1140) is clear of it.
    window = [
        _away(1092), _away(1104),
        _bar(1116, 100.6, 104.0, 103.0),
        _away(1128), _away(1140),
    ]
    tr.push(1140, [], window)
    box = tr.boxes()[0]
    assert box.cleared is True, "the between-readings touch was missed"
    assert box.to_time == 1116


def test_the_tagger_gets_the_nearest_shelf_not_the_strongest():
    """Two untested shelves, and the far one scores higher.

    This combination is what made "untested" and "strongest" incompatible. A
    *live* shelf is near price by construction — it sits in a window of recent
    bars — so picking the strongest was harmless while only live ones counted.
    An untested shelf can be anywhere, and on a real session the day's biggest
    stayed named for 72% of readings from over a hundred points away: a fill
    sitting exactly on the near band was scored against the far one and came
    back "not at a level".
    """
    near = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=2.2)
    far = vs.Shelf(lo=140.0, hi=141.0, price=140.5, z=9.9)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    for t in range(0, 901, 60):
        tr.push(t, [near, far], [_bar(t, 100.0, 141.0, 120.0)])
    for t in range(960, 1201, 60):
        tr.push(t, [], [_bar(t, 99.0, 102.0, 100.5)])

    got = tr.nearest(100.5)
    assert got is not None and got.lo == 100.0, "handed the far, stronger shelf"
    assert tr.nearest(140.5).lo == 140.0, "nearest must follow price, not z"


def test_a_revisit_while_still_detected_clears_and_retires():
    """Departure and return are facts about price, not about the trailing
    window. With a long window a band stays detected for the whole window after
    price leaves, and a revisit landing in that stretch is the very test the box
    exists to record — gating the sweep on detection lapsing missed it, the box
    overran, and the tagger went on scoring fills against a band price had
    already dealt with."""
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    _held_band(tr, band)
    # Price leaves while the band goes on being detected (the window still holds
    # the size that made it), then wicks back in — still detected throughout.
    tr.push(960, [band], [_away(960)])
    tr.push(1020, [band], [_away(1020)])
    tr.push(1080, [band], [_bar(1080, 100.4, 102.0, 101.5)])

    boxes = tr.boxes()
    assert len(boxes) == 1
    box = boxes[0]
    assert box.cleared is True and box.live is False
    assert box.to_time == 1080, "box did not freeze on the bar price returned"
    assert tr.nearest(100.5) is None, "a cleared shelf must leave the tagger"

    # The size is still in the window, so the next readings re-detect the band —
    # as a NEW box owing the hold gate from scratch, not this one growing past
    # the bar that tested it.
    for t in range(1140, 1741, 60):
        tr.push(t, [band], [_away(t)])
    boxes = tr.boxes()
    assert len(boxes) == 2, "the re-detection grew the tested box instead"
    assert boxes[0].cleared is True and boxes[0].to_time == 1080
    assert boxes[1].from_time == 1140


def test_bars_already_swept_cannot_clear_a_later_shelf():
    """Consecutive readings hand over overlapping window slices. Re-walking the
    overlap would let a bar from *before* a shelf existed clear it — the box
    would be born already tested."""
    tr = vs.ShelfTracker(grace_bars=2, min_hold_sec=600)
    early = [_bar(t, 100.0, 101.0, 100.5) for t in (0, 60, 120)]
    for t in (0, 60, 120):
        tr.push(t, [], early)
    # A shelf now forms at that same band, then stops. The bars that sat in it
    # are still inside every window slice handed over.
    band = vs.Shelf(lo=100.0, hi=101.0, price=100.5, z=3.0)
    for t in range(180, 1081, 60):
        tr.push(t, [band], early + [_bar(t, 100.0, 101.0, 100.5)])
    for t in range(1140, 1501, 60):
        tr.push(t, [], early + [_away(t)])

    box = tr.boxes()[0]
    assert box.cleared is False, "a bar older than the shelf cleared it"
    assert box.to_time == 1500


# -- guards ------------------------------------------------------------------


def test_estimated_profile_draws_nothing(fx, params):
    """``exact=False`` must return nothing.

    Under the bar-spreading estimate a bar's single volume number is spread
    across the rows its high-low covers, so ``volume / TPO`` is near-constant by
    construction and every band would be an artefact of the estimator.
    """
    bars, fp, tick = fx["bars"], fx["footprint"], fx["tickSize"]
    entries = [e for bar in fp for e in bar]
    prof = vs.tick_profile(entries, tick)
    shelves, reading = vs.detect_shelves(prof, bars, False, tick, params)
    assert shelves == []
    assert reading is None


def test_thin_window_says_nothing_rather_than_zero():
    """Too few occupied rows is *no reading*, not "no shelf".

    A z-score over n points cannot exceed (n-1)/sqrt(n), so below about six rows
    a z_min of 2 is unreachable however concentrated the size was. Reporting an
    empty list without this guard would spell "cannot tell" the same way as
    "nothing there".
    """
    tick = 0.25
    # Five occupied rows, one of them carrying wildly more size per visit.
    entries = [[100.0 + k * tick, 10.0] for k in range(5)]
    entries.append([100.0 + 2 * tick, 100000.0])
    bars = [{"time": 0, "high": 100.0 + 4 * tick, "low": 100.0}]
    prof = vs.tick_profile(entries, tick)
    assert len(prof) < vs.MIN_OCCUPIED_ROWS
    shelves, _ = vs.detect_shelves(prof, bars, True, tick, vs.ShelfParams())
    assert shelves == []


def test_jsround_breaks_ties_upward():
    """The parity trap that bites hardest: JS ``Math.round`` is floor(x + 0.5),
    Python's ``round`` is banker's. On a row boundary the two put a price in
    different rows, silently."""
    assert vs._jsround(0.5) == 1 and round(0.5) == 0
    assert vs._jsround(1.5) == 2 and round(1.5) == 2
    assert vs._jsround(2.5) == 3 and round(2.5) == 2
    assert vs._jsround(-0.5) == 0


def test_smoothed_matches_pandas():
    """``_smoothed`` is pandas' ``rolling(k, center=True, min_periods=1).mean()``
    — the partial window at the edges included, since that is the behaviour the
    padding exists to work around."""
    pd = pytest.importorskip("pandas")
    v = np.array([1.0, 5.0, 2.0, 8.0, 3.0, 9.0, 4.0])
    for k in (3, 5):
        want = pd.Series(v).rolling(k, center=True, min_periods=1).mean().to_numpy()
        assert np.allclose(vs._smoothed(v, k), want)


def test_time_at_price_is_read_off_bars_not_prints():
    """A row's time-at-price is how long bars covered it, so a bar with one huge
    print and a bar with a thousand small ones count the same. That is what
    makes the denominator a time measure rather than a second volume measure."""
    tick = 0.25
    prof = vs.tick_profile([[100.0, 1.0], [100.25, 1.0], [100.5, 1.0]], tick)
    # A single bar has no gap to read its duration from; it weighs 1.
    one = vs.time_at_price(prof, [{"time": 0, "high": 100.5, "low": 100.0}])
    assert list(one) == [1.0, 1.0, 1.0]
    # Two bars: each weighs its duration (the last reuses the previous gap), and
    # the row both covered carries both.
    two = vs.time_at_price(prof, [
        {"time": 0, "high": 100.25, "low": 100.0},
        {"time": 30, "high": 100.5, "low": 100.25},
    ])
    assert list(two) == [30.0, 60.0, 30.0]


def test_time_at_price_is_seconds_not_a_bar_count():
    """The reason the denominator is duration-weighted at all: on a tick- or
    volume-bucketed chart bars are activity-clocked, so *counting* them puts
    activity in the denominator of the very ratio the numerator measures. The
    same volume over the same number of bars must read hotter where the bars
    took less wall clock."""
    tick = 0.25
    prof = vs.tick_profile([[100.0, 10.0], [100.5, 10.0]], tick)
    fast = vs.time_at_price(prof, [
        {"time": 0, "high": 100.0, "low": 100.0},
        {"time": 5, "high": 100.5, "low": 100.5},
    ])
    slow = vs.time_at_price(prof, [
        {"time": 0, "high": 100.0, "low": 100.0},
        {"time": 90, "high": 100.5, "low": 100.5},
    ])
    assert fast[0] < slow[0], "a burst and a camp counted the same"


def test_absent_shelf_is_nan_at_every_reading(fx, params):
    """The series carries a row at EVERY reading, with ``nan`` where no shelf
    qualified — and this is load-bearing, not tidiness.

    ``level_tag.DayLevels.at`` answers with the last row at or before an instant
    and has no staleness bound, which is correct for the levels it was built for:
    a VWAP, an EMA and a developing value area exist on every bar, so "the last
    one" is always "the current one". A shelf does not. An earlier version of
    this emitted only the bars that had a shelf, and the tagger duly scored a
    fill against a band that had died hours before, with nothing raising a word,
    because a stale float and a live float are the same float. With a row at
    every reading the worst staleness is one ``step_sec``.
    """
    series = vs.shelf_series(fx["bars"], fx["footprint"], fx["tickSize"], params)
    bars = fx["bars"]
    want = [int(bars[i]["time"]) for i in vs.eval_bars(bars, params.step_sec)]
    assert [r["time"] for r in series] == want, "a reading went unrecorded"

    # Staleness a fill can be scored against is bounded by the cadence — but only
    # to the resolution the bars allow, since a reading can only be taken where a
    # bar exists. So the guarantee is not "never more than step_sec apart" (a
    # stretch of 90-second bars cannot honour that); it is that no bar was passed
    # over once step_sec had elapsed.
    times = [int(b["time"]) for b in bars]
    for a, b in zip(want, want[1:]):
        skipped = [t for t in times if a < t < b and t - a >= params.step_sec]
        assert not skipped, f"reading at {a} should have been taken at {skipped[0]}"

    gaps = [r for r in series if math.isnan(r["hi"])]
    live = [r for r in series if not math.isnan(r["hi"])]
    assert gaps and live, "fixture drifted: needs both shelf and no-shelf bars"
    # nan on both edges together — half a zone is not a reading.
    assert all(math.isnan(r["lo"]) for r in gaps)


def test_stale_shelf_does_not_reach_the_tagger():
    """The end of that same bug, asserted where it actually bit.

    A synthetic frame with one shelf early and nothing after: at a late instant
    the family must be absent, not resolved to the dead band. ``family_distance``
    skips non-finite members, so the density above is what makes this hold.
    """
    from journal import level_tag

    nan = float("nan")
    times = np.array([100, 200, 300], dtype="int64")
    axes = {
        "vol_shelf_hi": (times, np.array([50.0, nan, nan])),
        "vol_shelf_lo": (times, np.array([49.0, nan, nan])),
    }
    lv = object.__new__(level_tag.DayLevels)
    lv.axes = axes

    def read(bar_time: int) -> dict[str, float]:
        """What ``DayLevels.at`` does, without needing a whole SessionFrame."""
        return {
            name: float(vals[int(np.searchsorted(ts, bar_time, "left")) - 1])
            for name, (ts, vals) in axes.items()
        }

    # While it was live the family resolves and the distance is real...
    member, dist = lv.family_distance(49.5, read(200))["volume_shelf"]
    assert member in ("vol_shelf_hi", "vol_shelf_lo")
    assert math.isfinite(dist)

    # ...and once it has gone the family is absent, not stale.
    late = read(300)
    assert all(math.isnan(v) for v in late.values())
    assert "volume_shelf" not in lv.family_distance(49.5, late)
