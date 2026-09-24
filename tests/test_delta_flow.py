"""The delta lane's reading — bar lengths, flags, and the absorbed/initiative call.

``frontend/src/lib/deltaFlow.ts`` is what turned the delta lane from a shape you
squint at into three stated numbers, and every one of them is a judgement the
chart now makes on the reader's behalf. There is no Python twin to check it
against — unlike ``levelApproach`` this was never ported from an engine rule — so
these are unit tests rather than a parity check, run through the same harness for
the same reason: it is the only way TypeScript gets executed in this repo.

The load-bearing one is ``test_net_is_the_lane_as_it_was``. `net` is not a new
mode, it is the formula the primitive carried inline before any of this existed,
and a chart left on the default has to draw exactly what it drew last week. The
rest pin the branches a transcription slides off: the zero-volume row that must
not be divided by, the untagged profile that must come back empty rather than
flat, and the sign convention — selling that was followed by a *fall* is
initiative, not absorption, and getting that backwards would label every good
short a failure.

Run directly:  ``.venv/bin/python -m pytest tests/test_delta_flow.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TS_FILE = ROOT / "frontend/src/lib/deltaFlow.ts"
ESBUILD = ROOT / "frontend/node_modules/.bin/esbuild"

# A row's delta is spelled `null` when the source had no aggressor tag, which is
# the distinction `hasDelta` exists to carry — JSON has no `undefined`, and a
# fixture that spelled it 0.0 would be testing the wrong thing.
_DRIVER = """
import {
  readLane, classifyFlagged, windowedOnto, splitVisits, readVisitLane,
} from "./deltaFlow.mjs";
import { readFileSync } from "node:fs";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));

const mkProfile = (spec) => {
  const rows = spec.map(([low, high, volume, delta]) => {
    const r = { low, high, volume };
    if (delta !== null && delta !== undefined) r.delta = delta;
    return r;
  });
  let maxVolume = 0;
  let maxAbsDelta = 0;
  for (const r of rows) {
    if (r.volume > maxVolume) maxVolume = r.volume;
    const d = Math.abs(r.delta ?? 0);
    if (d > maxAbsDelta) maxAbsDelta = d;
  }
  return {
    rows,
    maxVolume,
    maxAbsDelta,
    hasDelta: rows.some((r) => r.delta !== undefined),
  };
};

const profile = mkProfile(fx.rows);
const { maxVolume, maxAbsDelta } = profile;

const lane = readLane(profile, fx.scale, fx.sigma);
const out = {
  maxAbsDelta,
  hasDelta: profile.hasDelta,
  frac: Array.from(lane.frac),
  delta: Array.from(lane.delta ?? []),
  flagged: lane.flagged,
};

// A second profile mapped onto the first's rows, and the lane read from it —
// the timed-window path.
if (fx.windowRows) {
  const ws = windowedOnto(profile, mkProfile(fx.windowRows));
  if (ws) {
    const wlane = readLane(ws, fx.scale, fx.sigma);
    out.window = {
      vols: ws.rows.map((r) => r.volume),
      dels: ws.rows.map((r) => r.delta),
      maxAbsDelta: ws.maxAbsDelta,
      frac: Array.from(wlane.frac),
      delta: Array.from(wlane.delta),
      flagged: wlane.flagged,
    };
  } else {
    out.window = null;
  }
}

// The visit split and its lane. `bars` here are the split's own — [high, low]
// pairs — and trades are [barIdx, price, delta] triples.
if (fx.visit) {
  const vbars = fx.visit.bars.map(([high, low]) => ({
    time: 0, open: 0, high, low, close: 0, volume: 0,
  }));
  const split = splitVisits(profile, vbars, (emit) => {
    for (const [b, price, d] of fx.visit.trades) emit(b, price, d);
  });
  const vlane = readVisitLane(profile, split);
  out.visit = {
    prior: Array.from(split.prior),
    visit: Array.from(split.visit),
    frac: Array.from(vlane.frac),
    delta: Array.from(vlane.delta),
    underFrac: Array.from(vlane.under.frac),
    underDelta: Array.from(vlane.under.delta),
    flagged: vlane.flagged,
  };
}

