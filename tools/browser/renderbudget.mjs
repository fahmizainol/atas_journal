// What fraction of the main thread one playing ReplayChart actually occupies.
//
// fps alone can't answer the multi-pane question: a page pinned at 60fps may be
// 10% busy or 95% busy, and only the second one is out of room. This reads
// Chrome's own TaskDuration counter over a fixed window, so the answer is
// "one pane costs N% of the main thread" — a number you can multiply by a pane
// count before building anything.
//
//   APP_URL=http://localhost:4300 node renderbudget.mjs
//
// Point APP_URL at a *production* build: the dev server's React adds ~40% of
// frame time as jsxDEV/validateProperties that ships to nobody.
import { launch, openChart } from "./lib.mjs";

const WINDOW = 10;
// A route can be passed in, so variants of the same page can be compared:
//   node renderbudget.mjs "/charts/replay?spike=gated"
const ROUTE = process.argv[2] ?? "/charts/replay";
const { browser, page, errors } = await launch({ headed: false });

try {
  // `--panes=N` seeds the page's own preference before measuring, so the split
  // can be compared against a single pane without a debug route existing for it.
  const panes = Number((process.argv.find((a) => a.startsWith("--panes=")) ?? "").split("=")[1]);
  await openChart(page, ROUTE);
  if (panes >= 1) {
    await page.evaluate((n) => {
      const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
      localStorage.setItem("sim.prefs", JSON.stringify({ ...p, panes: n }));
    }, panes);
    await openChart(page, ROUTE);
  }
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");

  const read = async () => {
    const { metrics } = await cdp.send("Performance.getMetrics");
    const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
    return { ts: m.Timestamp, task: m.TaskDuration, script: m.ScriptDuration, layout: m.LayoutDuration, recalc: m.RecalcStyleDuration };
  };

  const measure = async (label) => {
    const a = await read();
    await page.waitForTimeout(WINDOW * 1000);
    const b = await read();
    const wall = b.ts - a.ts;
    const busy = b.task - a.task;
    console.log(
      `${label.padEnd(18)} main thread ${((busy / wall) * 100).toFixed(1).padStart(5)}% busy` +
        `   (script ${(((b.script - a.script) / wall) * 100).toFixed(1)}%,` +
        ` layout ${(((b.layout - a.layout) / wall) * 100).toFixed(1)}%,` +
        ` style ${(((b.recalc - a.recalc) / wall) * 100).toFixed(1)}%)` +
        `   → ${(wall / busy).toFixed(1)} panes to saturate`,
    );
  };

  console.log(`\n${ROUTE}${process.argv.find((a) => a.startsWith("--panes=")) ?? ""}`);
  await measure("idle (paused)");
  await page.keyboard.press("k");
  await page.waitForTimeout(800);
  await measure("playing 1x");
  for (let i = 0; i < 3; i++) await page.keyboard.press("]");
  await page.waitForTimeout(500);
  await measure("playing fast");
  if (errors.length) console.log(`\nconsole errors: ${errors.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
}
