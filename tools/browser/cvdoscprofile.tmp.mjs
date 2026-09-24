// Throwaway: which function inside the CVD oscillator is spending the frame.
//
// cvdoscbudget.tmp.mjs says the layer costs ~29% of the frame rate at 2 panes.
// This says where: a CPU profile over a playing replay, aggregated by self time
// per function, filtered to the frames whose names mention the layer.
//
// Runs against the dev server on purpose — the production build is minified, so
// the answer there is a list of one-letter names. React's dev overhead inflates
// the absolute numbers; the *ranking* among our own functions is what is read.
//
//   node cvdoscprofile.tmp.mjs
import { launch, openChart } from "./lib.mjs";

const ROUTE = "/charts/replay";
const { browser, page, errors } = await launch({ headed: false });

await openChart(page, ROUTE, { timeout: 180000 });
await page.evaluate(() => {
  const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
  vis.cvdOsc = true;
  localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
  const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
  localStorage.setItem("sim.prefs", JSON.stringify({ ...p, panes: 2 }));
});
await openChart(page, ROUTE, { timeout: 180000 });
await page.waitForTimeout(2500);

const cdp = await page.context().newCDPSession(page);
await cdp.send("Profiler.enable");
await cdp.send("Profiler.setSamplingInterval", { interval: 100 });
await page.keyboard.press("k");
await page.waitForTimeout(1500);
await cdp.send("Profiler.start");
await page.waitForTimeout(10000);
const { profile } = await cdp.send("Profiler.stop");
await page.keyboard.press("k");

// Self time per node, from the sample counts the profiler already took.
const byId = new Map(profile.nodes.map((n) => [n.id, n]));
const self = new Map();
for (const id of profile.samples ?? []) self.set(id, (self.get(id) ?? 0) + 1);
const total = (profile.samples ?? []).length || 1;

const rows = [];
for (const [id, n] of byId) {
  const hits = self.get(id) ?? 0;
  if (!hits) continue;
  const f = n.callFrame;
  const where = (f.url || "").split("/").pop()?.split("?")[0] ?? "";
  rows.push({ name: f.functionName || "(anonymous)", where, pct: (hits / total) * 100 });
}
rows.sort((a, b) => b.pct - a.pct);

console.log(`\ntop self time, playing, 2 panes, oscillator on\n`);
for (const r of rows.slice(0, 18))
  console.log(`${r.pct.toFixed(1).padStart(5)}%  ${r.name.padEnd(34)} ${r.where}`);

const mine = rows.filter((r) => /cvdOsc|CvdDiv|Cvd/i.test(r.name + r.where));
console.log(`\nthe layer's own frames — ${mine.reduce((s, r) => s + r.pct, 0).toFixed(1)}% total\n`);
for (const r of mine.slice(0, 14))
  console.log(`${r.pct.toFixed(1).padStart(5)}%  ${r.name.padEnd(34)} ${r.where}`);
if (errors.length) console.log(`\nconsole errors: ${errors.slice(0, 3).join(" | ")}`);
await browser.close();
