"""The POC anchor's Python port — and the one check that actually settles it.

``src/journal/sim/modern_vwap.py`` exists so the level tagger can measure fills
against the naked-POC line the charts draw. That sentence contains the whole
risk: two implementations of the same indicator, in two languages, and both of
them produce a plausible-looking VWAP whatever they do. A drift between them has
no symptom — the chart shows one line, the journal reports proximity to another,
and nothing raises.

So the load-bearing test here is parity: the same bars and the same developing
POC through both implementations, values compared, once per re-arm rule. It
transpiles the TypeScript with the frontend's own esbuild and runs it under
node, and skips (rather than passes) when either is absent — a skipped parity
check is a known gap; a green one that never ran is a lie.

The rest are the properties the port has to hold on its own: each re-arm rule
waits for the thing it claims to wait for (the level migrating, or price leaving
and coming back), the accumulator resets where the anchor fired, no lookahead,
and a session with no developing profile gets no line at all rather than a
session VWAP wearing the POC anchor's name.

Run directly:  ``.venv/bin/python tests/test_modern_vwap.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.sim import modern_vwap as mv  # noqa: E402

TICK = 0.25
TS_FILE = ROOT / "frontend/src/lib/modernVwap.ts"
ESBUILD = ROOT / "frontend/node_modules/.bin/esbuild"


def _bars(highs, lows, closes, volumes=None, t0: int = 1_000):
    """Arrays in the shape both implementations take, on a 60-second grid."""
    n = len(highs)
    times = np.arange(t0, t0 + 60 * n, 60, dtype="int64")
    vols = np.ones(n) if volumes is None else np.asarray(volumes, dtype=float)
    return (times, np.asarray(highs, dtype=float), np.asarray(lows, dtype=float),
            np.asarray(closes, dtype=float), vols)


def _poc_rows(times, pocs):
    return [{"time": int(t), "poc": float(p), "vah": float(p) + 1,
             "val": float(p) - 1}
            for t, p in zip(times, pocs) if np.isfinite(p)]


def test_anchor_disarms_and_rearms_only_on_migration():
    """The rule the whole family rests on. A POC that stays put anchors once,
    however often price crosses it; the same POC moved far enough anchors again.

    Both cases are the same tape — only the level moves — because that is the
    distinction ``pocMove`` is making, and a test that varied the price too
    could pass with the price rule wired in by mistake.
    """
    n = 12
    # Price oscillates across 100.0 on every bar.
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    times, h, lo, c, v = _bars(highs, lows, closes)

    stays = mv.poc_anchor_events(times, h, lo, c,
                                 dict.fromkeys(times.tolist(), 100.0),
                                 TICK, "pocMove", rearm_ticks=50)
    assert stays.sum() == 1 and stays[0], (
        "a POC that never migrates must anchor once — crossing it is not news"
    )

    # Same tape, but the POC migrates 50 ticks (12.5 points) at bar 6. Bar 6's
    # range does not span the new level; bar 7's does, once widened.
    moved = dict.fromkeys(times.tolist(), 100.0)
    for t in times[6:]:
        moved[int(t)] = 112.5
    h2 = h.copy()
    h2[7] = 113.0
    fired = mv.poc_anchor_events(times, h2, lo, c, moved, TICK, "pocMove",
                                 rearm_ticks=50)
    assert list(np.flatnonzero(fired)) == [0, 7], (
        "a migrated POC must re-arm, and fire on the first bar to touch it"
    )

    # One tick short of the threshold is not a migration.
    near = dict.fromkeys(times.tolist(), 100.0)
    for t in times[6:]:
        near[int(t)] = 112.25
    assert mv.poc_anchor_events(times, h2, lo, c, near, TICK, "pocMove",
                                rearm_ticks=50).sum() == 1


def test_distance_rearms_on_price_leaving_not_on_the_level_moving():
    """The other rule, and the pair of cases that tells them apart.

    ``distance`` asks price to leave: a POC nailed to one price anchors again
    every time price has been ``rearmTicks`` away and comes back — the revisits
    ``pocMove`` throws out. And it is measured against the *current* POC, so a
    level that migrates onto a standing price re-arms too.
    """
    n = 12
    times = np.arange(1_000, 1_000 + 60 * n, 60, dtype="int64")
    poc = dict.fromkeys(times.tolist(), 100.0)
    # Price sits on the POC, walks 13 points away (52 ticks), and comes back.
    # Narrow bars, so the away leg does not span the level it has left.
    closes = np.array([100.0] * 4 + [113.0] * 3 + [100.0] * 5)
    highs = closes + 0.5
    lows = closes - 0.5

    far = mv.poc_anchor_events(times, highs, lows, closes, poc, TICK, "distance",
                               rearm_ticks=50)
    assert list(np.flatnonzero(far)) == [0, 7], (
        "leaving by more than the threshold and returning is a revisit"
    )
    assert mv.poc_anchor_events(times, highs, lows, closes, poc, TICK, "pocMove",
                                rearm_ticks=50).sum() == 1, (
        "the same tape under pocMove is one anchor — the level never moved"
    )

    # Price never leaves; the POC comes to it. Under `distance` that also re-arms,
    # because the gap is measured bar by bar against wherever the POC now is.
    still = np.array([100.0] * n)
    creep = {int(t): (100.0 if i < 4 else 113.0 if i < 7 else 100.0)
             for i, t in enumerate(times)}
    got = mv.poc_anchor_events(times, still + 0.5, still - 0.5, still, creep,
                               TICK, "distance", rearm_ticks=50)
    assert list(np.flatnonzero(got)) == [0, 7]


def test_the_mode_must_be_named():
    """No default. Each rule is a separate level family, and a caller that forgot
    to say which one it wanted would silently measure fills against the other."""
    times, h, lo, c, v = _bars([101.0], [99.0], [100.0])
    with pytest.raises(ValueError):
        mv.poc_anchor_events(times, h, lo, c, {int(times[0]): 100.0}, TICK, "swing")


def test_missing_poc_bars_are_skipped_not_guessed():
    """Bars before the profile exists can neither touch the level nor migrate
    away from it. They must not carry the last known POC forward."""
    times, h, lo, c, v = _bars([101.0] * 6, [99.0] * 6, [100.0] * 6)
    poc = {int(t): 100.0 for t in times[3:]}
    ev = mv.poc_anchor_events(times, h, lo, c, poc, TICK, "pocMove")
    assert list(np.flatnonzero(ev)) == [0, 3]


def test_accumulator_resets_at_the_anchor():
    """The mid after an anchor is the VWAP of the bars since it, not of the day."""
    times, h, lo, c, v = _bars(
        highs=[10.0, 10.0, 30.0, 30.0],
        lows=[10.0, 10.0, 30.0, 30.0],
        closes=[10.0, 10.0, 30.0, 30.0],
        volumes=[1, 1, 1, 1],
    )
    ev = np.array([True, False, True, False])
    mid = mv.anchored_mid(h, lo, c, v, ev)
    assert mid.tolist() == [10.0, 10.0, 30.0, 30.0]

    no_reset = mv.anchored_mid(h, lo, c, v, np.array([True, False, False, False]))
    assert no_reset[-1] == 20.0, "without the reset it is the session average"


def test_zero_volume_bars_do_not_move_the_average():
    times, h, lo, c, v = _bars([10.0, 99.0], [10.0, 99.0], [10.0, 99.0],
                               volumes=[5, 0])
    mid = mv.anchored_mid(h, lo, c, v, np.array([True, False]))
    assert mid.tolist() == [10.0, 10.0]


@pytest.mark.parametrize("mode", mv.MODES)
def test_no_lookahead(mode):
    """Every value is final when its bar closes. Appending the rest of the
    session must not change a single earlier point — the property that makes the
    line legal to measure a fill against."""
    rng = np.random.default_rng(4)
    n = 200
    walk = 20_000 + np.cumsum(rng.normal(0, 2.0, n))
    times, h, lo, c, v = _bars(walk + 1.0, walk - 1.0, walk,
                               volumes=rng.integers(1, 500, n))
    pocs = np.round((walk + rng.normal(0, 3.0, n)) * 4) / 4
    rows = _poc_rows(times, pocs)

    full = mv.poc_mid_rows(times, h, lo, c, v, rows, TICK, mode)
    cut = 120
    early = mv.poc_mid_rows(times[:cut], h[:cut], lo[:cut], c[:cut], v[:cut],
                            [r for r in rows if int(r["time"]) < int(times[cut])],
                            TICK, mode)
    assert early == [r for r in full if r["time"] < int(times[cut])]


@pytest.mark.parametrize("mode", mv.MODES)
def test_absent_profile_yields_no_line(mode):
    """A session with no developing profile has no POC anchor. The frontend
    degrades to a session anchor and says so in its legend; a *level* may not —
    that line is the session VWAP under a name that claims otherwise."""
    times, h, lo, c, v = _bars([101.0] * 5, [99.0] * 5, [100.0] * 5)
    assert mv.poc_mid_rows(times, h, lo, c, v, [], TICK, mode) == []
    assert mv.poc_mid_rows(times, h, lo, c, v,
                           [{"time": int(times[0]), "poc": float("nan")}],
                           TICK, mode) == []


# --- parity with the indicator the chart actually draws ---------------------

_DRIVER = """
import { computeModernVwap, DEFAULT_MODERN_VWAP } from "./modernVwap.mjs";
import { readFileSync } from "node:fs";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const p = { ...DEFAULT_MODERN_VWAP, anchor: "poc", rearmMode: fx.rearmMode,
            rearmTicks: fx.rearmTicks };
