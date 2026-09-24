/**
 * Generates the order-preset fixture: every bracket across a grid of stops,
 * where the stop comes from, and what the presets' own ruler reads off a tape
 * built to a rule Python can rebuild — so `tests/test_order_presets.py` can
 * re-derive all of them from first principles and hold them still.
 *
 * Why a fixture for what looks like arithmetic. The brackets themselves are five
 * numbers per preset, and getting one wrong is silent: a trail of `sl` where the
 * rule says `sl - 5` is a bracket that still works, still fills, and quietly
 * trades a different shape than the one being practised. The ruler underneath is
 * the part with somewhere to hide — it is incremental, it rewinds on a seek, and
 * it reads a window off a wall clock. An answer that drifts from a straight
 * recompute drifts the stop on every ticket after it.
 *
 * Run it with `tools/order-presets/run.sh`, which bundles this through the
 * esbuild that already ships inside the frontend's vite. Re-run and commit the
 * fixture whenever the rule changes on purpose — the Python test failing is the
 * intended alarm when it changes by accident.
 */
import {
  LEG_ROOM,
  ORDER_PRESETS,
  presetStop,
  stopForReading,
  type PresetId,
} from "../../frontend/src/lib/orderPresets";
import { PresetRuler, PRESET_BUCKETS } from "../../frontend/src/lib/volRuler";

// --- the brackets ------------------------------------------------------------
// Stops worth pinning: a quiet tape, the user's own 35/50 reference points, the
// guards' 60t ceiling, a hot day past it, and the degenerate small ones where
// `sl - 5` and `sl / 2` are the two places a floor has to hold.
const STOPS = [1, 4, 6, 10, 20, 30, 35, 40, 50, 60, 75, 90, 120];

const brackets = [];
for (const stopTicks of STOPS) {
  for (const p of ORDER_PRESETS) {
    // `asked` beside the bracket's own `stopTicks`, not merged into it: the test
    // has to be able to say the preset handed back the stop it was given, and a
    // single field spread over itself cannot fail.
    brackets.push({ asked: stopTicks, id: p.id as PresetId, name: p.name, ...p.bracket(stopTicks) });
  }
}

// --- reading to stop ---------------------------------------------------------
// One reading, rounded, or nothing. The zero and null rows are the ones that
// matter: a ruler that has spoken but said nothing usable must produce no
// preset rather than a 0-tick stop, and there is nothing behind it to fall
// through to any more. The half-tick rows pin the rounding, which is JS's
// half-away-from-zero and not Python's half-to-even.
const READINGS = [41.5, 42.5, 38.4, 0.2, 0.6, 1, 0, -3, null];
const stopReadings = READINGS.map((reading) => ({ reading, stop: presetStop(reading) }));

// --- the ruler ---------------------------------------------------------------
// A tape built from a spec rather than from a recording: the point is that the
// test can rebuild the same prints and measure them its own way. MINSTD, whose
// arithmetic stays exact in a double and reproduces line for line in Python.
const SPEC = {
  seed: 20260820,
  tickSize: 0.25,
  /** Prints a second, and the seconds each day runs — 09:00 to 16:00 ET, which
   *  straddles the ruler's 09:30 window opening on purpose. */
  perSecond: 2,
  startSec: 9 * 3600,
  endSec: 16 * 3600,
  /** Days 0 and 1 are the context drawn to the left; day 2 is the session. */
  days: 3,
  startPrice: 20000,
};

/**
 * How big a step the walk takes, by day and clock — the one part of the tape
 * that is not uniform, and it is deliberate.
 *
 * A tape whose every stretch has the same volatility measures the ruler's
 * arithmetic and nothing else: the median comes out the same number at four
 * bars and at eight hundred, so a ruler that quietly measured the wrong *span*
 * — the context days as well as the session, or a window that never opened —
 * would agree with a correct one and every check would pass. Two scales fix
 * that, and both mirror something real:
 *
 *   - the **context days run 3x**, so a reading that included them is visibly
 *     not a reading of the session;
 *   - the **first ten minutes of RTH run 2x**, because they do
 *     (docs/research/preset-stop-fallback.md measures 1.12x on a volume clock,
 *     and this is a clock bar, where the effect is far larger). It is the whole
 *     reason the window opens at the bell, so the fixture has to contain it or
 *     the choice is untested.
 *
 * Integer scales on a 0.25 tick stay exact in a double, so Python reproduces
 * the walk print for print.
 */
const OPEN_END = 9 * 3600 + 40 * 60;
const stepScale = (day: number, sec: number) =>
  day < SPEC.days - 1 ? 3 : sec >= 9 * 3600 + 30 * 60 && sec < OPEN_END ? 2 : 1;

