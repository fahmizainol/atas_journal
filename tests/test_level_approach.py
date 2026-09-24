"""The approach classification's TypeScript port — and the check that pins it.

``src/journal/sim/profile.gap_closer`` says its definition "lives in exactly one
place", and it means it: the engine's drift-touch fade and the Interactions Lab
both call it, which is why a touch classed ``drift`` in the Lab is the same event
the adopted rule traded. ``frontend/src/lib/levelApproach.ts`` now reads the same
classification onto the live chart, in another language — and a drift between the
two has no symptom, because both sides return a plausible-looking word.

So the load-bearing test here is parity: the same level path and the same closes
through both implementations, every bar index compared. It transpiles the
TypeScript with the frontend's own esbuild and runs it under node, and skips
(rather than passes) when either is absent — a skipped parity check is a known
gap; a green one that never ran is a lie. The same shape as
``tests/test_modern_vwap.py``, which fences the other cross-language port.

The rest pin the branches individually, including the three boundaries a
transcription is most likely to slide off: the ``total == 0`` guard that must
answer *drift* rather than divide, and the two share thresholds, which are
inclusive on both sides.

Run directly:  ``.venv/bin/python tests/test_level_approach.py``
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.sim import profile as prof  # noqa: E402

TS_FILE = ROOT / "frontend/src/lib/levelApproach.ts"
ESBUILD = ROOT / "frontend/node_modules/.bin/esbuild"

# JSON has no NaN, so the fixture spells a missing level value `null` and the
# driver puts the NaN back — the very case the "level younger than the window"
# branch turns on, so it may not be quietly dropped in transport.
_DRIVER = """
import { gapCloser, GAP_LOOKBACK_BARS } from "./levelApproach.mjs";
import { readFileSync } from "node:fs";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const nan = (a) => a.map((x) => (x === null ? NaN : x));
const values = nan(fx.values);
const close = nan(fx.close);
const lookback = fx.lookback ?? GAP_LOOKBACK_BARS;
const out = values.map((_, i) => gapCloser(values, close, i, lookback));
process.stdout.write(JSON.stringify({ lookback: GAP_LOOKBACK_BARS, classes: out }));
"""


def _ts_classes(values, close, lookback=None):
    """Run the TypeScript port over a path and hand back its class per bar."""
    node = shutil.which("node")
    if node is None or not ESBUILD.exists():
        pytest.skip("parity needs node and the frontend's esbuild (pnpm install)")
    jsonable = lambda a: [None if np.isnan(x) else float(x) for x in a]  # noqa: E731
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        subprocess.run(
            [str(ESBUILD), str(TS_FILE), "--bundle", "--format=esm",
             "--platform=node", f"--outfile={d / 'levelApproach.mjs'}"],
            check=True, capture_output=True,
        )
        (d / "run.mjs").write_text(_DRIVER)
        fx = {"values": jsonable(values), "close": jsonable(close)}
        if lookback is not None:
            fx["lookback"] = lookback
        (d / "fx.json").write_text(json.dumps(fx))
        got = subprocess.run([node, str(d / "run.mjs"), str(d / "fx.json")],
                             check=True, capture_output=True, text=True)
    return json.loads(got.stdout)


def _py_classes(values, close, lookback=None):
    kw = {} if lookback is None else {"lookback": lookback}
    return [prof.gap_closer(values, close, i, **kw)[0] for i in range(len(values))]


# --- parity with the classification the chart actually draws ----------------

def test_matches_the_typescript():
    """The check the module's cross-reference comment is claiming.

    A random walk with a *lagging* level path — which is what a developing level
    is — so the window covers every branch rather than an agreement that only
    holds because nothing ever moved. Every bar index is compared, not a
    summary: the classification is read at one bar at a time on the chart, and a
    port that agreed on aggregate while disagreeing bar-by-bar would be exactly
    the failure this exists to catch.
    """
    rng = np.random.default_rng(11)
    n = 600
    walk = 20_000 + np.cumsum(rng.normal(0, 1.5, n))
    close = np.round(walk * 4) / 4
    # Lagging, and crossing the walk repeatedly — so the level is sometimes
    # overhead and sometimes below, which is the sign flip in `toward`.
    values = np.round(pd.Series(walk).rolling(40, min_periods=1).median().to_numpy() * 4) / 4

    got = _ts_classes(values, close)
    assert got["lookback"] == prof.GAP_LOOKBACK_BARS, "the two lookbacks have drifted apart"
    assert got["classes"] == _py_classes(values, close)
    # And the fixture is not degenerate: a walk that only ever produced one class
    # would pass whatever the port did with the others.
    assert len(set(got["classes"])) >= 4, f"fixture too thin: {set(got['classes'])}"


def test_matches_the_typescript_with_gaps_in_the_level():
    """A level younger than the window — the NaN branch, which JSON cannot carry
    and a transcription can silently turn into 0."""
    rng = np.random.default_rng(5)
    n = 120
    close = 20_000 + np.cumsum(rng.normal(0, 1.0, n))
    values = close + rng.normal(0, 3.0, n)
    values[:37] = np.nan          # the level does not exist yet
    values[60:64] = np.nan        # and a hole inside the window later

    got = _ts_classes(values, close)
    assert got["classes"] == _py_classes(values, close)
    assert "unknown" in got["classes"]


def test_the_lookback_constant_is_pinned_on_both_sides():
    """Bump it in one language and this fails rather than the read quietly
    changing meaning on the chart."""
    src = TS_FILE.read_text()
    m = re.search(r"GAP_LOOKBACK_BARS\s*=\s*(\d+)", src)
    assert m, "the TS port no longer declares GAP_LOOKBACK_BARS"
    assert int(m.group(1)) == prof.GAP_LOOKBACK_BARS


# --- the branches, pinned one at a time -------------------------------------

def _case(values, close, lookback=2):
    v = np.array(values, dtype=float)
    c = np.array(close, dtype=float)
    i = len(v) - 1
    return prof.gap_closer(v, c, i, lookback=lookback)[0], (v, c, lookback)


def test_price_led_is_price_closing_the_gap():
    """Price walks up to a level that never moved: a momentum test."""
    cls, fx = _case([110, 110, 110], [100, 104, 108])
    assert cls == "price"
    assert _ts_classes(*fx)["classes"][-1] == "price"


def test_level_led_is_the_level_coming_to_price():
    """A falling band chased by price — it 'touches' without price testing
    anything, which is the whole reason this classification exists."""
    cls, fx = _case([110, 106, 101], [100, 100, 100])
    assert cls == "level"
    assert _ts_classes(*fx)["classes"][-1] == "level"


def test_both_is_meeting_in_the_middle():
    cls, fx = _case([110, 108, 106], [100, 102, 104])
    assert cls == "both"
    assert _ts_classes(*fx)["classes"][-1] == "both"


def test_drift_is_never_converging():
    """They ended further apart than they started: price was loitering, not
    approaching. `total <= 0` must answer here rather than reach the division."""
    cls, fx = _case([110, 111, 112], [100, 100, 99])
    assert cls == "drift"
    assert _ts_classes(*fx)["classes"][-1] == "drift"


def test_total_exactly_zero_is_drift_not_a_division():
    """Price closes exactly as much as the level opens. The guard is `<= 0`, so
    this is drift; a port that wrote `< 0` divides by zero and answers `level`."""
    cls, fx = _case([110, 112, 114], [100, 102, 104])
    assert cls == "drift"
    assert _ts_classes(*fx)["classes"][-1] == "drift"


def test_the_share_thresholds_are_inclusive_on_both_sides():
    """0.6 is level and 0.4 is price — the two boundaries a transcription slides
    off by writing a strict comparison."""
    # level closes 6 of a 10-point convergence -> share 0.6
    cls, fx = _case([110, 107, 104], [100, 102, 104])
    assert cls == "level"
    assert _ts_classes(*fx)["classes"][-1] == "level"
    # level closes 4 of 10 -> share 0.4
    cls, fx = _case([110, 108, 106], [100, 103, 106])
    assert cls == "price"
    assert _ts_classes(*fx)["classes"][-1] == "price"


def test_no_history_yet_is_unknown():
    """The first bar has no window behind it, and answering anything else would
    class a level the chart has only just drawn."""
    v = np.array([110.0, 109.0, 108.0])
    c = np.array([100.0, 101.0, 102.0])
    assert prof.gap_closer(v, c, 0, lookback=2)[0] == "unknown"
    assert _ts_classes(v, c, 2)["classes"][0] == "unknown"


def test_a_level_sitting_exactly_on_the_close_is_drift():
    """`toward` is a sign, and the sign of zero is zero — so both distances
    collapse and the window says nothing. Pinned because Python's np.sign and
    JavaScript's Math.sign agreeing on 0 is a coincidence worth a test."""
    cls, fx = _case([100, 101, 102], [100, 100, 100])
    assert cls == "drift"
    assert _ts_classes(*fx)["classes"][-1] == "drift"