if (fx.bars) {
  const bars = fx.bars.map(([high, low, close]) => ({
    time: 0, open: close, high, low, close, volume: 0,
  }));
  const contrib = fx.contrib ?? {};
  const v = classifyFlagged(
    profile,
    fx.flagged ?? lane.flagged,
    (i) => Float64Array.from(contrib[String(i)] ?? []),
    bars,
    fx.forwardBars,
  );
  out.verdict = Object.fromEntries([...v.entries()].map(([k, s]) => [String(k), s]));
}

process.stdout.write(JSON.stringify(out));
"""


def _run(rows, scale="net", sigma=0.0, **extra):
    """Transpile deltaFlow.ts and read one fixture through it."""
    node = shutil.which("node")
    if node is None or not ESBUILD.exists():
        pytest.skip("needs node and the frontend's esbuild (pnpm install)")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        subprocess.run(
            [str(ESBUILD), str(TS_FILE), "--bundle", "--format=esm",
             "--platform=node", f"--outfile={d / 'deltaFlow.mjs'}"],
            check=True, capture_output=True,
        )
        (d / "run.mjs").write_text(_DRIVER)
        (d / "fx.json").write_text(
            json.dumps({"rows": rows, "scale": scale, "sigma": sigma, **extra})
        )
        got = subprocess.run([node, str(d / "run.mjs"), str(d / "fx.json")],
                             check=True, capture_output=True, text=True)
    return json.loads(got.stdout)


# A session shaped like a real one: a volume hump, two dozen ordinary rows
# leaning modestly either way, and the three rows the whole exercise is about.
#
# Sized realistically on purpose. An earlier six-row fixture flagged nothing at
# all, because with n that small one outlier inflates the standard deviation it
# is then measured against — an artefact of the fixture, not of the statistic,
# and the kind of thing that gets a working rule "fixed" until it breaks.
def _session():
    rows = []
    for i in range(24):
        # Volume humps toward the middle; the *deviation* is set directly (delta
        # scaled by sqrt(volume)) so the ordinary rows spread the same way at
        # every row size, which is the null the flag is measured against.
        d = abs(i - 12) / 12
        vol = round(400 + 2600 * (1 - d) ** 2)
        dev = ((i % 7) - 3) * 0.4           # roughly -1.2 .. +1.2, a modest lean
        rows.append([100.0 + i / 2, 100.5 + i / 2, float(vol), round(dev * vol**0.5)])
    return rows


BASE = _session()

# Heavy and barely one-sided: ratio 0.06, but 300 net lots out of 5000 is a long
# way past what chance produces at that size (deviation 4.2).
HEAVY = len(BASE)
BASE.append([112.0, 112.5, 5000.0, 300.0])

# Thin and strongly one-sided: ratio 0.75, deviation 10.6. Unusual on any
# measure, and the row the lane exists to surface.
THIN_ONEWAY = len(BASE)
BASE.append([112.5, 113.0, 200.0, 150.0])

# Six contracts, all one way. Ratio 1.0 — the *most* one-sided row on the chart,
# and the top of the imbalance lane — but six lots landing together is a coin
# toss, not a statement. Deviation 2.4. This is the row the flag used to pick.
NOISE = len(BASE)
BASE.append([113.0, 113.5, 6.0, 6.0])


# --- the mode that has to change nothing -------------------------------------

def test_net_is_the_lane_as_it_was():
    """`net` must reproduce |delta| / maxAbsDelta, which is what the primitive
    drew inline before this module existed. A default that redrew every chart
    would be this change breaking the thing it is trying to make readable."""
    got = _run(BASE, scale="net")
    mx = max(abs(r[3]) for r in BASE)
    assert got["maxAbsDelta"] == mx
    for frac, row in zip(got["frac"], BASE):
        assert frac == pytest.approx(abs(row[3]) / mx)


def test_net_ranks_by_size_not_one_sidedness():
    """The complaint that started this: in `net` the heavy two-sided row draws
    longer than the thin one-way one. Pinned, because it is the baseline the
    other modes are supposed to differ from — if this ever stopped being true,
    `imbalance` would be fixing a problem that had gone away."""
    got = _run(BASE, scale="net")
    assert got["frac"][HEAVY] > got["frac"][THIN_ONEWAY]


# --- the modes that make it readable -----------------------------------------

def test_imbalance_ranks_one_sidedness_not_size():
    """The thin one-way shelf finally out-draws the heavy row that netted flat —
    and the heavy row drops near the bottom of the lane, which is the reading."""
    got = _run(BASE, scale="imbalance")
    assert got["frac"][THIN_ONEWAY] > got["frac"][HEAVY]
    assert got["frac"][HEAVY] < 0.15


def test_imbalance_is_dominated_by_the_thinnest_row():
    """The honest cost of the ratio, stated so the trade-off is on the record:
    six contracts that landed together are 'entirely one-sided' and take the top
    of the lane. It is a fair display of what the ratio measures, and it is why
    the ratio is not what the flag is computed on."""
    got = _run(BASE, scale="imbalance")
    assert got["frac"][NOISE] == pytest.approx(1.0)


# --- the statistic the flag is actually computed on ---------------------------

def test_the_flag_does_not_just_find_thin_rows():
    """The defect this module was measured doing, and the reason `deviationOf`
    divides by sqrt(volume) rather than by volume.

    A row's delta swings by about sqrt(its volume) on chance alone, so the plain
    imbalance ratio is large for thin rows and *arithmetically cannot* be large
    for heavy ones. Ranking on it put every flag on a top-or-bottom tick. Here
    the six-lot row is the most one-sided row on the chart and must still not be
    flagged, while the heavy row that netted 300 of 5000 must be."""
    flagged = _run(BASE, scale="imbalance", sigma=2.0)["flagged"]
    assert NOISE not in flagged, "the flag is back to finding thin rows"
    assert THIN_ONEWAY in flagged
    assert HEAVY in flagged


def test_one_extreme_row_does_not_hide_the_moderate_ones():
    """Why the spread is a MAD and not a standard deviation.

    Squaring lets the most extreme row set the very ruler it is measured
    against, so a second, moderately unusual row disappears behind it. Measured:
    with mean/sd the 300-of-5000 row went unflagged purely because a more
    extreme row existed elsewhere on the same chart — and removing that row
    brought it back, which is the ruler moving rather than the market."""
    both = _run(BASE, scale="imbalance", sigma=2.0)["flagged"]
    assert HEAVY in both and THIN_ONEWAY in both

    # And the moderate row's own mark does not depend on the extreme one being
    # there: drop it, and HEAVY is still flagged.
    without = list(BASE)
    without.pop(THIN_ONEWAY)
    assert HEAVY in _run(without, scale="imbalance", sigma=2.0)["flagged"]


def test_flagging_survives_a_profile_that_mostly_agrees():
    """The MAD's degenerate case, which is a real profile and not a contrived
    one: when more than half the rows share a deviation the MAD is exactly zero,
    and a spread of zero would silently disable flagging on the very session
    where the one row that leaned differently is most worth marking."""
    rows = [[100.0 + i / 2, 100.5 + i / 2, 500.0, 400.0] for i in range(9)]
    rows[4][3] = -400.0
    assert _run(rows, scale="imbalance", sigma=2.0)["flagged"] == [4]


def test_zscore_lane_agrees_with_the_flag():
    """The z-score lane draws the same statistic the flag tests, so the longest
    bars and the marked rows are the same rows. A lane that ranked one way while
    the flag marked another would be the panel disagreeing with itself."""
    got = _run(BASE, scale="zscore", sigma=2.0)
    order = sorted(range(len(BASE)), key=lambda i: -got["frac"][i])
    assert set(got["flagged"]) <= set(order[: len(got["flagged"])])
    assert got["frac"][THIN_ONEWAY] == pytest.approx(1.0)
    assert got["frac"][NOISE] < got["frac"][HEAVY]


# --- flagging -----------------------------------------------------------------

def test_flags_the_unusual_rows_at_two_sigma():
    got = _run(BASE, scale="imbalance", sigma=2.0)
    assert got["flagged"] == [HEAVY, THIN_ONEWAY]


def test_a_tighter_threshold_flags_no_more_than_a_looser_one():
    marks = {s: _run(BASE, scale="imbalance", sigma=s)["flagged"] for s in (1.5, 2.0, 2.5)}
    assert set(marks[2.5]) <= set(marks[2.0]) <= set(marks[1.5])


def test_flagging_is_the_same_rows_whatever_the_lane_is_drawn_at():
    """The stated contract: the flag is a claim about the row, so switching the
    scale must not change which rows carry one. A reader who marks a row in one
    view and loses it in another has been told two different things."""
    marks = {s: _run(BASE, scale=s, sigma=2.0)["flagged"]
             for s in ("net", "imbalance", "zscore")}
    assert marks["net"] == marks["imbalance"] == marks["zscore"] == [HEAVY, THIN_ONEWAY]


def test_a_uniform_session_flags_nothing():
    """Every row equally one-sided: there is no spread to stand out from, and the
    honest answer at any threshold is that nothing does."""
    rows = [[100.0 + i / 2, 100.5 + i / 2, 500.0, 100.0] for i in range(8)]
    for sigma in (1.5, 2.0, 2.5):
        assert _run(rows, scale="imbalance", sigma=sigma)["flagged"] == []


def test_a_row_that_netted_flat_can_still_be_flagged():
    """The case the renderer draws a cap for even with no bar: in a session where
    every row was bought, the row that *wasn't* is the unusual one. Its lane
    length is zero and its flag is the only mark it gets."""
    rows = [[100.0 + i / 2, 100.5 + i / 2, 500.0, 400.0] for i in range(8)]
    rows[3][3] = 0.0
    got = _run(rows, scale="imbalance", sigma=2.0)
    assert got["flagged"] == [3]
    assert got["frac"][3] == 0.0


# --- the guards ---------------------------------------------------------------

def test_no_delta_reads_empty_not_flat():
    """An untagged profile has no lane, and must not come back as a row of
    zeros that looks like a session which traded perfectly two-sided."""
    rows = [[100.0 + i / 2, 100.5 + i / 2, 500.0, None] for i in range(5)]
    got = _run(rows, scale="imbalance", sigma=2.0)
    assert got["hasDelta"] is False
    assert got["flagged"] == []
    assert got["frac"] == [0.0] * 5


def test_zero_volume_rows_are_excluded_not_divided_by():
    """A row that caught no trades has no imbalance ratio — not a zero one. If it
    reached the statistics it would arrive as an Inf or a NaN and take the whole
    lane with it."""
    rows = list(map(list, BASE))
    rows.insert(3, [101.4, 101.45, 0.0, 0.0])
    got = _run(rows, scale="zscore", sigma=2.0)
    assert all(f == f and abs(f) != float("inf") for f in got["frac"])  # no NaN/Inf
    assert got["frac"][3] == 0.0


# --- the verdict --------------------------------------------------------------

# Six bars, each 10 wide, so the median range is 10 and the tolerance is 5.
# The flagged row is centred on 100.
def _bars(closes):
    return [[c + 5.0, c - 5.0, c] for c in closes]


ROW = [[99.5, 100.5, 500.0, 400.0]]          # bought hard, centre 100
ROW_SOLD = [[99.5, 100.5, 500.0, -400.0]]    # sold hard, same centre
PEAK_AT_0 = {"0": [400.0, 0.0, 0.0, 0.0, 0.0, 0.0]}


def test_buying_that_ran_is_initiative():
    got = _run(ROW, sigma=0.0, flagged=[0], contrib=PEAK_AT_0,
               bars=_bars([100, 105, 112, 112, 112, 112]), forwardBars=2)
    assert got["verdict"] == {"0": "initiative"}


def test_buying_that_went_nowhere_is_absorbed():
    got = _run(ROW, sigma=0.0, flagged=[0], contrib=PEAK_AT_0,
               bars=_bars([100, 101, 102, 102, 102, 102]), forwardBars=2)
    assert got["verdict"] == {"0": "absorbed"}


def test_buying_that_was_followed_by_a_fall_is_absorbed():
    """Adverse, not merely flat — and still absorbed. Someone was on the other
    side of all that buying, which is the whole reading."""
    got = _run(ROW, sigma=0.0, flagged=[0], contrib=PEAK_AT_0,
               bars=_bars([100, 96, 92, 92, 92, 92]), forwardBars=2)
    assert got["verdict"] == {"0": "absorbed"}


def test_selling_that_ran_down_is_initiative():
    """The sign convention. Measured against the row's own direction, so a short
    that worked is initiative — reading it off raw displacement would call every
    good short an absorption."""
    got = _run(ROW_SOLD, sigma=0.0, flagged=[0], contrib=PEAK_AT_0,
               bars=_bars([100, 95, 88, 88, 88, 88]), forwardBars=2)
    assert got["verdict"] == {"0": "initiative"}


def test_no_verdict_without_room_to_look_forward():
    """A row whose delta landed too near the right edge gets no mark: measuring
    against a bar that hasn't happened would answer with the end of the window
    rather than with the market."""
    contrib = {"0": [0.0, 0.0, 0.0, 0.0, 0.0, 400.0]}   # peak on the last bar
    got = _run(ROW, sigma=0.0, flagged=[0], contrib=contrib,
               bars=_bars([100, 101, 102, 103, 104, 105]), forwardBars=2)
    assert got["verdict"] == {}


def test_the_peak_bar_is_where_the_delta_landed():
    """Two visits to one row, and the verdict has to hang off the bigger one.
    Averaging the visits — or taking the first — would measure forward from a
    moment that row was not made at."""
    contrib = {"0": [80.0, 0.0, 0.0, 400.0, 0.0, 0.0]}   # the real visit is bar 3
    # Flat after bar 0 (so a bar-0 anchor would say absorbed), running after
    # bar 3 (so the correct anchor says initiative).
    got = _run(ROW, sigma=0.0, flagged=[0], contrib=contrib,
               bars=_bars([100, 100, 100, 100, 106, 118]), forwardBars=2)
    assert got["verdict"] == {"0": "initiative"}


# --- the reading carries its own delta -----------------------------------------

def test_the_lane_reports_the_delta_it_read():
    """`LaneReading.delta` is what colours the bars, and it must be the deltas of
    the source the lane was read from — for the session lane, the rows' own."""
    got = _run(BASE, scale="net")
    assert got["delta"] == [float(r[3]) for r in BASE]


