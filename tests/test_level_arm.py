"""The armed-level geometry, run as the TypeScript it actually is.

``frontend/src/lib/levelArm.ts`` decides two things and nothing else: whether the
tape crossed an armed level over a played tick range, and — given that crossing —
which price the reclaim order rests at and which of the chart's two click
geometries puts it there. Both are pure, and both are wrong in ways that look
fine on screen: a stop placed on the side price came *from* and a stop placed on
the side it went *to* are the same order shape at the same distance, and only one
of them is a reclaim.

There is no test runner in ``frontend/``, so this is the runner: esbuild bundles
the module with the frontend's own binary and node executes it, the same bridge
``tests/test_level_approach.py`` and ``tests/test_modern_vwap.py`` use. It skips
rather than passes when either is missing — a skipped check is a known gap, a
green one that never ran is a lie.

What this deliberately does *not* cover is the wiring: that the panel offers the
control, that firing reaches ``placeOrder`` and inherits its gates, that a rewind
un-places what an arm placed. Those need the app, and they are
``tools/browser/armcheck.mjs``.

Run directly:  ``.venv/bin/python -m pytest tests/test_level_arm.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TS_FILE = ROOT / "frontend/src/lib/levelArm.ts"
ESBUILD = ROOT / "frontend/node_modules/.bin/esbuild"

_DRIVER = """
import {
  crossDirection, throughPlacement, limitPlacement, armAction, armPurpose,
  DEFAULT_THROUGH_TICKS, ARM_REACH_TICKS,
} from "./levelArm.mjs";
import { readFileSync } from "node:fs";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
// JSON has no NaN, so the fixture spells "not a number" as null and it is put
// back here — the non-finite guard is a branch, so it may not be lost in
// transport.
const num = (x) => (x === null ? NaN : x);
const out = fx.cases.map((c) => {
  const price = Float64Array.from(c.price.map(num));
  const prev = num(c.prev);
  const tick = c.tick ?? 0.25;
  const arm = {
    key: "k", label: "l", shape: c.shape ?? "through",
    price: c.level, throughTicks: c.throughTicks ?? DEFAULT_THROUGH_TICKS,
  };
  const dir = crossDirection(price, c.from, c.to, prev, c.level);
  return {
    dir,
    through: dir === 0 ? null : throughPlacement(arm, dir, tick),
    limit: limitPlacement(arm),
    action: armAction(arm, price, c.from, c.to, prev, tick),
  };
});
process.stdout.write(JSON.stringify({
  constants: { DEFAULT_THROUGH_TICKS, ARM_REACH_TICKS },
  purpose: Object.fromEntries(
    ["limit", "through", "exit"].map((s) => [s, armPurpose(s)]),
  ),
  out,
}));
"""


def _run(cases):
    node = shutil.which("node")
    if node is None or not ESBUILD.exists():
        pytest.skip("needs node and the frontend's esbuild (pnpm install)")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        subprocess.run(
            [str(ESBUILD), str(TS_FILE), "--bundle", "--format=esm",
             "--platform=node", f"--outfile={d / 'levelArm.mjs'}"],
            check=True, capture_output=True,
        )
        (d / "run.mjs").write_text(_DRIVER)
        (d / "fx.json").write_text(json.dumps({"cases": cases}))
        got = subprocess.run([node, str(d / "run.mjs"), str(d / "fx.json")],
                             check=True, capture_output=True, text=True)
    return json.loads(got.stdout)


def _one(**kw):
    kw.setdefault("from", 0)
    kw.setdefault("to", len(kw["price"]))
    return _run([kw])["out"][0]


# --- the crossing ------------------------------------------------------------

def test_crossing_down_and_up():
    """The predicate is the price line's own alert: touching counts as reaching."""
    down = _one(price=[101.0, 100.5, 99.5], prev=101.5, level=100.0)
    up = _one(price=[99.0, 99.5, 100.5], prev=98.5, level=100.0)
    assert down["dir"] == -1
    assert up["dir"] == 1


def test_landing_exactly_on_the_level_is_a_crossing():
    """`>=` / `<=` on the far side — a level price stopped dead on was reached."""
    assert _one(price=[100.0], prev=101.0, level=100.0)["dir"] == -1
    assert _one(price=[100.0], prev=99.0, level=100.0)["dir"] == 1


def test_no_crossing_when_the_range_stays_one_side():
    assert _one(price=[101.0, 102.0, 101.5], prev=101.0, level=100.0)["dir"] == 0


def test_the_first_crossing_in_the_range_wins():
    """Down then back up inside one frame reports the down: an arm is spent when
    it fires, and the event that fired it is the one that happened first."""
    assert _one(price=[99.0, 101.0], prev=101.0, level=100.0)["dir"] == -1


def test_the_price_before_the_range_is_part_of_it():
    """A range whose own first print is already through the level still crossed —
    the transition is from `prev`, which is the whole reason it is passed in."""
    assert _one(price=[99.0, 98.0], prev=101.0, level=100.0)["dir"] == -1
    # ...and the same prints with `prev` already below are not a crossing.
    assert _one(price=[99.0, 98.0], prev=99.5, level=100.0)["dir"] == 0