# --- the rows the panel draws ----------------------------------------------
#
# `clusterLevels` has no Python counterpart to be parity-checked against — it is
# presentation, not arithmetic. It is driven from here anyway because the two
# things it decides (when a stack of levels is one level, and what a stack says
# when its members disagree) are invisible on screen when wrong: a chained row
# and an honest row look identical, and that is the failure this feature exists
# to avoid rather than commit.

_CLUSTER_DRIVER = """
import { clusterLevels } from "./levelApproach.mjs";
import { readFileSync } from "node:fs";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const nan = (a) => a.map((x) => (x === null ? NaN : x));
const levels = fx.levels.map((l) => ({ ...l, path: nan(l.path) }));
const rows = clusterLevels(levels, nan(fx.close), fx.i, fx.price, fx.tickSize, fx.opts ?? {});
process.stdout.write(JSON.stringify(rows));
"""

TICK = 0.25
# Six bars, which is the window `GAP_LOOKBACK_BARS` actually spans. The fixtures
# are kept *consistent* — a level's path ends where its price is — because an
# inconsistent one can pass for the wrong reason: a static level sitting exactly
# on the window's first close has `toward == 0` and reads drift however price
# moved afterwards, which is correct and would silently satisfy a careless test.
_CLIMB = [92.0, 94.0, 95.5, 97.0, 98.0, 99.0]     # price climbing toward 100
_CREEP = [99.0, 99.2, 99.4, 99.6, 99.7, 99.8]     # price barely moving
_SLIP = [99.0, 98.8, 98.6, 98.4, 98.2, 98.0]      # price easing away