# --- the timed window ----------------------------------------------------------

# Three half-point viewport rows. The window rows are finer and sit on a
# different grid — each must land, whole, in the viewport row holding its
# centre, because the two profiles' binning is *not* guaranteed to line up.
VIEW = [[100.0, 100.5, 1000.0, 500.0],
        [100.5, 101.0, 800.0, -200.0],
        [101.0, 101.5, 600.0, 100.0]]
WIN = [[100.1, 100.3, 50.0, 30.0],      # inside viewport row 0
       [100.6, 100.9, 70.0, -70.0],     # inside viewport row 1
       [100.95, 101.2, 40.0, -40.0]]    # centre 101.075 → viewport row 2


def test_windowed_rows_land_by_centre_on_the_viewport_grid():
    got = _run(VIEW, scale="net", windowRows=WIN)["window"]
    assert got["vols"] == [50.0, 70.0, 40.0]
    assert got["dels"] == [30.0, -70.0, -40.0]
    assert got["maxAbsDelta"] == 70.0


def test_windowed_lane_is_the_windows_lengths_and_signs():
    """The point of the window: a row the session bought can be one the window
    sold, and both the bar's length and its colour must say so. Viewport row 2
    carries +100 on the session and -40 in the window — the windowed lane must
    report the minus."""
    got = _run(VIEW, scale="net", windowRows=WIN)["window"]
    assert got["frac"] == pytest.approx([30 / 70, 1.0, 40 / 70])
    assert got["delta"][2] == -40.0


