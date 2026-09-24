"""The bracket presets, and the ruler they are built from.

The fixture is generated from the real `frontend/src/lib/orderPresets.ts` and
`frontend/src/lib/volRuler.ts` by `tools/order-presets/run.sh`. This file
re-derives every number in it from first principles — including rebuilding the
synthetic tape print for print and measuring it its own way — and asserts the
two agree.

Why bother. A preset is five distances applied in one click, and every way it
can be wrong is silent: a trail set to the stop instead of the stop minus five
still fills, still trails, and quietly trades a shape nobody chose. The ruler
under it is worse, because it is incremental — a bar counted twice, a window
edge off by one, a seek that fails to forget the future, and the stop on every
ticket after that moment is a number no rule produced.

Re-run the generator and commit the fixture when the rule changes on purpose.
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures/order_presets.json"

#: volRuler.ts — the bucketings a preset's stop can be read at, the window every
#: one of their medians develops in, and the fewest closed bars that make one.
#: Restated here rather than imported so the fixture is graded against a rule
#: written down twice; `test_the_fixture_was_built_at_the_rule_this_file_grades`
#: is the tripwire when they part.
#:
#: `(prints, seconds)` — exactly one of the pair is set. A print bar is a volume
#: clock counted from the session's first print; a clock bar is a slice of the
#: wall clock. That is the whole difference between them.
BUCKETS: dict[str, tuple[int | None, int | None]] = {
    "t500": (500, None),
    "s30": (None, 30),
    "s15": (None, 15),
}
PRESET_WIN_START = 9 * 3600 + 30 * 60
WIN_END = 16 * 3600
MIN_BARS = 3


@pytest.fixture(scope="module")
def fx():
    assert FIXTURE.exists(), "run tools/order-presets/run.sh"
    return json.loads(FIXTURE.read_text())


def js_round(x: float) -> int:
    """`Math.round`, which is not `round`.

    JavaScript rounds a half away from zero; Python rounds it to even. They
    disagree on exactly the cases these rules produce — a 35t stop is 52.5 at
    1.5R, and 53 is the answer the ticket gets sent. Re-deriving with the wrong
    one would either fail honest code or, worse, pass a rule that had drifted.
    """
    return math.floor(x + 0.5)


# --- the brackets ------------------------------------------------------------


def test_a_is_the_trend_bracket(fx):
    """A: no target at all, and a trail 5 ticks inside the stop starting at the
    entry itself. The floor matters — a 4t reading must not produce a trail of
    -1.

    Note the target stays at **zero** through the room: a leg that is off must
    not be switched on by an offset, which is the one way "+5 on everything"
    could turn a preset into a different preset."""
    room = fx["legRoom"]
    for b in fx["brackets"]:
        if b["id"] != "A":
            continue
        r = b["asked"]
        assert b["targetTicks"] == 0
        assert b["stopTicks"] == r + room
        assert b["trailTicks"] == max(1, r - 5) + room
        assert b["trailStepTicks"] == 0
        assert b["trailBeTicks"] == 0
        assert b["trailBeOnly"] is False


def test_b_is_one_and_a_half_r_with_a_breakeven_jump(fx):
    """B: 1.5R, and the first rung only — at one stop's distance, 3 ticks past
    the fill."""
    room = fx["legRoom"]
    for b in fx["brackets"]:
        if b["id"] != "B":
            continue
        r = b["asked"]
        assert b["stopTicks"] == r + room
        assert b["targetTicks"] == js_round(r * 1.5) + room
        assert b["trailTicks"] == r + room
        assert b["trailStepTicks"] == 0
        assert b["trailBeTicks"] == 3
        assert b["trailBeOnly"] is True


def test_c_is_one_and_a_third_r_with_no_breakeven(fx):
    """C: 1.33R, and no ladder at all.

    It is B's trade with the rung removed and the target pulled in, so the
    assertion that earns its keep here is the *absence*: `trailBeOnly is False`
    and every rung knob at zero. A C that kept B's breakeven would look right in
    the panel — same stop, a nearer target — and quietly convert the losers this
    shape exists to take in full into scratches."""
    room = fx["legRoom"]
    for b in fx["brackets"]:
        if b["id"] != "C":
            continue
        r = b["asked"]
        assert b["stopTicks"] == r + room
        assert b["targetTicks"] == js_round(r * (4 / 3)) + room
        assert b["trailTicks"] == 0
        assert b["trailStepTicks"] == 0
        assert b["trailBeTicks"] == 0
        assert b["trailBeOnly"] is False


def test_d_is_one_r_and_nothing_behind_it(fx):
    """D: 1R, and no ladder at all — every trail knob off, at every stop.

    All four are asserted rather than just the distance: `trailTicks == 0` is
    what switches the ladder off, but a leftover rung or a `trailBeOnly` still
    standing from the shape this used to be is exactly the kind of half-removal
    that reads as harmless in a diff and re-arms the moment someone turns the
    distance back on."""
    room = fx["legRoom"]
    for b in fx["brackets"]:
        if b["id"] != "D":
            continue
        r = b["asked"]
        assert b["stopTicks"] == r + room
        assert b["targetTicks"] == r + room
        assert b["trailTicks"] == 0
        assert b["trailStepTicks"] == 0
        assert b["trailBeTicks"] == 0
        assert b["trailBeOnly"] is False


def test_the_targets_step_down_a_and_then_b_c_d(fx):
    """The list is one axis, not four labels: A has no target at all, and B, C
    and D name a strictly falling ratio.

    Ordering is what makes the letters mean something at a glance, and it is the
    thing a preset inserted in the middle breaks — which is exactly what
    happened when 1.33R went in above the 1R and pushed it to D. Asserted at
    every stop rather than at one, because the room lands on both legs and a
    rounding that crossed would cross at a particular reading.

    Strict only from 4 ticks up: at a 1-tick reading 1.33R and 1R round to the
    same target and C and D really are the same bracket. That is the rounding
    being honest, not the order being broken, and no ruler that has closed three
    bars reads 1."""
    by_stop: dict[int, dict[str, int]] = {}
    for b in fx["brackets"]:
        by_stop.setdefault(b["asked"], {})[b["id"]] = b["targetTicks"]
    assert by_stop
    for r, t in by_stop.items():
        assert t["A"] == 0, r
        assert t["B"] >= t["C"] >= t["D"], (r, t)
        if r >= 4:
            assert t["B"] > t["C"] > t["D"], (r, t)


def test_every_preset_places_the_reading_plus_the_room(fx):
    """The one thing every preset shares: the stop is the reading and the room, and
    nothing else has been done to it."""
    room = fx["legRoom"]
    for p in fx["placed"]:
        assert p["stopTicks"] == p["reading"] + room
    for b in fx["brackets"]:
        assert b["stopTicks"] == b["asked"] + room
        assert b["stopTicks"] >= 1
        # No leg may come out negative or fractional — every one of these is
        # sent as a tick distance.
        for k in ("targetTicks", "trailTicks", "trailStepTicks", "trailBeTicks"):
            assert isinstance(b[k], int) and b[k] >= 0


def test_the_room_lands_on_live_legs_only(fx):
    """Every distance that is *on* carries the room; the two rung knobs do not.

    `trailStepTicks` is a grid, not a distance, and `trailBeTicks` is measured
    from the fill — five more ticks of "breakeven" would be five ticks of
    profit wearing the word. Checked against the shape re-derived here rather
    than against the fixture's own arithmetic, so a room applied in the wrong
    place fails rather than agrees with itself."""
    room = fx["legRoom"]
    assert room > 0
    for b in fx["brackets"]:
        assert b["trailStepTicks"] == 0
        assert b["trailBeTicks"] in (0, 3)
        # Every live distance is at least the room away from zero: nothing was
        # offset into existence, and nothing live was left un-offset.
        for k in ("stopTicks", "targetTicks", "trailTicks"):
            assert b[k] == 0 or b[k] > room


def test_every_preset_is_present_at_every_stop(fx):
    ids = {p["id"] for p in fx["presets"]}
    assert ids == {"A", "B", "C", "D"}
    by_stop: dict[int, set[str]] = {}
    for b in fx["brackets"]:
        by_stop.setdefault(b["asked"], set()).add(b["id"])
    assert by_stop and all(v == ids for v in by_stop.values())


# --- reading to stop ---------------------------------------------------------


def test_the_stop_is_the_reading_rounded_and_nothing_else(fx):
    """One reading, rounded to a whole tick, floored at 1 — and no preset at all
    where the ruler said nothing usable.

    The floor and the null are the two halves of the same rule. A reading of 0.2
    is a real measurement of a dead tape and rounds to a 0-tick stop, which is a
    bracket with no stop on it; a reading of 0 or None is the ruler declining to
    answer, and there is nothing behind it to answer instead."""
    for case in fx["stopReadings"]:
        r = case["reading"]
        want = max(1, js_round(r)) if r is not None and r > 0 else None
        assert case["stop"] == want, r


def test_there_is_no_fallback_behind_the_reading(fx):
    """The chain is gone on purpose, and this is the assertion that says so.

    Until 2026-08-25 a null reading fell through to a pre-bell median ÷1.06 and
    then to yesterday's settled one — both fitted for a 500-print bar, where an
    overnight bar spans more clock time and therefore reads *wide*. At a fixed
    30 seconds that inverts, so the correction pointed the wrong way and the
    window yesterday was measured over was no longer the window being asked
    about. Nothing replaced them: before the bell the presets have no stop."""
    assert "nightScale" not in fx
    for case in fx["stopReadings"]:
        assert case["stop"] is None or isinstance(case["stop"], int)
    assert any(c["stop"] is None for c in fx["stopReadings"])


# --- the ruler ---------------------------------------------------------------


def build_tape(spec: dict, open_end: int) -> tuple[list[int], list[float]]:
    """The generator's tape, rebuilt print for print.

    MINSTD (`s = s * 48271 mod 2**31-1`), which is exact in a double and so
    reproduces the TypeScript's walk exactly. If this drifts, everything below
    is comparing two different tapes and would fail loudly rather than pass
    vacuously — which is why the medians are asserted against real numbers.

    The two step scales are the generator's and are load-bearing, not colour: a
    uniform tape medians to the same number at four bars and at eight hundred,
    so a ruler measuring the wrong *span* would agree with a correct one. The
    context days run 3x and the first ten minutes of RTH run 2x, which is what
    gives a context leak and a window that never opened somewhere to show.
    Integer scales on a 0.25 tick stay exact in a double, so this walk and the
    TypeScript's stay identical.
    """
    per_day, n = spec["perDay"], spec["n"]
    t: list[int] = []
    price: list[float] = []
    s = spec["seed"]
    px = float(spec["startPrice"])
    for i in range(n):
        day, k = divmod(i, per_day)
        sec = spec["startSec"] + k // spec["perSecond"]
        t.append((day * 86400 + sec) * 1000)
        s = (s * 48271) % 2147483647
        if day < spec["days"] - 1:
            scale = 3
        elif PRESET_WIN_START <= sec < open_end:
            scale = 2
        else:
            scale = 1
        px += ((s % 5) - 2) * spec["tickSize"] * scale
        price.append(px)
    return t, price


def closed_bars(
    t: list[int], price: list[float], start: int, playhead: int, tick: float, bucket: str
) -> list[tuple[int, float]]:
    """Every *closed* bar of one bucketing from `start` through `playhead`, as
    (first print's second, range in ticks).

    A bar is the prints sharing a key — `(i - start) // prints` for the volume
    clock, `sec // seconds` for a wall clock — and it closes when a print
    belonging to a later one arrives, so the bar the playhead is sitting in is
    deliberately not in here. Re-derived the obvious way, one pass per
    bucketing with no state carried between calls, against a ruler that is
    incremental, shares one pass across all three, and rewinds: an off-by-one at
    a bar edge, a bar counted twice, a forming bar folded in early, or two
    bucketings leaking into each other all show up as a disagreement.
    """
    prints, seconds = BUCKETS[bucket]
    out: list[tuple[int, float]] = []
    key: int | None = None
    start_sec = 0
    hi = lo = 0.0
    for i in range(start, playhead + 1):
        sec = t[i] // 1000
        k = (i - start) // prints if prints is not None else sec // seconds
        if k != key:
            if key is not None:
                out.append((start_sec, (hi - lo) / tick))
            key, start_sec, hi, lo = k, sec, price[i], price[i]
        else:
            hi, lo = max(hi, price[i]), min(lo, price[i])
    return out


def reading_at(
    t: list[int], price: list[float], spec: dict, playhead: int, bucket: str
) -> float | None:
    """One bucketing's reading, re-derived: the median range of its closed bars
    that *opened* inside 09:30-16:00 ET, or nothing below MIN_BARS of them.

    The window is tested on the bar's **first print**, which is the only clock a
    volume bar has and — because every window edge is a multiple of every bar
    size offered — the same answer a clock bar's own start would give.
    """
    bars = closed_bars(t, price, spec["sessionStart"], playhead, spec["tickSize"], bucket)
    rs = [r for sec, r in bars if PRESET_WIN_START <= sec % 86400 < WIN_END]
    return statistics.median(rs) if len(rs) >= MIN_BARS else None


@pytest.fixture(scope="module")
def tape(fx):
    return build_tape(fx["spec"], fx["openEndSec"])


def test_the_fixture_was_built_at_the_rule_this_file_grades(fx):
    """The bucketings and the window are stated in both places. If the generator
    moves one and this file does not, every assertion below would still pass
    while grading the wrong rule — so compare them first."""
    assert {b["id"]: (b["prints"], b["seconds"]) for b in fx["buckets"]} == BUCKETS
    assert fx["winStartSec"] == PRESET_WIN_START
    # Exactly one of the pair, or the ruler has no rule for making a bar.
    for b in fx["buckets"]:
        assert (b["prints"] is None) != (b["seconds"] is None), b


def test_every_bucketing_is_the_median_of_its_own_closed_bars(fx, tape):
    """The whole ruler, every bucketing, at every playhead the fixture recorded.

    The three share one pass over the prints, which is the optimisation that
    makes the toggle free and also the one that could silently mix them — an
    accumulator reset on the wrong branch, a bar pushed to two of them. Each is
    re-derived here in its own independent pass, so a leak between them is a
    disagreement rather than a plausible number."""
    t, price = tape
    for r in fx["reads"]:
        for bucket in BUCKETS:
            want = reading_at(t, price, fx["spec"], r["playhead"], bucket)
            assert r["reading"][bucket] == want, f"{bucket} at playhead {r['playhead']}"


def test_the_bucketings_do_not_agree_with_each_other(fx):
    """If all three always returned the same number the toggle would be a
    decoration and every test above would pass against one accumulator wired up
    three times. At least one recorded playhead has to show them disagreeing."""
    assert any(
        len({r["reading"][b] for b in BUCKETS if r["reading"][b] is not None}) > 1
        for r in fx["reads"]
    ), [r["reading"] for r in fx["reads"]]


def test_nothing_is_read_before_the_window_opens(fx, tape):
    """The first row is inside the session and before the bell, and **every**
    bucketing reads nothing — the case that used to be the pre-bell fallback's
    and is now simply "no preset". If the fixture stops carrying it, the state
    the panels render an empty box for is untested."""
    t, _ = tape
    first = fx["reads"][0]
    assert (t[first["playhead"]] // 1000) % 86400 < PRESET_WIN_START
    assert all(v is None for v in first["reading"].values()), first["reading"]


def test_the_finest_bar_speaks_first(fx, tape):
    """MIN_BARS is three closed bars, so a 15-second bar has an answer while a
    30-second one is still on its second and a 500-print one may not have opened
    inside the window at all.

    That is a real consequence of the toggle rather than an accident: switching
    to a finer bar right after the bell is how you get a reading sooner, and it
    is why the panel shows every bucketing's number beside its name instead of
    only the selected one. The fixture's second row is the moment it happens."""
    t, _ = tape
    row = fx["reads"][1]
    assert PRESET_WIN_START <= (t[row["playhead"]] // 1000) % 86400 < WIN_END
    assert row["reading"]["s15"] is not None
    assert row["reading"]["s30"] is None and row["reading"]["t500"] is None


def test_the_window_opens_at_the_bell(fx, tape):
    """The opening bars are in the median, which is the whole point of the
    window: a bracket set at 09:32 has to be measured against the tape it is
    being set against, not against a settled window that has not opened.

    Asserted as a boundary on the clock bars rather than as a constant — the bar
    that opened one bar-length before the bell must be out and the one that
    opened on it must be in, both of which the tape carries because it starts at
    09:00. The volume bar is excluded here for the reason it exists: its bars do
    not land on clock boundaries at all."""
    assert PRESET_WIN_START == 9 * 3600 + 30 * 60
    t, price = tape
    spec = fx["spec"]
    for bucket, (prints, seconds) in BUCKETS.items():
        if prints is not None:
            continue
        bars = closed_bars(t, price, spec["sessionStart"], spec["n"] - 1, spec["tickSize"], bucket)
        opened = {sec % 86400 for sec, _ in bars}
        assert PRESET_WIN_START in opened, bucket
        assert PRESET_WIN_START - seconds in opened, bucket
        kept = {s for s in opened if PRESET_WIN_START <= s < WIN_END}
        assert min(kept) == PRESET_WIN_START, bucket


def test_the_bar_in_hand_is_not_in_any_median(fx):
    """Read at the first print of a bar and again a few seconds later, still
    inside it: every bucketing has the same bar open at both, so no answer may
    move.

    This is the promise `computeVolRuler` keeps by dropping its last bar, kept
    here without a bar array to drop from. A ruler that folded the forming bar
    in would climb as it filled, and the stop quoted on the ticket would differ
    between two glances at the same chart."""
    ob = fx["openBar"]
    assert ob["last"] > ob["first"]
    assert ob["a"] == ob["b"]
    assert all(v is not None for v in ob["a"].values()), ob["a"]
    assert ob["stable"] is True


def test_the_open_is_in_the_median_and_it_shows(fx):
    """The reason the window moved to the bell: the first clock-bar reading is
    taken entirely inside the open and must come out materially wider than the
    settled one.

    The generator's tape runs the first ten minutes of RTH at 2x for exactly
    this assertion. A window that opened later — at 09:35, or at the settled
    10:00 — would miss that stretch and the two readings would converge, so this
    is the test that fails if the opening bars ever stop counting.

    Clock bars only: on this tape the print *rate* is constant, so a 500-print
    bar spans 250 seconds and its first in-window bar does not close until well
    after the open. The fixture models a volatility change, not a rate change —
    it cannot speak to whether a volume clock flattens a real open, and nothing
    here claims it does."""
    for bucket, (prints, _) in BUCKETS.items():
        if prints is not None:
            continue
        vals = [r["reading"][bucket] for r in fx["reads"] if r["reading"][bucket] is not None]
        assert len(vals) >= 2, bucket
        assert vals[0] > vals[-1] * 1.5, (bucket, vals)


def test_no_bucketing_reaches_into_the_context_days(fx, tape):
    """A ruler that started at the tape's first print instead of the session's
    would still return a plausible number — so it is measured here and asserted
    to be a *different* one.

    The context days run 3x, so a leak reads far wide of the session. Checked
    per bucketing because the volume clock is the one with a second way to get
    this wrong: its bars are *counted from* the session's first print, so a
    start index off by anything at all reshuffles every bar boundary it has."""
    t, price = tape
    spec = fx["spec"]
    assert spec["sessionStart"] > 0 and spec["days"] > 1
    last = fx["reads"][-1]
    for bucket in BUCKETS:
        leaked = reading_at(t, price, {**spec, "sessionStart": 0}, last["playhead"], bucket)
        assert leaked is not None, bucket
        assert leaked > last["reading"][bucket], (bucket, leaked, last["reading"][bucket])


def test_incremental_agrees_with_a_standing_start(fx):
    """The ruler measures each block once and remembers. An answer that depends
    on how the playhead got there is a stop that depends on whether you scrubbed
    — the generator asks both ways and this is the verdict."""
    assert fx["coldAgrees"] is True


def test_a_seek_backwards_forgets_the_bars_that_have_not_happened(fx):
    """Read to the end of the session, then seek back: the ruler must return
    what it would have said at that moment, not what it learned afterwards."""
    assert fx["rewindAgrees"] is True
