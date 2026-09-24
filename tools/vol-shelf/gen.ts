/**
 * Generates the volume-shelf parity fixture: what `lib/volumeShelf.ts` makes of
 * a session, so `tests/test_vol_shelf.py` can hold `journal.sim.vol_shelf` to it.
 *
 * The TypeScript is the original — it is what the four charts actually draw — and
 * the Python is a copy that exists only so the level tagger can measure fills
 * against a shelf. So the fixture carries the *input* as well as the output:
 * Python reads the same bars and footprint out of it and must produce the same
 * shelves, rather than both sides generating their own idea of a session and
 * agreeing about nothing.
 *
 * The session is synthetic and deterministic (no Date, no Math.random) and is
 * built to trip the things that actually broke while this was written:
 *
 *   camp vs burst   a price price *sat* at, carrying more raw volume than a
 *                   price a lot traded at quickly. The normalisation has to pick
 *                   the second one; a profile alone picks the first.
 *   edge shelf      a burst at the very top of the session's range, where the
 *                   profile's rows stop. Smoothing runs off the end there and
 *                   an unpadded implementation cannot see it at all.
 *   irregular bars  bars that are NOT one a minute, so a window walked by bar
 *                   count and one walked by timestamp disagree.
 *
 * The duration gate is deliberately NOT exercised from a session here. With a
 * *trailing* window a one-off burst keeps being detected for as long as it stays
 * inside the window, so a synthetic "brief spike" does not express what the gate
 * filters (bands whose score flickers around the threshold as the window slides).
 * `tests/test_vol_shelf.py` drives `ShelfTracker` directly for that instead,
 * which says exactly what is meant.
 *
 * Run it with `tools/vol-shelf/run.sh`. Re-run and commit the fixture whenever
 * the TS changes on purpose — the Python test failing is the intended alarm when
 * it changes by accident.
 */
import {
  detectShelves,
  windowStart,
  evalBars,
  ShelfTracker,
  DEFAULT_SHELF_PARAMS,
  type ShelfBar,
  type ShelfParams,
} from "../../frontend/src/lib/volumeShelf";
import { computeTickProfile } from "../../frontend/src/lib/volumeProfile";

const TICK = 0.25;
const PARAMS: ShelfParams = { ...DEFAULT_SHELF_PARAMS, minHoldMin: 5 };

/** Deterministic LCG — `Math.random` is unavailable to workflow-style scripts
 *  here for the same reason it would be wrong in a fixture: a fixture that
 *  changes every run cannot pin anything. */
let seed = 20260827;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;

/** The fixture's bars carry a close on top of `ShelfBar`: the tracker stopped
 *  reading it when arming moved to full-bar clearance, but the Python port's
 *  `shelf_series` still anchors `nearest()` on each bar's close, and the
 *  fixture is its input too. */
type FixtureBar = ShelfBar & { close: number };

interface Built {
  bars: FixtureBar[];
  footprint: number[][][];
}

/**
 * A session as (bars, per-bar volume-at-price), which is the shape
 * `api.session_chart._footprint` ships.
 *
 * Bars are spaced irregularly — 20s early, 90s in the middle, 15s late — because
 * every chart this layer draws on is tick- or volume-bucketed rather than
 * time-bucketed, and a window walked by bar count would silently pass on evenly
 * spaced bars while being wrong on every real session.
 */