def test_a_window_with_no_delta_reads_null_not_flat():
    """An untagged window maps to nothing — the callers fall back to the session
    lane rather than drawing a lane of zeros that claims the window was flat."""
    win = [[100.1, 100.3, 50.0, None]]
    assert _run(VIEW, scale="net", windowRows=win)["window"] is None


# --- the visit split ------------------------------------------------------------

# Six half-point rows covering 100.0–103.0, all volume-backed so every price the
# bars visit has a row to land in. The session deltas are irrelevant to the
# split — it files the *trades* it is handed — so they are zero.
VROWS = [[100.0 + i / 2, 100.5 + i / 2, 500.0, 0.0] for i in range(6)]

# Price sits at ~100.2 (row 0) for two bars, leaves for ~102.8 (row 5) for two,
# and returns to row 0 on the last bar.
VBARS = [[100.4, 100.0], [100.4, 100.0], [103.0, 102.6], [103.0, 102.6], [100.4, 100.0]]


def test_visit_split_files_the_latest_run_apart_from_the_rest():
    """Row 0 was visited twice — bars 0–1 and bar 4 — so its latest visit is the
    return alone, and the morning's buying belongs to `prior`. Row 5's only run
    is bars 2–3, so everything there is its (latest) visit."""
    got = _run(VROWS, visit={"bars": VBARS,
                             "trades": [[0, 100.2, 50.0],
                                        [2, 102.8, 20.0],
                                        [4, 100.2, -30.0]]})["visit"]
    assert got["prior"][0] == 50.0 and got["visit"][0] == -30.0
    assert got["prior"][5] == 0.0 and got["visit"][5] == 20.0


