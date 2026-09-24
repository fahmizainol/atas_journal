// Throwaway: what the CVD oscillator costs a playing replay, per pane.
//
// renderbudget.mjs asks the same question of the page as a whole; this asks it
// of one layer, by measuring the same window twice with only that layer's
// visibility changed. Everything else is identical — same route, same pane
// count, same seed — so the delta is attributable.
//
// Main-thread busy% rather than fps: a page pinned at 60fps may be 10% busy or
// 95% busy, and only the second one drops frames the moment you add a pane.
//
//   APP_URL=http://localhost:8000 node cvdoscbudget.tmp.mjs --panes=2
import { launch, openChart } from "./lib.mjs";

const WINDOW = 8;
const ROUTE = "/charts/replay";
const panes = Number((process.argv.find((a) => a.startsWith("--panes=")) ?? "").split("=")[1]) || 2;

/** `which` names the delta pane to run: none, the cumulative CVD, or the
 *  oscillator. The cumulative one is the control — it costs a sub-pane and a
 *  divergence primitive exactly like the oscillator does, but it writes one
 *  point per frame and recomputes nothing. So `cvd → cvdOsc` isolates the
 *  per-frame *computation* from the price of simply owning another pane. */
const seed = (page, which) =>
  page.evaluate(
    ({ which, panes }) => {
      const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
      vis.cvd = which === "cvd";
      vis.cvdOsc = which === "cvdOsc";
      localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
      const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
      localStorage.setItem("sim.prefs", JSON.stringify({ ...p, panes }));
    },
    { which, panes },
  );

const { browser, page, errors } = await launch({ headed: false });
const cdp = await page.context().newCDPSession(page);
await cdp.send("Performance.enable");

const read = async () => {
  const { metrics } = await cdp.send("Performance.getMetrics");
  const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
  return { ts: m.Timestamp, task: m.TaskDuration, script: m.ScriptDuration };
};

/** Busy% *and* delivered frames over the same window.
 *
 *  Busy% alone goes blind once the main thread saturates: two runs both pinned
 *  at 99.8% can be delivering 60fps and 20fps. Under saturation the number that
 *  still moves is throughput — how many frames actually landed, and how long the
 *  worst one took. */
const measure = async () => {
  const a = await read();
  const frames = await page.evaluate(
    (ms) =>
      new Promise((done) => {
        const gaps = [];
        let last = performance.now();
        const t0 = last;
        const tick = (now) => {
          gaps.push(now - last);
          last = now;
          if (now - t0 >= ms) {
            gaps.sort((x, y) => x - y);
            return done({
              fps: (gaps.length / (now - t0)) * 1000,
              p50: gaps[Math.floor(gaps.length * 0.5)] ?? 0,
              p95: gaps[Math.floor(gaps.length * 0.95)] ?? 0,
            });
          }
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }),
    WINDOW * 1000,
  );
  const b = await read();
  const wall = b.ts - a.ts;
  return {
    busy: ((b.task - a.task) / wall) * 100,
    script: ((b.script - a.script) / wall) * 100,
    ...frames,
  };
};

const run = async (which) => {
  await openChart(page, ROUTE);
  await seed(page, which);
  await openChart(page, ROUTE);
  await page.waitForTimeout(2500);
  await page.keyboard.press("k");
  await page.waitForTimeout(1200);
  const play = await measure();
  await page.keyboard.press("k");
  return play;
};

const fmt = (r) =>
  `${r.fps.toFixed(1).padStart(5)}fps  p95 ${r.p95.toFixed(0).padStart(4)}ms  ${r.busy
    .toFixed(0)
    .padStart(3)}% busy`;

// Interleaved repeats, median reported.
//
// One pass of this measured `none` at 30.6fps and, twenty minutes later, at
// 39.2fps — a 28% swing in the *baseline*, which is larger than the effect being
// looked for. A single sample per condition can only produce a confident wrong
// answer. Interleaving spreads whatever the machine is doing across all three
// conditions instead of donating it to whichever ran first.
const REPS = 3;
const CONDS = ["none", "cvd", "cvdOsc"];
const runs = Object.fromEntries(CONDS.map((c) => [c, []]));
for (let rep = 0; rep < REPS; rep++) {
  for (const which of CONDS) {
    const r = await run(which);
    runs[which].push(r);
    console.log(`  rep ${rep + 1} ${which.padEnd(7)} ${fmt(r)}`);
  }
}

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
console.log(`\n${ROUTE} · ${panes} pane(s) · playing · median of ${REPS}\n`);
const base = median(runs.none.map((r) => r.fps));
for (const which of CONDS) {
  const fps = runs[which].map((r) => r.fps);
  const m = median(fps);
  console.log(
    `${which.padEnd(7)} ${m.toFixed(1).padStart(5)}fps  ` +
      `[${Math.min(...fps).toFixed(1)}–${Math.max(...fps).toFixed(1)}]` +
      (which === "none" ? "  ← baseline" : `   → ${(m - base).toFixed(1)}fps vs none`),
  );
}
if (errors.length) console.log(`\nconsole errors: ${errors.slice(0, 3).join(" | ")}`);
await browser.close();