function build(): Built {
  const bars: FixtureBar[] = [];
  const footprint: number[][][] = [];
  let t = 1_700_000_000;

  // Closes at the bar's mid — the one choice that needs no direction argument
  // threaded through `span` and `walk`. Only the Python `shelf_series` reads it
  // (see `FixtureBar`).
  const bar = (lo: number, hi: number, rows: [number, number][], dt: number) => {
    bars.push({ time: t, low: lo, high: hi, close: (lo + hi) / 2 });
    footprint.push(rows.map(([p, s]) => [p, s, 0]));
    t += dt;
  };

  /** One bar covering [lo, hi] with `per` lots at every tick in it. */
  const span = (lo: number, hi: number, per: () => number, dt: number) => {
    const rows: [number, number][] = [];
    for (let p = lo; p <= hi + 1e-9; p += TICK) rows.push([p, per()]);
    bar(lo, hi, rows, dt);
  };

  /** Price walked from `a` to `b` over `n` bars, each covering the stretch it
   *  crossed. Keeps the rows between two regions *occupied*, which is what a
   *  real session does and what a z-score needs: a window whose price jumped
   *  leaves too few occupied rows to score at all (MIN_OCCUPIED_ROWS). */
  const walk = (a: number, b: number, n: number, per: () => number, dt: number) => {
    const stepPx = (b - a) / n;
    let px = a;
    for (let i = 0; i < n; i++) {
      const next = px + stepPx;
      span(Math.min(px, next), Math.max(px, next) + 2 * TICK, per, dt);
      px = next;
    }
    return px;
  };

  const base = () => 30 + Math.floor(rnd() * 12);
  const START = 20000;

  // 1. Drift up and back — unremarkable, and it is what lays down the occupied
  //    rows the later z-scores are taken against.
  walk(START, START + 60 * TICK, 26, base, 20);
  walk(START + 60 * TICK, START + 10 * TICK, 22, base, 20);

  // 2. CAMP: price sits still for a long stretch, accruing a great deal of raw
  //    volume — but at the *same size per visit* as everywhere else. That is the
  //    whole claim: it becomes the session's POC by a mile and must NOT become a
  //    shelf. Giving it fatter prints too would make it a genuine concentration
  //    and the test would pass while proving nothing.
  //    Long enough that its raw volume per row really does beat both bursts —
  //    `test_camp_is_not_a_shelf` checks it is the POC before trusting that its
  //    absence from the shelves means anything.
  const camp = START + 10 * TICK;
  for (let i = 0; i < 120; i++) span(camp, camp + 4 * TICK, base, 90);

  // 3. BURST: far more size at a band, over few visits. Must become a shelf and
  //    must outrank the camp, which carries more raw volume than it does.
  const burst = camp + 34 * TICK;
  walk(camp + 4 * TICK, burst, 8, base, 20);
  for (let i = 0; i < 12; i++) {
    span(burst, burst + 5 * TICK, () => 300 + Math.floor(rnd() * 30), 60);
  }

  // 4. Ordinary two-way drift between the bursts, so the rows between them are
  //    occupied and each window has a population to score against.
  const mid = burst - 20 * TICK;
  walk(burst, mid, 6, base, 20);
  for (let i = 0; i < 14; i++) span(mid, mid + 6 * TICK, base, 15);

  // 5. EDGE: a burst at the very top of the session's range, nothing traded
  //    above it. An unpadded smoother is blind here — its curve rises into the
  //    boundary instead of decaying, and the band never registers.
  //    Same width as the burst on purpose: this case is about the band's
  //    *position*, and a narrower one would fail the width test for a reason
  //    that has nothing to do with being at the edge.
  const top = burst + 26 * TICK;
  walk(mid, top, 10, base, 20);
  for (let i = 0; i < 14; i++) {
    span(top, top + 5 * TICK, () => 280 + Math.floor(rnd() * 25), 45);
  }

  // 6. RETURN: price comes back down to the camp and stays there. The burst
  //    band was already cleared on step 5's climb straight through it — while
  //    the trailing window still *detected* it, which is exactly the branch
  //    worth pinning: departure and return are facts about price, not about
  //    what the window remembers, so the box froze on the crossing bar and
  //    retired mid-detection (its `to` does not outrun its `detectedTo`).
  //    Whatever the later windows re-detect at that band owes the hold gate
  //    from scratch and never earns it here. What this walk itself does is
  //    take price away from the TOP band — arming it — and lay down the rows
  //    the final windows are scored against.
  walk(top, camp, 12, base, 20);

  // 7. And then price stays down here, long enough that the *top* band falls out
  //    of the trailing window too. That leaves the session ending on one shelf
  //    that was cleared and one still standing — the second of which is the
  //    headline behaviour (a box reaching the live edge because price has not
  //    been back), and without this tail the fixture ends with the top band still
  //    live and pins the extension only through the band that was cleared.
  //    Camp-shaped on purpose: base size per visit, so sitting here this long
  //    creates no new shelf of its own.
  for (let i = 0; i < 24; i++) span(camp, camp + 4 * TICK, base, 90);

  return { bars, footprint };
}

const { bars, footprint } = build();

// Per-bar readings, exactly as a host steps them.
// Per-window shelves and the tracked boxes are what BOTH sides compute, so they
// are what the fixture pins. The Python port's `shelf_series` has no counterpart
// here — nothing in the TypeScript builds a per-bar level series, because the
// charts draw boxes and only the level tagger wants a series — so pinning one
// would be inventing a TypeScript answer to a question TypeScript never asks.
// `tests/test_vol_shelf.py` covers it on the Python side.
const tracker = new ShelfTracker(2, PARAMS.minHoldMin * 60);
const perBar: { time: number; from: number; shelves: unknown[] }[] = [];

// Walked at the shared reading cadence (`evalBars`), not per bar, because that
// is what the hosts do — a fixture pinning a per-bar walk would pin behaviour
// nothing in the app performs.
for (const i of evalBars(bars, PARAMS.stepSec)) {
  const j = windowStart(bars, i, PARAMS.windowMin);
  const entries = footprint.slice(j, i + 1).flat();
  const prof = computeTickProfile(entries, TICK);
  const win = bars.slice(j, i + 1);
  const { shelves } = detectShelves(prof, win, true, TICK, PARAMS);
  tracker.push(bars[i].time, shelves, win);
  perBar.push({
    time: bars[i].time,
    from: j,
    shelves: shelves.map((s) => ({
      lo: round(s.lo), hi: round(s.hi), price: round(s.price), z: round(s.z),
    })),
  });
}

/** Six places: the arithmetic is float on both sides and the test compares with
 *  a tolerance, but an un-rounded dump makes a diff unreadable. */
function round(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}

process.stdout.write(
  JSON.stringify(
    {
      tickSize: TICK,
      params: PARAMS,
      bars,
      footprint,
      perBar,
      // The session clears one shelf and leaves one standing, without either
      // being staged for it: price walks from `mid` up to `top` straight through
      // the burst band — clearing it while the window still detects it, so the
      // mid-live freeze is pinned — and never returns to the top band it ends
      // on, so a `to` that outruns `detectedTo` is pinned by the same walk.
      boxes: tracker.boxes().map((b) => ({
        lo: round(b.lo), hi: round(b.hi), from: b.from, to: b.to,
        detectedTo: b.detectedTo, z: round(b.z),
        live: b.live, armed: b.armed, cleared: b.cleared,
      })),
    },
    null,
    2,
  ) + "\n",
);
