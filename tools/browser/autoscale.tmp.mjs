// TEMP — price-scale occupancy after demoting the σ envelopes and the weekly mid.
// Re-runs §3 of docs/research/chart-price-scale-occupancy.md against the same four
// pinned sessions. Delete after use, along with the __occ hook in ReplayChart.
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { SHOTS } from "./lib.mjs";

const SESSIONS = ["2026-08-05", "2026-08-07", "2026-07-29", "2026-08-11"];
const SYMBOL = "NQU6";
const BASE = "http://localhost:5173";

const headed = process.argv.includes("--headed");
const browser = await chromium.launch({ channel: "chrome", headless: !headed });
await mkdir(SHOTS, { recursive: true });

const rows = [];
for (const date of SESSIONS) {
  // A fresh context per session: addInitScript accumulates on a page, so reusing
  // one would have every later day's bookmark overwrite the one under test.
  // DPR 2 to match the study — dev-px/tick is a device-pixel number.
  const ctx = await browser.newContext({
    viewport: { width: 1600, height: 900 },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));

  // Pin the day: the picker otherwise draws at random. Clock at 11:00 in the
  // display zone (the tape's own projection, so UTC arithmetic is ET wall time).
  const [y, m, d] = date.split("-").map(Number);
  const clockMs = Date.UTC(y, m - 1, d, 11, 0, 0);
  await page.addInitScript(
    ([date, symbol, clockMs]) => {
      localStorage.setItem(
        "sim.resume",
        JSON.stringify({ symbol, date, clockMs, attemptId: null, contextTicks: 0 }),
      );
    },
    [date, SYMBOL, clockMs],
  );

  await page.goto(`${BASE}/charts/replay`, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForSelector(".chart-legend", { timeout: 120000 });
  // Wait for the probe to have a reading rather than guessing at a settle time:
  // the tape, the engine and the first paint are three separate arrivals.
  let occ = null;
  for (let i = 0; i < 60; i++) {
    occ = await page.evaluate(() => window.__occ?.() ?? null);
    if (occ && occ.bars > 0) break;
    await page.waitForTimeout(1000);
  }
  const edge = await page.evaluate(() =>
    [...document.querySelectorAll(".chart-edge")].map((e) => ({
      text: e.textContent.trim(),
      title: e.getAttribute("title"),
    })),
  );
  const legend = await page.evaluate(
    () => document.querySelector(".chart-legend")?.textContent?.slice(0, 40) ?? "",
  );
  await page.screenshot({ path: `${SHOTS}/autoscale-${date}.png` });
  await ctx.close();

  if (!occ || !(occ.bars > 0)) {
    rows.push({ date, err: `no reading (legend: ${legend || "-"})`, errors });
    continue;
  }
  // What the scale would have been before the change: the bars plus every level
  // that used to vote and no longer does — the four anchors' σ lines and the
  // weekly mid. MV rings and IB guides were already demoted, so they are out.
  const wasVoting = occ.levels.filter((l) => /^(GX|NY|WK|⚓)/.test(l.label));
  const lo = Math.min(occ.visRange.from, ...wasVoting.map((l) => l.price));
  const hi = Math.max(occ.visRange.to, ...wasVoting.map((l) => l.price));
  rows.push({
    date,
    legend,
    bars: occ.bars,
    scale: occ.scale,
    occ: occ.occupancy,
    dpt: occ.devPxPerTick,
    priorScale: hi - lo,
    priorOcc: occ.bars / (hi - lo),
    priorDpt: (occ.devPxPerTick * occ.scale) / (hi - lo),
    edge,
    errors,
  });
}
await browser.close();

const pad = (v, n) => String(v).padStart(n);
console.log("\n              |------ after the fix ------|  |--------- before ---------|");
console.log("session         bars   scale   occ%  dpx/tk     scale   occ%  dpx/tk");
for (const r of rows) {
  if (r.err) {
    console.log(`${r.date}   ${r.err}`);
    continue;
  }
  console.log(
    `${r.date}  ${pad(r.bars.toFixed(0), 6)}  ${pad(r.scale.toFixed(0), 6)}  ` +
      `${pad((r.occ * 100).toFixed(0), 5)}  ${pad(r.dpt.toFixed(2), 6)}    ` +
      `${pad(r.priorScale.toFixed(0), 6)}  ${pad((r.priorOcc * 100).toFixed(0), 5)}  ${pad(r.priorDpt.toFixed(2), 6)}`,
  );
}
console.log("\nedge markers:");
for (const r of rows) {
  if (r.err) continue;
  console.log(`  ${r.date}: ${r.edge.length ? r.edge.map((e) => `[${e.text}]`).join("  ") : "(none)"}`);
  for (const e of r.edge) console.log(`      ${e.title}`);
}
const errs = rows.flatMap((r) => r.errors ?? []);
if (errs.length) console.log(`\npage errors (${errs.length}):\n  ${errs.slice(0, 8).join("\n  ")}`);
