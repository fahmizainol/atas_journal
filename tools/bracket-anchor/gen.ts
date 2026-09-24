/**
 * Generates the bracket-anchor fixture: where a position's stop and target end
 * up, for each of the three kinds of order that can open one.
 *
 * THE QUESTION IT PINS. The ticket promises a distance — "risk 50 ticks" — but
 * `placeOrder` used to freeze the bracket as *prices* struck from the mark at
 * the gesture. The fill lands `latencyMs` later having paid the spread, so the
 * position came out with a stop of 54 and a target of 46 on a fill four ticks
 * the wrong way. Measured over the recorded sittings the fill was not the
 * clicked print on 90% of orders (`data/research/preset-stop/leg_drift.py`).
 *
 * A market order now carries `stopTicks`/`targetTicks` and the distance wins.
 * Three cases have to stay true at once, and only a fixture can hold all three:
 *
 *   `market_ticks`  the new shape — the legs sit exactly the ticket's distance
 *                   from the **fill**, however far the fill drifted;
 *   `market_legacy` an order logged before the distances existed — no ticks, so
 *                   the stored prices still decide, exactly as they always did.
 *                   136 recorded sittings depend on this;
 *   `resting`       a limit order — the bracket belongs to the level it was
 *                   drawn against and does *not* follow the fill.
 *
 * The tape is built to a rule Python can rebuild, so the test re-derives every
 * number rather than reading them back.
 *
 * Run it with `tools/bracket-anchor/run.sh`.
 */
import { DEFAULT_FILL_MODEL } from "../../frontend/src/lib/fillModel";
import { newLog, runSim, type Log, type OrderRec } from "../../frontend/src/lib/replaySim";
import type { Tape } from "../../frontend/src/lib/replayEngine";

const TICK = 0.25;
const POINT = 20;

/** A tape that *moves* — the point of the exercise is a fill that is not the
 *  print the ticket was written against. One print every 50ms, walking up a tick
 *  at a time, so the 250ms of gesture latency is worth exactly five ticks and
 *  the drift is a number the test can state rather than discover. */
const SPEC = { n: 400, stepMs: 50, startMs: 1_760_000_000_000, startPx: 20_000, up: 1 };

const t = new Float64Array(SPEC.n);
const price = new Float64Array(SPEC.n);
const level = new Int32Array(SPEC.n);
const size = new Int32Array(SPEC.n);
const side = new Uint8Array(SPEC.n);
for (let i = 0; i < SPEC.n; i++) {
  t[i] = SPEC.startMs + i * SPEC.stepMs;
  price[i] = SPEC.startPx + i * SPEC.up * TICK;
  level[i] = Math.round(price[i] / TICK);
  size[i] = 1;
  side[i] = 2;
}
const tape: Tape = { n: SPEC.n, t, price, level, size, side, tickSize: TICK, pointValue: POINT };

const cfg = { ...DEFAULT_FILL_MODEL, tickSize: TICK, pointValue: POINT };

/** The gesture: placed at print 100, on a rising tape, long. */
const AT_IDX = 100;
const AT = SPEC.startPx + AT_IDX * TICK;
const MS = SPEC.startMs + AT_IDX * SPEC.stepMs;
const STOP_T = 40;
const TGT_T = 60;

const base: OrderRec = {
  id: 1,
  type: "market",
  side: "long",
  size: 1,
  ms: MS,
  idx: AT_IDX,
  price: null,
  // Struck from the mark, the way the ticket has always written them.
  stop: AT - STOP_T * TICK,
  target: AT + TGT_T * TICK,
  trail: null,
  edits: [],
  cancelMs: null,
};

const cases: Record<string, OrderRec> = {
  // The new shape: the same order, carrying what the ticket actually said.
  market_ticks: { ...base, stopTicks: STOP_T, targetTicks: TGT_T },
  // The old one, byte for byte what a stored log holds.
  market_legacy: { ...base },
  // A ticket with the stop leg switched off. Zero is a real value on both legs
  // and it is *not* the same as absent: the record still carries the pair, so
  // the distances still decide — they just decide "no stop". A `0` read as
  // "nothing was said here" would fall back to the price struck from the mark
  // and hang a stop on a trade that asked for none.
  market_no_stop: { ...base, stop: null, stopTicks: 0, targetTicks: TGT_T },
  // A limit resting where this tape will actually reach it: a sell 10 ticks
  // above a rising market, with its bracket drawn off *its* level rather than
  // off the mark. It carries no distances — that is the point of the case.
  resting: {
    ...base,
    type: "limit",
    side: "short",
    price: AT + 10 * TICK,
    stop: AT + 10 * TICK + STOP_T * TICK,
    target: AT + 10 * TICK - TGT_T * TICK,
  },
  // The resting case that actually *discriminates*. A limit fills at its own
  // price, so level-anchoring and fill-anchoring agree there and the case above
  // cannot tell them apart. A stop order triggers at its level and fills at the
  // print that crossed it plus the spread — so its fill is not its level, and
  // the bracket staying on the level is visible.
  resting_stop: {
    ...base,
    type: "stop",
    price: AT + 10 * TICK,
    stop: AT + 10 * TICK - STOP_T * TICK,
    target: AT + 10 * TICK + TGT_T * TICK,
  },
};

/** Read the position while it is still *open*: the stop and the target are what
 *  is being pinned, and a rising tape would take this long through its target
 *  within three seconds and leave nothing to look at. The clock stops 400ms
 *  after the gesture — long enough for the 250ms of latency, far short of the
 *  60 ticks the target is away. */
const CLOCK = MS + 1_200;

const out: Record<string, unknown> = {};
for (const [name, o] of Object.entries(cases)) {
  const log: Log = { ...newLog(), orders: [o] };
  const st = runSim(tape, log, CLOCK, cfg);
  const p = st.open;
  out[name] = {
    /** The print the ticket was written against, and the one that filled it. */
    at: AT,
    entry: p?.entryPrice ?? null,
    stop: p?.stop ?? null,
    target: p?.target ?? null,
    /** Both legs as the *position* holds them: distances from its own fill, in
     *  ticks. This is the pair the chips quote and the ticket makes a promise
     *  about. */
    riskTicks: p?.riskPts != null ? p.riskPts / TICK : null,
    rewardTicks: p?.target != null && p ? Math.abs(p.target - p.entryPrice) / TICK : null,
  };
}

console.log(
  JSON.stringify(
    { spec: SPEC, tick: TICK, point: POINT, stopTicks: STOP_T, targetTicks: TGT_T,
      latencyMs: cfg.latencyMs, slipTicks: cfg.slipTicks, cases: out },
    null,
    2,
  ),
);