const secs = SPEC.endSec - SPEC.startSec;
const perDay = secs * SPEC.perSecond;
const n = perDay * SPEC.days;
const t = new Float64Array(n);
const price = new Float64Array(n);
let s = SPEC.seed;
let px = SPEC.startPrice;
for (let i = 0; i < n; i++) {
  const day = Math.floor(i / perDay);
  const k = i % perDay;
  const sec = SPEC.startSec + Math.floor(k / SPEC.perSecond);
  t[i] = (day * 86400 + sec) * 1000;
  s = (s * 48271) % 2147483647;
  px += ((s % 5) - 2) * SPEC.tickSize * stepScale(day, sec);
  price[i] = px;
}
const tape = { t, price };
const sessionStart = perDay * (SPEC.days - 1);

/** A print index for an ET second of the session's day. */
const at = (sec: number) => sessionStart + (sec - SPEC.startSec) * SPEC.perSecond;

/** Playheads worth reading at, and each is a case rather than a sample: inside
 *  the session but before the window opens (nothing to say, and nothing behind
 *  it to say instead), just inside it with one bar closed (still nothing —
 *  MIN_BARS is the whole of the "not yet" answer now), the first playhead that
 *  does read, then the middle of the day and the end. */
const playheads = [
  at(9 * 3600 + 8 * 60), // 09:08 — before the bell
  at(9 * 3600 + 30 * 60 + 45), // 09:30:45 — one closed bar, below MIN_BARS
  at(9 * 3600 + 32 * 60), // 09:32:00 — four closed, so it reads
  at(12 * 3600),
  n - 1,
];

const ruler = new PresetRuler();
const reads = playheads.map((playhead) => ({
  playhead,
  // Every bucketing at once, which is what the ruler hands back — the toggle in
  // the panel picks out of exactly this record.
  reading: ruler.read(tape, sessionStart, playhead, SPEC.tickSize),
}));

/** The same readings taken cold, one ruler per playhead. An incremental answer
 *  that differs from a standing start is the whole failure mode this class can
 *  have, and it would show as a stop that depends on how you got there. */
const cold = playheads.map((playhead) =>
  new PresetRuler().read(tape, sessionStart, playhead, SPEC.tickSize),
);
const same = (a, b) => PRESET_BUCKETS.every((x) => a[x.id] === b[x.id]);
const agrees = (a, i) => same(a, reads[i].reading);

/** And after a seek backwards: the ruler that has already read the whole session
 *  must forget the bars that have not happened again. */
const seeked = playheads.map((playhead) => {
  ruler.read(tape, sessionStart, n - 1, SPEC.tickSize);
  return ruler.read(tape, sessionStart, playhead, SPEC.tickSize);
});

/**
 * The bar in hand must not be in any median. Read at 10:00:00 and again a few
 * seconds later: every bucketing still has the same bar open at both, so no
 * answer may move — a ruler that folded the forming bar in would climb through
 * each bar as it filled, and the stop on the ticket would drift between two
 * glances at the same chart.
 *
 * The span is the **shortest** bar offered, less one print, so the finest
 * bucketing is the one that decides how far this can reach. 10:00:00 is a
 * boundary every clock bar shares, and on this tape (2 prints a second, session
 * open at 09:00) the 500-print bar covering it runs 09:58:20 to 10:02:29 — so
 * that one is mid-bar here too, for its own reason rather than by luck.
 */
const SHORTEST = Math.min(...PRESET_BUCKETS.map((b) => b.seconds ?? Infinity));
const openBar = (() => {
  const first = at(10 * 3600);
  const last = at(10 * 3600 + SHORTEST) - 1;
  const a = new PresetRuler().read(tape, sessionStart, first, SPEC.tickSize);
  const b = new PresetRuler().read(tape, sessionStart, last, SPEC.tickSize);
  return {
    first,
    last,
    a,
    b,
    // Every bucketing has to hold still, not just the selected one — and 10:00
    // is a boundary all three share, so none of them is mid-bar for a different
    // reason than the others.
    stable: same(a, b) && PRESET_BUCKETS.every((x) => a[x.id] !== null),
  };
})();

console.log(
  JSON.stringify(
    {
      buckets: PRESET_BUCKETS.map((b) => ({ id: b.id, label: b.label, prints: b.prints, seconds: b.seconds })),
      winStartSec: 9 * 3600 + 30 * 60,
      openEndSec: OPEN_END,
      legRoom: LEG_ROOM,
      // The stop each reading actually places, beside the reading — the panel
      // shows both and so must the fixture, or the test cannot tell a bracket
      // that forgot the room from one that never had it.
      placed: [1, 20, 45, 50].map((reading) => ({ reading, stopTicks: stopForReading(reading) })),
      presets: ORDER_PRESETS.map((p) => ({ id: p.id, name: p.name, says: p.says })),
      brackets,
      stopReadings,
      spec: { ...SPEC, perDay, n, sessionStart },
      reads,
      openBar,
      coldAgrees: cold.every(agrees),
      rewindAgrees: seeked.every(agrees),
    },
    null,
    2,
  ),
);