const ctx = { poc: new Map(fx.poc), tickSize: fx.tickSize };
const out = computeModernVwap(fx.bars, 0, p, ctx);
process.stdout.write(JSON.stringify(
  out.points.map((q) => (Number.isFinite(q.mid) ? q.mid : null))));
"""


def _ts_mids(bars: list[dict], poc: list[list], tick: float, rearm: int, mode: str):
    """Run the TypeScript indicator on a fixture and hand back its mid series."""
    node = shutil.which("node")
    if node is None or not ESBUILD.exists():
        pytest.skip("parity needs node and the frontend's esbuild (pnpm install)")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # Bundled, not merely transpiled: the indicator imports the shared VWAP
        # accumulator (frontend/src/lib/vwap) since every cumulative anchored
        # VWAP was unified on tick moments, and a bare `./vwap` specifier would
        # not resolve inside the temp dir. Bundling pulls the real dependency in,
        # so this still compares against the module the chart actually runs.
        subprocess.run(
            [str(ESBUILD), str(TS_FILE), "--bundle", "--format=esm",
             "--platform=node", f"--outfile={d / 'modernVwap.mjs'}"],
            check=True, capture_output=True,
        )
        (d / "run.mjs").write_text(_DRIVER)
        (d / "fx.json").write_text(json.dumps(
            {"bars": bars, "poc": poc, "tickSize": tick, "rearmTicks": rearm,
             "rearmMode": mode}))
        got = subprocess.run([node, str(d / "run.mjs"), str(d / "fx.json")],
                             check=True, capture_output=True, text=True)
    return json.loads(got.stdout)


@pytest.mark.parametrize("mode", mv.MODES)
@pytest.mark.parametrize("tick_moments", [False, True], ids=["hlc3", "ticks"])
def test_matches_the_typescript(mode, tick_moments):
    """The check the module's cross-reference comment is claiming.

    A random walk long enough for the POC to migrate several times, so the
    comparison covers the anchor firing, disarming and re-arming — not just an
    accumulator that happens to agree because it never reset. Run for each
    re-arm rule: they share the accumulator and differ only in the branch that
    decides when to reset it, which is the half a single-mode check would leave
    unguarded.

    Run for each *averaging* too. The two sides accumulate each bar's own tick
    VWAP where the payload carries one (``vwap.bar_moments`` server-side,
    ``lib/vwap.barMoments`` in the chart) and fall back to ``(h+l+c)/3`` where it
    does not. Only checking the fallback would leave the path that actually
    draws on a real session unguarded — which is exactly how the two came to
    disagree once already.
    """
    rng = np.random.default_rng(11)
    n = 600
    walk = 20_000 + np.cumsum(rng.normal(0, 1.5, n))
    highs = np.round((walk + np.abs(rng.normal(0, 4, n))) * 4) / 4
    lows = np.round((walk - np.abs(rng.normal(0, 4, n))) * 4) / 4
    closes = np.round(walk * 4) / 4
    vols = rng.integers(1, 800, n).astype(float)
    times, h, lo, c, v = _bars(highs, lows, closes, vols)
    # A POC that lags the walk, which is what a developing profile does.
    pocs = np.round(pd_rolling_median(walk, 40) * 4) / 4
    rows = _poc_rows(times, pocs)

    # A per-bar tick VWAP that sits *off* hlc3 — the point of the tick path is
    # that it is a different number, so a fixture where the two coincided would
    # pass whichever side was wired up.
    bar_vwap = None
    if tick_moments:
        # Biased, not just noisy: a zero-mean offset averages toward nothing over
        # a long segment, so a side that ignored the moments could still slip
        # under the tolerance far from an anchor. A standing offset cannot.
        bar_vwap = (h + lo + c) / 3.0 + 0.75 + rng.normal(0, 1.0, n)

    ours = {r["time"]: r["value"] for r in mv.poc_mid_rows(
        times, h, lo, c, v, rows, TICK, mode, rearm_ticks=mv.REARM_TICKS,
        bar_vwap=bar_vwap)}

    bars = [{"time": int(t), "open": float(o), "high": float(hi),
             "low": float(low), "close": float(cl), "volume": float(vol),
             **({} if bar_vwap is None
                else {"tvwap": float(bar_vwap[k]), "tvar": 0.0})}
            for k, (t, o, hi, low, cl, vol) in enumerate(
                zip(times, closes, h, lo, c, v))]
    theirs = _ts_mids(bars, [[int(r["time"]), r["poc"]] for r in rows],
                      TICK, mv.REARM_TICKS, mode)

    assert len(theirs) == len(times)
    checked = 0
    for t, m in zip(times, theirs):
        if m is None:
            assert int(t) not in ours
            continue
        assert int(t) in ours, f"no Python value at {t}, TypeScript drew {m}"
        assert abs(ours[int(t)] - m) <= 0.005, (
            f"bar {t}: python {ours[int(t)]} vs typescript {m}"
        )
        checked += 1
    assert checked > n // 2, "the fixture must actually produce a line"


def pd_rolling_median(a: np.ndarray, w: int) -> np.ndarray:
    import pandas as pd
    return pd.Series(a).rolling(w, min_periods=1).median().to_numpy()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
