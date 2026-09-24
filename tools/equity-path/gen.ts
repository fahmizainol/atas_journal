// The sitting's equity path, checked against the real `lib/replaySim.ts`.
//
// Same shape and the same reason as tools/bracket-anchor: bundle through the
// esbuild that already ships inside the frontend's vite, so a piece of browser
// arithmetic can be asserted in node without a test runner to install.
//
// What is worth checking here is not the arithmetic — it is the two properties
// the account's floor rests on, both of which are easy to break by "tidying"
// the code and impossible to notice from the UI:
//
//   1. `peakUsd` is a **fold**, so a rewind lowers it. A ratchet that survived a
//      rewind would raise an account's floor off a trade that never happened.
//   2. It marks on **prints**, not on fills, so an unrealised high that comes
//      back leaves a trace. That is the entire case the figures exist for.

import {
  newSim,
  runSim,
  stepSim,
  type FillCfg,
  type Log,
  type Tape,
} from "../../frontend/src/lib/replaySim";

const CFG: FillCfg = {
  tickSize: 0.25,
  pointValue: 20,
  commission: 0,
  slipTicks: 0,
  queueTicks: 0,
  latencyMs: 0,
};

/** A tape that goes 21000 → 21010 → 21000, one print a second. */
function tape(prices: number[]): Tape {
  const t0 = 1_770_000_000_000;
  return {
    n: prices.length,
    t: Float64Array.from(prices.map((_, i) => t0 + i * 1000)),
    price: Float64Array.from(prices),
    size: Float64Array.from(prices.map(() => 1)),
    side: new Int8Array(prices.length),
    t0,
    end: t0 + prices.length * 1000,
    rth_open_ms: t0,
  } as unknown as Tape;
}

const at = (i: number) => 1_770_000_000_000 + i * 1000;

function buyAt(i: number): Log {
  return {
    orders: [{
      id: 1, ms: at(i), idx: i, side: "long", size: 1, type: "market",
      price: null, stop: null, target: null, edits: [], cancelMs: null,
      micro: false, trail: null,
    }],
    closes: [],
    brackets: [],
  } as unknown as Log;
}

const checks: [string, boolean, string][] = [];
const eq = (name: string, got: unknown, want: unknown) =>
  checks.push([name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`]);

// --- 1. an unrealised high that comes back is on the path -------------------
// Long one contract at 21000, price runs to 21010 (+$200 at $20/pt) and returns.
// Nothing books. `trades` is empty and `net` is zero — and the peak is $200.
{
  const tp = tape([21000, 21005, 21010, 21005, 21000]);
  const st = runSim(tp, buyAt(0), at(4), CFG);
  eq("unrealised high: no trade booked", st.trades.length, 0);
  eq("unrealised high: peak is the excursion", Math.round(st.peakUsd), 200);
  eq("unrealised high: trough stays at zero", Math.round(st.troughUsd), 0);
}

// --- 2. an unrealised low is on it too --------------------------------------
{
  const tp = tape([21000, 20990, 21000]);
  const st = runSim(tp, buyAt(0), at(2), CFG);
  eq("unrealised low: trough is the excursion", Math.round(st.troughUsd), -200);
  eq("unrealised low: peak stays at zero", Math.round(st.peakUsd), 0);
}

// --- 3. the peak is a fold: a shorter clock cannot have seen the later high --
// This is the rewind property. `runSim` from the same log at an earlier clock is
// exactly what a rewind produces, so a peak that survived one would show here.
{
  const tp = tape([21000, 21010, 21000]);
  const full = runSim(tp, buyAt(0), at(2), CFG);
  const early = runSim(tp, buyAt(0), at(0), CFG);
  eq("fold: the full clock saw the high", Math.round(full.peakUsd), 200);
  eq("fold: rewinding before it un-sees it", Math.round(early.peakUsd), 0);
}

// --- 4. stepping incrementally agrees with rebuilding from scratch -----------
// `stepSim` extends the running state and `runSim` rebuilds it; the page uses
// both, on the same log, and a path that differed between them would make the
// floor depend on whether you had scrubbed.
{
  const tp = tape([21000, 21004, 21010, 21002, 21006]);
  const log = buyAt(0);
  const st = newSim();
  for (let i = 0; i < tp.n; i++) stepSim(tp, log, st, i, i + 1, at(i), CFG);
  const whole = runSim(tp, log, at(tp.n - 1), CFG);
  eq("stepped peak === rebuilt peak", Math.round(st.peakUsd), Math.round(whole.peakUsd));
  eq("stepped trough === rebuilt trough", Math.round(st.troughUsd), Math.round(whole.troughUsd));
}

// --- 5. peak >= 0 >= trough, because t=0 is a point on the path --------------
// A sitting that only ever lost has peak 0, not its best moment.
{
  const tp = tape([21000, 20995, 20990]);
  const st = runSim(tp, buyAt(0), at(2), CFG);
  eq("only-down sitting: peak is zero not the best tick", Math.round(st.peakUsd), 0);
  checks.push([
    "invariant peak >= 0 >= trough",
    st.peakUsd >= 0 && 0 >= st.troughUsd,
    `peak ${st.peakUsd} trough ${st.troughUsd}`,
  ]);
}

// --- 6. a flat sitting has a flat path --------------------------------------
{
  const tp = tape([21000, 21010, 20990]);
  const st = runSim(tp, { orders: [], closes: [], brackets: [] } as unknown as Log, at(2), CFG);
  eq("no position: peak flat", st.peakUsd, 0);
  eq("no position: trough flat", st.troughUsd, 0);
}

let bad = 0;
for (const [name, ok, detail] of checks) {
  if (!ok) bad++;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `  — ${detail}`}`);
}
console.log(`\n${checks.length - bad}/${checks.length} passed`);
process.exit(bad ? 1 : 0);
