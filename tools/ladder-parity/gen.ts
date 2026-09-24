/**
 * Generates the ladder parity fixture: what the *real* replay engine does with a
 * trail, tick by tick, so `tests/test_live_ladder.py` can hold the Python port
 * to it.
 *
 * This drives `runSim` — the same public entry the Simulator page calls — rather
 * than reaching into the module for `trailStop`. Testing the private function
 * would prove the arithmetic matches while leaving the thing that actually
 * decides a live stop (when the ladder runs, what it runs after, what a drag
 * does to it) unchecked. The ordering is half the rule: the bracket is read
 * before the high-water mark moves, so a print can never both set a new best and
 * ratchet a stop into its own path on the same tick.
 *
 * Run it with `tools/ladder-parity/run.sh`, which bundles this through the
 * esbuild that already ships inside the frontend's vite. Re-run and commit the
 * fixture whenever the ladder in `replaySim.ts` changes on purpose — the Python
 * test failing is the intended alarm when it changes by accident.
 */
import {
  newLog,
  runSim,
  type Log,
  type OrderRec,
  type Side,
  type SimState,
} from "../../frontend/src/lib/replaySim";
import type { FillCfg } from "../../frontend/src/lib/fillModel";
import type { Tape } from "../../frontend/src/lib/replayEngine";

const TICK = 0.25;
const POINT = 20;

/** Costless and instant. The ladder is geometry; charging it a spread and a
 *  quarter-second of lag would make the fixture a test of the fill model too,
 *  and a failure would not say which half moved. */
const CFG: FillCfg = {
  commission: 0,
  slipTicks: 0,
  queueTicks: 0,
  latencyMs: 0,
  pointValue: POINT,
  tickSize: TICK,
};

/** A tape from a list of prices, one print a second. */
function tapeOf(prices: number[]): Tape {
  const n = prices.length;
  const t = new Float64Array(n);
  const price = new Float64Array(n);
  const level = new Int32Array(n);
  const size = new Int32Array(n);
  const side = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    t[i] = i * 1000;
    price[i] = prices[i];
    level[i] = Math.round(prices[i] / TICK);
    size[i] = 1;
    side[i] = 0;
  }
  return { n, t, price, level, size, side, tickSize: TICK, pointValue: POINT };
}

interface Scenario {
  name: string;
  side: Side;
  /** In ticks, as the ticket carries them. */
  dist: number;
  step: number;
  be: number;
  beOnly: boolean;
  prices: number[];
  /** A drag of the position's stop, in prices, at a tape index. */
  drag?: { at: number; stop: number };
}

/** A ramp away from `from`, `n` prints of `perTick` ticks each. */
function ramp(from: number, n: number, perTick: number): number[] {
  const out: number[] = [];
  for (let i = 0; i <= n; i++) out.push(from + i * perTick * TICK);
  return out;
}

const SCENARIOS: Scenario[] = [
  {
    // The worked example in both docstrings: stop on 21001 once the trade has
    // printed 21010, on 21006 at 21015.
    name: "long-40-20-4",
    side: "long",
    dist: 40,
    step: 20,
    be: 4,
    beOnly: false,
    prices: ramp(21000, 60, 2),
  },
  {
    // The same geometry mirrored. A sign convention that is only ever exercised
    // on one side is a sign convention that is eventually wrong on the other.
    name: "short-40-20-4",
    side: "short",
    dist: 40,
    step: 20,
    be: 4,
    beOnly: false,
    prices: ramp(21000, 60, -2),
  },
  {
    // step = 0 means one rung per `dist` — a coarse ladder, not a smooth one.
    name: "long-40-0-0",
    side: "long",
    dist: 40,
    step: 0,
    be: 0,
    beOnly: false,
    prices: ramp(21000, 80, 2),
  },
  {
    // Takes the first rung and no other. The stop must stay put however far the
    // trade runs, which is the whole difference from a trail.
    name: "long-be-only",
    side: "long",
    dist: 40,
    step: 20,
    be: 4,
    beOnly: true,
    prices: ramp(21000, 80, 2),
  },
  {
    // Up, back, and up again. The ladder must not give a rung back on the
    // pullback, and must resume from where it got to rather than from the entry.
    name: "long-pullback",
    side: "long",
    dist: 40,
    step: 20,
    be: 4,
    beOnly: false,
    prices: [
      ...ramp(21000, 30, 2),
      ...ramp(21015, 20, -1).slice(1),
      ...ramp(21010, 40, 2).slice(1),
    ],
  },
  {
    // A drag mid-ladder, tighter than the rung it was on. It has to hold: the
    // grid re-pins on the dragged level and the high-water mark walks back to
    // the last high that level is consistent with.
    name: "long-drag-tighter",
    side: "long",
    dist: 40,
    step: 20,
    be: 4,
    beOnly: false,
    prices: ramp(21000, 80, 2),
    drag: { at: 40, stop: 21012 },
  },
  {
    // And a drag *looser*. The drag still wins — the ladder is a tool for
    // managing the stop, not a lock on it — and the ladder resumes only on a
    // high that beats what the new level justifies.
    name: "long-drag-looser",
    side: "long",
    dist: 40,
    step: 20,
    be: 4,
    beOnly: false,
    prices: ramp(21000, 80, 2),
    drag: { at: 40, stop: 21002 },
  },
];

function logFor(s: Scenario, entry: number): Log {
  const log = newLog();
  const order: OrderRec = {
    id: 1,
    type: "market",
    side: s.side,
    size: 1,
    ms: 0,
    idx: 0,
    price: null,
    // Far enough away that it is never hit: this fixture is about where the
    // ladder puts the stop, not about the exit.
    stop: s.side === "long" ? entry - 500 : entry + 500,
    target: null,
    trail: {
      dist: s.dist * TICK,
      step: s.step * TICK,
      be: s.be * TICK,
      beOnly: s.beOnly,
    },
    edits: [],
  };
  log.orders.push(order);
  if (s.drag) log.brackets.push({ ms: s.drag.at * 1000, stop: s.drag.stop, target: null });
  return log;
}

const out = SCENARIOS.map((s) => {
  const tape = tapeOf(s.prices);
  const entry = s.prices[0];
  const log = logFor(s, entry);
  // The stop after every print, read by re-running to each clock. O(n²) and
  // entirely fine at this size — what it buys is that every sample comes out of
  // the same public entry the page uses, with no incremental path to disagree
  // with it.
  const stops: (number | null)[] = [];
  const hwms: (number | null)[] = [];
  for (let i = 0; i < tape.n; i++) {
    const st: SimState = runSim(tape, log, tape.t[i], CFG);
    stops.push(st.open ? st.open.stop : null);
    hwms.push(st.open ? st.open.hwm : null);
  }
  return {
    name: s.name,
    side: s.side,
    tickSize: TICK,
    dist: s.dist,
    step: s.step,
    be: s.be,
    beOnly: s.beOnly,
    entry,
    prices: s.prices,
    drag: s.drag ?? null,
    stops,
    hwms,
  };
});

process.stdout.write(JSON.stringify({ scenarios: out }, null, 2) + "\n");