def _static(price):
    """A level that cannot move — a drawn line, a frozen composite level."""
    return [price] * 6


def _falls_to(price, frm=110.0):
    """A band coming down to `price` — the level doing the closing."""
    step = (frm - price) / 5
    return [frm - step * k for k in range(6)]


def _rows(levels, close, price, tick=TICK, i=5, opts=None):
    node = shutil.which("node")
    if node is None or not ESBUILD.exists():
        pytest.skip("needs node and the frontend's esbuild (pnpm install)")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        subprocess.run(
            [str(ESBUILD), str(TS_FILE), "--bundle", "--format=esm",
             "--platform=node", f"--outfile={d / 'levelApproach.mjs'}"],
            check=True, capture_output=True,
        )
        (d / "run.mjs").write_text(_CLUSTER_DRIVER)
        (d / "fx.json").write_text(json.dumps({
            "levels": levels, "close": close, "i": i, "price": price,
            "tickSize": tick, "opts": opts or {},
        }))
        got = subprocess.run([node, str(d / "run.mjs"), str(d / "fx.json")],
                             check=True, capture_output=True, text=True)
    return json.loads(got.stdout)


def _lvl(label, price, path, family="test"):
    return {"label": label, "price": price, "path": path, "family": family}


def test_a_ladder_of_near_levels_does_not_chain_into_one_row():
    """Single-linkage clustering's classic failure, and the one that would turn
    this from a confluence reader into a confluence launderer: twelve levels each
    3 ticks from the next span 33 ticks, which is not 'the same price'."""
    levels = [
        _lvl(f"L{k}", 100.0 + k * 3 * TICK, _static(100.0 + k * 3 * TICK)) for k in range(12)
    ]
    by_label = {l["label"]: l["price"] for l in levels}
    rows = _rows(levels, _CLIMB, price=99.0)

    assert len(rows) > 1, "the whole ladder collapsed into one row"
    # The cap is twice the collinear tolerance — 8 ticks — so no row may span
    # more than that however gently the ladder steps.
    for r in rows:
        prices = [by_label[m["label"]] for m in r["members"]]
        spread = max(prices) - min(prices)
        assert spread <= 2 * 4 * TICK + 1e-9, f"row spans {spread} across {len(prices)} levels"


