// Where a playing replay's frame time actually goes.
//
// renderbench.mjs says one ReplayChart holds ~24-33fps while playing and spends
// a third of its wall clock in long tasks. This asks *whose* time that is: the
// chart's draw (which a second pane would duplicate) or the page's own React /
// HUD work (which it would not). CPU sampling profile, aggregated by self time.
//
//   node renderprofile.mjs
//
// Needs the dev servers up (`pnpm dev` at the repo root).
import { launch, openChart } from "./lib.mjs";

const { browser, page, errors } = await launch({ headed: false });

try {
  await openChart(page, "/charts/replay");
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Profiler.enable");
  await cdp.send("Profiler.setSamplingInterval", { interval: 100 }); // µs

  await page.keyboard.press("k"); // play
  await page.waitForTimeout(800);
  await cdp.send("Profiler.start");
  await page.waitForTimeout(10000);
  const { profile } = await cdp.send("Profiler.stop");

  // Self time per node, from the sample counts.
  const byId = new Map(profile.nodes.map((n) => [n.id, n]));
  const self = new Map();
  const total = profile.samples.length;
  for (let i = 0; i < profile.samples.length; i++) {
    const id = profile.samples[i];
    self.set(id, (self.get(id) ?? 0) + 1);
  }
  const rows = [];
  for (const [id, count] of self) {
    const n = byId.get(id);
    if (!n) continue;
    const f = n.callFrame;
    const url = (f.url || "").split("/").slice(-1)[0].split("?")[0];
    rows.push({
      name: f.functionName || "(anonymous)",
      where: url ? `${url}:${f.lineNumber + 1}` : "(native)",
      pct: (count / total) * 100,
    });
  }
  rows.sort((a, b) => b.pct - a.pct);

  const dur = (profile.endTime - profile.startTime) / 1e6;
  console.log(`\n${total} samples over ${dur.toFixed(1)}s — self time, top 25\n`);
  for (const r of rows.slice(0, 25)) {
    console.log(`  ${r.pct.toFixed(1).padStart(5)}%  ${r.name.slice(0, 34).padEnd(35)} ${r.where}`);
  }

  // Roll up by file, which is the question actually being asked.
  const byFile = new Map();
  for (const r of rows) {
    const f = r.where.split(":")[0];
    byFile.set(f, (byFile.get(f) ?? 0) + r.pct);
  }
  console.log(`\nby file:\n`);
  for (const [f, pct] of [...byFile].sort((a, b) => b[1] - a[1]).slice(0, 14)) {
    console.log(`  ${pct.toFixed(1).padStart(5)}%  ${f}`);
  }
  if (errors.length) console.log(`\nconsole errors: ${errors.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
}