def test_only_the_named_range_is_walked():
    """Prints outside `[from, to)` are somebody else's frame. A crossing in the
    tail must not be reported by a call that was not handed it."""
    px = [101.0, 100.5, 99.0, 98.0]
    assert _one(price=px, prev=101.5, level=100.0, **{"from": 0, "to": 2})["dir"] == 0
    assert _one(price=px, prev=101.5, level=100.0, **{"from": 0, "to": 3})["dir"] == -1


def test_no_crossing_before_there_is_a_price_to_cross_from():
    """The first range of a session has no `prev`. Answering anything but 0 there
    would fire every arm the moment the tape opened."""
    assert _one(price=[100.0], prev=None, level=100.0)["dir"] == 0


# --- the order the crossing places -------------------------------------------

def test_a_down_cross_rests_the_stop_above_the_level():
    """The reclaim. Price gave the level up going down, so the trade is a long
    back over it: the stop sits `throughTicks` **above**, and it is the right
    button — the geometry the chart calls "an order price has to be run through".

    This is the one that has no symptom if it is inverted. A stop four ticks
    *below* a level price just broke is a perfectly ordinary breakout entry; it
    is simply the opposite trade to the one this shape exists to take.
    """
    got = _one(price=[99.5], prev=101.0, level=100.0, throughTicks=4, tick=0.25)
    assert got["dir"] == -1
    assert got["through"] == {"price": 101.0, "button": "right"}


def test_an_up_cross_rests_the_stop_below_the_level():
    got = _one(price=[100.5], prev=99.0, level=100.0, throughTicks=4, tick=0.25)
    assert got["dir"] == 1
    assert got["through"] == {"price": 99.0, "button": "right"}


def test_the_offset_is_in_ticks_not_points():
    got = _one(price=[99.5], prev=101.0, level=100.0, throughTicks=8, tick=0.25)
    assert got["through"]["price"] == 102.0


def test_a_bid_is_the_level_itself_and_the_passive_button():
    """No offset and no trigger: `left` is a bid under the market and an offer
    over it, so the side falls out of where the level is rather than being
    chosen."""
    assert _one(price=[100.0], prev=100.0, level=100.0)["limit"] == {
        "price": 100.0, "button": "left",
    }


# --- what the caller actually asks ------------------------------------------

def test_a_bid_never_waits_for_anything():
    """`armAction` answers null for a limit at every range: it was placed when it
    was armed, and polling for it would place a second one."""
    got = _one(price=[99.0], prev=101.0, level=100.0, shape="limit")
    assert got["action"] is None


def test_a_through_arm_answers_only_on_the_crossing():
    crossed = _one(price=[99.0], prev=101.0, level=100.0, shape="through")
    quiet = _one(price=[101.5], prev=101.0, level=100.0, shape="through")
    assert crossed["action"] == {"kind": "place", "price": 101.0, "button": "right"}
    assert quiet["action"] is None


def test_the_defaults_are_the_ones_documented():
    """Pinned so a change to either is a change somebody made on purpose. Four
    ticks is one NQ point; the reach is about a session's range."""
    c = _run([{"price": [100.0], "prev": 100.0, "level": 100.0, "from": 0, "to": 1}])
    assert c["constants"] == {"DEFAULT_THROUGH_TICKS": 4, "ARM_REACH_TICKS": 240}


# --- the exit shape ----------------------------------------------------------
# An `exit` arm closes rather than opens, and it is the one shape whose crossing
# *direction* is thrown away — a flatten has no side to pick, the position it is
# taking off already has one.

def test_an_exit_fires_on_a_crossing_from_either_side():
    """The asymmetry that matters. A `through` arm crossed downward and one
    crossed upward are opposite trades; an exit crossed either way is the same
    exit, so both directions must produce the identical answer."""
    down = _one(price=[99.0], prev=101.0, level=100.0, shape="exit")
    up = _one(price=[101.0], prev=99.0, level=100.0, shape="exit")
    assert down["dir"] == -1
    assert up["dir"] == 1
    assert down["action"] == {"kind": "close"}
    assert up["action"] == {"kind": "close"}


def test_an_exit_stays_quiet_until_the_level_is_reached():
    """It is standing state, not a flatten now: a range that never touches the
    level must not take the position off."""
    assert _one(price=[101.5, 102.0], prev=101.0, level=100.0, shape="exit")["action"] is None


def test_an_exit_carries_no_price_or_button():
    """A closing arm is routed through the page's flatten, which has nothing to
    read a price off. Spelling one anyway is how a `close` grows a field some
    later caller starts trusting."""
    got = _one(price=[99.0], prev=101.0, level=100.0, shape="exit")
    assert set(got["action"]) == {"kind"}


def test_the_through_offset_does_not_leak_into_an_exit():
    """`throughTicks` is on every arm because they share a record. It must not
    move an exit off the level it was armed at — an exit is triggered *at* the
    level, and there is nothing four ticks away to trigger on."""
    got = _one(price=[99.0], prev=101.0, level=100.0, shape="exit", throughTicks=40)
    assert got["action"] == {"kind": "close"}


def test_only_the_exit_shape_closes():
    """The race is run within a purpose, so mis-sorting a shape would let an exit
    firing cancel a pending entry — the one thing the split exists to prevent."""
    c = _run([{"price": [100.0], "prev": 100.0, "level": 100.0, "from": 0, "to": 1}])
    assert c["purpose"] == {"limit": "entry", "through": "entry", "exit": "exit"}
