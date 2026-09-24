"""The shelf raster's second field: which side built a band.

``lib/volumeShelf.shelfFlow`` is a thin composition — it asks
``deltaFlow.deviationOf`` for every row — so what needs testing is not the
statistic (``tests/test_delta_flow.py`` owns that) but the three things the
composition itself decides, each of which is a way to be confidently wrong:

  the sign     A layer that reads buy-led where the tape sold is worse than no
               layer, because nothing about it looks broken. This repo has had an
               aggressor sign inverted before, and the footprint's delta column
               spent its whole life wrapping every sell through 2^32
               (``test_journal_charts``). So the sign is asserted in both
               directions, off a fixture whose intent is unambiguous.

  no tag       A tape with no aggressor side has no flow. That must arrive as
               *absent*, never as a field of zeros: zero is a real reading here
               (the two sides cancelled exactly) and a grey field would be
               indistinguishable from a balanced market.

  no reading   Same distinction one level down. A row below the relevance floor
               is NaN rather than 0, so the raster paints nothing there instead
               of painting "balanced".

Driven through the real TypeScript with the frontend's own esbuild, the same way
``test_delta_flow.py`` drives the module next door — there is no TS test runner
in this repo, and a Python re-implementation of the thing under test would be
testing the re-implementation.

Run directly:  ``.venv/bin/python -m pytest tests/test_shelf_flow.py -q``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TS_FILE = ROOT / "frontend/src/lib/volumeShelf.ts"
ESBUILD = ROOT / "frontend/node_modules/.bin/esbuild"

# `delta: null` is how a row with no aggressor tag is spelled — JSON has no
# `undefined`, and 0.0 would be the other reading entirely.
_DRIVER = """
import { shelfFlow } from "./volumeShelf.mjs";
import { readFileSync } from "node:fs";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const rows = fx.rows.map(([low, high, volume, delta]) => {
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
const flow = shelfFlow({
  rows,
  maxVolume,
  maxAbsDelta,
  hasDelta: fx.hasDelta,
  poc: 0, vah: 0, val: 0, total: 0,
});
process.stdout.write(JSON.stringify(
  flow === null ? null : Array.from(flow, (v) => (Number.isFinite(v) ? v : null)),
));
"""


def _flow(rows, has_delta=True):
    """Transpile volumeShelf.ts and read one profile through `shelfFlow`."""
    node = shutil.which("node")
    if node is None or not ESBUILD.exists():
        pytest.skip("needs node and the frontend's esbuild (pnpm install)")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        subprocess.run(
            [str(ESBUILD), str(TS_FILE), "--bundle", "--format=esm",
             "--platform=node", f"--outfile={d / 'volumeShelf.mjs'}"],
            check=True, capture_output=True,
        )
        (d / "run.mjs").write_text(_DRIVER)
        (d / "fx.json").write_text(json.dumps({"rows": rows, "hasDelta": has_delta}))
        got = subprocess.run([node, str(d / "run.mjs"), str(d / "fx.json")],
                             check=True, capture_output=True, text=True)
    return json.loads(got.stdout)


def _rows(deltas, volume=400.0):
    """A profile of equal-volume rows differing only in how they leaned, so the
    only thing any assertion below can be reading is the delta."""
    return [[100 + i * 0.25, 100.25 + i * 0.25, volume, d] for i, d in enumerate(deltas)]


def test_a_buy_led_row_reads_positive_and_a_sell_led_row_negative():
    """The assertion the whole layer's honesty rests on."""
    got = _flow(_rows([+200.0, -200.0, 0.0]))
    assert got[0] > 0, "a buy-led row must read positive"
    assert got[1] < 0, "a sell-led row must read negative"
    assert got[0] == pytest.approx(-got[1]), "the two sides must be symmetric"
    assert got[2] == pytest.approx(0.0), "an evenly traded row reads zero, not absent"


def test_the_statistic_is_delta_over_root_volume():
    """Not delta over volume. A ratio lets a four-lot row reach the top of the
    ramp on a coin toss, which paints the thin top and bottom ticks of every
    window; dividing by the square root gives a quantity whose spread is the
    same at any row size."""
    got = _flow(_rows([+200.0], volume=400.0))
    assert got[0] == pytest.approx(200.0 / 20.0)      # not 200/400 = 0.5


def test_size_alone_does_not_move_it():
    """Two rows equally one-sided, one ten times heavier. The heavier one reads
    stronger — it is more evidence of the same lean — but not ten times."""
    rows = [[100.0, 100.25, 100.0, 100.0], [100.25, 100.5, 1000.0, 1000.0]]
    got = _flow(rows)
    assert got[1] > got[0]
    assert got[1] == pytest.approx(got[0] * 10 ** 0.5)


def test_an_untagged_tape_has_no_flow_rather_than_a_flat_one():
    """`null`, not an array of zeros. The raster refuses to draw on this, because
    a grey field and a perfectly balanced market are the same picture."""
    assert _flow(_rows([+200.0, -200.0]), has_delta=False) is None


def test_a_row_below_the_relevance_floor_has_no_reading():
    """Six contracts that landed together are "entirely one-sided" on every
    measure and are still six contracts. `deviationOf`'s floor drops them, and
    the drop must arrive as NaN — the raster paints nothing — rather than as 0,
    which would paint "balanced" over a row nothing is known about.
    """
    rows = [[100.0, 100.25, 5000.0, 100.0], [100.25, 100.5, 4.0, 4.0]]
    got = _flow(rows)
    assert got[0] is not None, "the busy row still reads"
    assert got[1] is None, "a row under 1% of the busiest must not read as balanced"