def test_a_flip_is_capped_and_a_lean_that_held_is_not():
    """Row 0's return sold against its bought morning — flagged. Row 5 leans one
    way only — not. And the lane's two segments are the split, scaled against
    the biggest segment on the chart so their lengths compare."""
    got = _run(VROWS, visit={"bars": VBARS,
                             "trades": [[0, 100.2, 50.0],
                                        [2, 102.8, 20.0],
                                        [4, 100.2, -30.0]]})["visit"]
    assert got["flagged"] == [0]
    assert got["frac"][0] == pytest.approx(30 / 50)
    assert got["underFrac"][0] == pytest.approx(1.0)
    assert got["delta"][0] == -30.0 and got["underDelta"][0] == 50.0


def test_a_featherweight_flip_earns_no_cap():
    """Two lots against the morning's fifty is a coin toss, not a flip — the
    relevance floor is what keeps the lane from capping every third row."""
    got = _run(VROWS, visit={"bars": VBARS,
                             "trades": [[0, 100.2, 50.0],
                                        [4, 100.2, -2.0]]})["visit"]
    assert got["flagged"] == []


def test_an_unbroken_stay_is_all_one_visit():
    """Price never left row 0 between its trades, so the run is one visit and
    nothing is filed as prior — a quiet bar sitting on the price must not split
    the stay in two."""
    bars = [[100.4, 100.0]] * 5
    got = _run(VROWS, visit={"bars": bars,
                             "trades": [[0, 100.2, 50.0], [4, 100.2, -30.0]]})["visit"]
    assert got["prior"][0] == 0.0
    assert got["visit"][0] == pytest.approx(20.0)
