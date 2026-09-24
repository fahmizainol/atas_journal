// The Pine adapter, checked where the canvas cannot see it.
//
//   node tools/pine-study/check.mjs
//
// tools/browser/pinecheck.tmp.mjs drives the seam end to end, but two facts it
// cannot reach are exactly the two this file exists for:
//
//   1. the numbers. A transcribed indicator that is quietly wrong is the whole
//      risk of this feature, so the EMA a script produces is diffed against a
//      hand-rolled one on the same closes.
//   2. the marker mapping. `plotshape` has twelve Pine styles and
//      lightweight-charts draws four, so pineStudy collapses them by direction —
//      a deliberate divergence from the community package, which maps them
//      one-to-one and hands the library names like `triangleUp` that it has no
//      case for and silently does not draw. Nothing on a chart distinguishes
//      "no marker" from "a marker that didn't render", which is precisely why
//      this is asserted here and not by counting pixels.
//
// Runs the real `src/studies` through esbuild, so it is the shipped code under
// test rather than a copy of it. esbuild is reached by path into the frontend's
// own node_modules rather than installed beside this file: the check exists to
// compile the app's source, so borrowing the app's compiler is the honest
// dependency, and a tools dir with its own lockfile for one import is not.
import { build } from "../../frontend/node_modules/esbuild/lib/main.js";

const ROOT = new URL("../../frontend/", import.meta.url);

const bundle = await build({
  entryPoints: [new URL("src/studies/index.ts", ROOT).pathname],
  bundle: true,
  format: "esm",
  write: false,
  platform: "neutral",
  absWorkingDir: ROOT.pathname,
});
const { MINE } = await import(
  "data:text/javascript;base64," + Buffer.from(bundle.outputFiles[0].text).toString("base64")
);

const out = [];
const ok = (label, pass) => out.push([label, pass]);

// A ramp with enough turns to force crossings in both directions.
const bars = [];
let p = 100;
for (let i = 0; i < 300; i++) {
  p += Math.sin(i / 9) * 1.4 + (i < 150 ? 0.25 : -0.3);
  bars.push({ time: 1700000000 + i * 60, open: p - 0.3, high: p + 0.6, low: p - 0.6, close: p, volume: 10 });
}

const pair = MINE.find((s) => s.key === "pine:emaPair");
ok(`emaPair is in the registry`, !!pair);
ok(
  `it declared itself (${pair.title} / ${pair.short} / ${pair.overlay ? "overlay" : "pane"} / ${pair.origin})`,
  pair.title === "EMA pair" && pair.short === "EMA×2" && pair.overlay === true && pair.origin === "mine",
);

const ids = pair.mod.inputConfig.map((c) => c.id);
ok(`inputs harvested from the probe run (${ids.join(", ")})`,
   ids.join(",") === "fast_length,slow_length,source");
ok(`two plots, two colours (${pair.mod.plotConfig.map((c) => c.color).join(", ")})`,
   pair.mod.plotConfig.length === 2 &&
   pair.mod.plotConfig[0].color !== pair.mod.plotConfig[1].color);

const inputs = { fast_length: 9, slow_length: 21, source: "close" };
const r = pair.mod.calculate(bars, inputs);

// --- 1. the numbers ---------------------------------------------------------
const closes = bars.map((b) => b.close);
const ema = (n) => {
  const k = 2 / (n + 1);
  let e = closes.slice(0, n).reduce((a, b) => a + b, 0) / n;
  const o = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < n - 1) { o.push(null); continue; }
    if (i > n - 1) e = closes[i] * k + e * (1 - k);
    o.push(e);
  }
  return o;
};
for (const [plot, len] of [["plot0", 9], ["plot1", 21]]) {
  const want = ema(len).filter((v) => v != null);
  const got = r.plots[plot].map((d) => d.value);
  const worst = Math.max(...got.map((v, i) => Math.abs(v - want[i])));
  ok(`${plot} is ta.ema(${len}) to ${worst.toExponential(1)} (${got.length} values)`,
     got.length === want.length && worst < 1e-9);
}

// --- 2. the markers ---------------------------------------------------------
const up = r.markers.filter((m) => m.shape === "arrowUp");
const down = r.markers.filter((m) => m.shape === "arrowDown");
const LWC_SHAPES = new Set(["circle", "square", "arrowUp", "arrowDown"]);
const LWC_POS = new Set(["aboveBar", "belowBar", "inBar"]);
ok(`both crossings marked (${up.length} up, ${down.length} down)`, up.length > 2 && down.length > 2);
ok(`every shape is one lightweight-charts draws`,
   r.markers.every((m) => LWC_SHAPES.has(m.shape)));
ok(`every position is one it accepts`, r.markers.every((m) => LWC_POS.has(m.position)));
ok(`direction survived the collapse (triangleup -> arrowUp, below the bar)`,
   up.every((m) => m.position === "belowBar") && down.every((m) => m.position === "aboveBar"));
ok(`every mark is timestamped and coloured`,
   r.markers.every((m) => Number.isFinite(m.time) && /^#/.test(m.color)));

// --- the script is a pure function of its inputs -----------------------------
const again = pair.mod.calculate(bars, inputs);
ok(`a second run is identical (no state carried between them)`,
   JSON.stringify(again.plots) === JSON.stringify(r.plots) &&
   JSON.stringify(again.markers) === JSON.stringify(r.markers));

const slower = pair.mod.calculate(bars, { ...inputs, fast_length: 50 });
ok(`inputs reach the runtime (9 -> ${r.plots.plot0.length} values, 50 -> ${slower.plots.plot0.length})`,
   slower.plots.plot0.length === r.plots.plot0.length - 41);
const onHigh = pair.mod.calculate(bars, { ...inputs, source: "high" });
ok(`so does the source`, onHigh.plots.plot0.at(-1).value !== r.plots.plot0.at(-1).value);

let failed = 0;
console.log("\npine adapter");
for (const [label, pass] of out) {
  console.log(`  ${pass ? "ok  " : "FAIL"} ${label}`);
  if (!pass) failed++;
}
process.exit(failed ? 1 : 0);
