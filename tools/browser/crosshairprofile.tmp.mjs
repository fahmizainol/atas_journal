// Where a crosshair move actually spends its time.
//
// crosshaircost.tmp.mjs establishes THAT a pointer move costs something under
// playback, but rAF cadence cannot say what: its p50 quantizes to whole 16.7ms
// frames, so "one frame slower" is the only answer it can give at any load. This
// takes a V8 sampling profile across a sweep and reports self-time by function,
// which names the cost instead of bounding it.
//
// Run it twice — once sweeping, once with the pointer parked — and diff the
// tables. Anything that only appears in the sweeping column is the crosshair's.
//
//   node crosshairprofile.tmp.mjs                sweeping, 4 panes
//   node crosshairprofile.tmp.mjs --idle         pointer parked (control)
//   node crosshairprofile.tmp.mjs --panes=1
//
// Run it against a PRODUCTION bundle or the table is mostly React: `jsxDEV` is
// 38% of self time on the dev server and identical in both columns, so it tells
// you nothing about the crosshair while hiding everything that would.
//
//   pnpm --dir frontend build
//   pnpm --dir frontend exec vite preview --config vite.preview.config.ts
//   APP_URL=http://localhost:4300 node crosshairprofile.tmp.mjs
import { launch, openChart, BASE } from "./lib.mjs";

const IDLE = process.argv.includes("--idle");
const PANES = Number((process.argv.find((a) => a.startsWith("--panes=")) ?? "").split("=")[1]) || 4;
const SECONDS = 10;
const SYMBOL = "NQZ5";
const DAY = "2025-12-17";

const { browser, page } = await launch({ headed: false });

try {
  // Same pin as crosshaircost: a random day would make two runs incomparable.
  await page.goto(`${BASE}/`);
  await page.evaluate(
    ([sym, day, n]) => {
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: 0, contextTicks: 0 }),
      );
      const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
      localStorage.setItem("sim.prefs", JSON.stringify({ ...p, panes: n }));
    },
    [SYMBOL, DAY, PANES],
  );
  await openChart(page, "/charts/replay", { waitUntil: "domcontentloaded" });

  const box = await page.locator("canvas").first().boundingBox();
  const panes = await page.evaluate(() => document.querySelectorAll(".chart-legend").length);
  console.log(`\n${panes} pane(s), ${IDLE ? "pointer parked" : "sweeping"}, ${SECONDS}s\n`);

  await page.mouse.move(5, 5);
  await page.keyboard.press("k");
  await page.waitForTimeout(700);
  for (const key of ["]", "]", "]"]) await page.keyboard.press(key);
  await page.waitForTimeout(500);

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Profiler.enable");
  // 100us: the handler runs per pointer move, so the samples that matter are
  // short and frequent. The default 1ms interval buries them.
  await cdp.send("Profiler.setSamplingInterval", { interval: 100 });
  await cdp.send("Profiler.start");

  const until = Date.now() + SECONDS * 1000;
  if (IDLE) {
    await page.waitForTimeout(SECONDS * 1000);
  } else {
    const y = box.y + box.height / 2;
    const x0 = box.x + box.width * 0.15;
    const x1 = box.x + box.width * 0.85;
    let i = 0;
    while (Date.now() < until) {
      const t = (i % 200) / 200;
      await page.mouse.move(x0 + (x1 - x0) * (t < 0.5 ? t * 2 : 2 - t * 2), y);
      i++;
    }
  }

  const { profile } = await cdp.send("Profiler.stop");
  await page.keyboard.press("k");

  // Self time per node: V8 gives hit counts per node id plus a global time
  // delta list, so ticks x interval is the self time.
  const byId = new Map(profile.nodes.map((n) => [n.id, n]));
  const self = new Map();
  let total = 0;
  for (const n of profile.nodes) {
    const hits = n.hitCount ?? 0;
    if (!hits) continue;
    const f = n.callFrame;
    const where = f.url ? f.url.replace(/^https?:\/\/[^/]+/, "").split("?")[0] : "(native)";
    const key = `${f.functionName || "(anonymous)"}  ${where}:${f.lineNumber + 1}`;
    self.set(key, (self.get(key) ?? 0) + hits);
    total += hits;
  }
  const ms = (h) => (h * 100) / 1000; // 100us per sample

  console.log(`total sampled: ${ms(total).toFixed(0)}ms of ${SECONDS * 1000}ms wall\n`);
  console.log("self time                                                          ms      %");
  for (const [k, h] of [...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, 22)) {
    console.log(`${k.slice(0, 62).padEnd(64)} ${ms(h).toFixed(0).padStart(6)}  ${((100 * h) / total).toFixed(1).padStart(5)}`);
  }
  void byId;
} finally {
  await browser.close();
}