def test_levels_at_the_same_price_become_one_row():
    """Three names for one price is a stack of one — the whole point."""
    levels = [
        _lvl("NY VWAP +1σ", 100.0, _static(100.0)),
        _lvl("GX VAH", 100.25, _static(100.25)),
        _lvl("your line", 100.0, _static(100.0)),
    ]
    rows = _rows(levels, _CLIMB, price=99.0)
    assert len(rows) == 1
    assert len(rows[0]["members"]) == 3
    # Price climbed 7 points to them and they never moved: price-led, all three.
    assert rows[0]["cls"] == "price"


def test_members_that_disagree_report_mixed():
    """A static reference and a moving one at the same price can disagree about
    who closed the gap, and that disagreement is the reading — not something to
    average away. Price creeps up 0.8 while the band falls 9: the line is a
    momentum test, the band came to price."""
    levels = [
        _lvl("your line", 101.0, _static(101.0)),
        _lvl("a falling band", 101.0, _falls_to(101.0)),
    ]
    rows = _rows(levels, _CREEP, price=100.0)
    assert len(rows) == 1
    assert rows[0]["cls"] == "mixed"
    assert {m["cls"] for m in rows[0]["members"]} == {"price", "level"}


def test_a_member_too_young_to_answer_does_not_outvote_the_others():
    """`unknown` is an absence, not a disagreement: a level drawn two bars ago
    must not turn a row 'mixed' and hide what the others are saying."""
    young = [None] * 4 + [100.0, 100.0]           # drawn two bars ago
    levels = [_lvl("old", 100.0, _static(100.0)), _lvl("young", 100.0, young)]
    rows = _rows(levels, _CLIMB, price=99.0)
    assert len(rows) == 1
    assert rows[0]["cls"] == "price"
    assert {m["cls"] for m in rows[0]["members"]} == {"price", "unknown"}


def test_a_row_nobody_can_answer_for_stays_unknown():
    rows = _rows([_lvl("young", 100.0, [None] * 6)], _CLIMB, price=99.0)
    assert rows[0]["cls"] == "unknown"


def test_levels_beyond_the_radius_get_no_row():
    levels = [_lvl("near", 100.5, _static(100.5)), _lvl("far", 130.0, _static(130.0))]
    rows = _rows(levels, _SLIP, price=100.0)
    assert [m["label"] for r in rows for m in r["members"]] == ["near"]


def test_rows_come_back_in_price_order_not_distance_order():
    """Highest price first, so the panel reads down the way the price scale does.

    Deliberately not ranked by distance, which is the obvious choice: the rows
    are rebuilt on bar close while their distances are repainted with the tape,
    so a distance-ranked list falls visibly out of order as soon as price moves.
    Prices don't move, so this ordering cannot go stale.
    """
    # Spaced past the collinear tolerance on purpose — inside it they would be
    # one row, which is the other test's subject.
    levels = [
        _lvl("just below", 98.5, _static(98.5)),       #  −6 ticks
        _lvl("far above", 103.0, _static(103.0)),      # +12 ticks
        _lvl("just above", 100.75, _static(100.75)),   #  +3 ticks
    ]
    rows = _rows(levels, _SLIP, price=100.0)
    assert [m["label"] for r in rows for m in r["members"]] == [
        "far above", "just above", "just below",
    ]
    # The sign is what says which side of price a row is on.
    assert rows[0]["dist"] == pytest.approx(12.0)
    assert rows[2]["dist"] == pytest.approx(-6.0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
