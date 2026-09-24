// The per-anchor "Bands" knob on the three fixed VWAP anchors.
//
// What this asks, on the Journal's day chart (CandlestickChart — the same knob
// ReplayChart carries, on the safer of the two surfaces to script):
//
//   1. the NY row opens with the envelope it has always had, "±1σ ±2σ";
//   2. setting the knob to "±2σ only" changes the row's own label — a legend that
//      still claims two rings over a chart drawing one is the failure worth
//      catching, since the label is the only DOM evidence a canvas layer leaves;
//   3. the canvas actually loses ink. Two dashed lines and the wash between them
//      coming off the pane has to show up as off-background pixels going away;
//   4. "none" takes the rest of the envelope and leaves the mid;
//   5. the choice survives a reload — it is a sticky global (chart.vwapBands).
//
// Run: node tools/browser/vwapbandcheck.tmp.mjs [--headed]
import { launch, openChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const DAY = process.env.JOURNAL_DAY ?? "2026-06-30";
const MODES = process.env.JOURNAL_MODES ?? "replay";

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

async function openLegend(page) {
  const head = page.locator(".chart-legend-head").first();
  if ((await head.getAttribute("aria-expanded")) !== "true") await head.click();
  await page.waitForTimeout(150);
}

const rowLabel = (page, key) =>
  page.locator(`[data-ind-item="${key}"] .chart-legend-row span:nth-child(2)`).first().innerText();

/** Share of the price canvas that is not its own modal colour. The cheap way to
 *  ask "did lines come off the pane" without pinning exact pixels. */
const inkShare = (page) =>
  page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const tally = new Map();
    for (let i = 0; i < d.length; i += 4) {
      const k = `${d[i]},${d[i + 1]},${d[i + 2]}`;
      tally.set(k, (tally.get(k) ?? 0) + 1);
    }
    const bg = [...tally.entries()].sort((p, q) => q[1] - p[1])[0][0].split(",").map(Number);
    let off = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (
        Math.abs(d[i] - bg[0]) >= 10 ||
        Math.abs(d[i + 1] - bg[1]) >= 10 ||
        Math.abs(d[i + 2] - bg[2]) >= 10
      )
        off++;
    }
    return off / (d.length / 4);
  });

/** Set one anchor's Bands knob. Leaves the panel open. */
async function setBands(page, key, value) {
  // The dots button toggles, so a second click on an open panel would shut it.
  const dots = page.locator(`[data-ind-item="${key}"] .chart-legend-dots`).first();
  if ((await dots.getAttribute("aria-expanded")) !== "true") await dots.click();
  await page.waitForTimeout(120);
  const sel = page.locator(`[data-ind-item="${key}"] .chart-set select`).first();
  await sel.selectOption(value);
  await page.waitForTimeout(350);
}

const { browser, page } = await launch({ headed });
try {
  await openChart(page, `/calendar/${DAY}?mode=${MODES}`);
  // A known starting point: this is a sticky global, so a previous run's choice
  // would otherwise decide what "the default" looks like here.
  await page.evaluate(() => localStorage.removeItem("chart.vwapBands"));
  await page.reload({ waitUntil: "networkidle" });
  await openChart(page, `/calendar/${DAY}?mode=${MODES}`);
  await openLegend(page);

  const before = await rowLabel(page, "vwapNy");
  ok("NY row opens with both rings", /±1σ\s*±2σ/.test(before), before);
  const inkBoth = await inkShare(page);

  await setBands(page, "vwapNy", "s2");
  const s2 = await rowLabel(page, "vwapNy");
  ok("label drops ±1σ", s2.includes("±2σ") && !s2.includes("±1σ"), s2);
  const inkS2 = await inkShare(page);
  ok("canvas lost ink with ±1σ and the wash gone", inkS2 < inkBoth, `${inkBoth.toFixed(4)} → ${inkS2.toFixed(4)}`);

  await setBands(page, "vwapNy", "none");
  const none = await rowLabel(page, "vwapNy");
  ok("label names no rings", !none.includes("σ"), none);
  const inkNone = await inkShare(page);
  ok("canvas lost more ink with the envelope gone", inkNone < inkS2, `${inkS2.toFixed(4)} → ${inkNone.toFixed(4)}`);

  // The other two anchors are untouched — the knob is per anchor.
  const gx = await rowLabel(page, "vwapGlobex");
  ok("Globex keeps its own envelope", /±1σ\s*±2σ/.test(gx), gx);

  await shot(page, "vwapband-none");

  await page.reload({ waitUntil: "networkidle" });
  await openChart(page, `/calendar/${DAY}?mode=${MODES}`);
  await openLegend(page);
  const after = await rowLabel(page, "vwapNy");
  ok("choice survives a reload", !after.includes("σ"), after);
  const stored = await page.evaluate(() => localStorage.getItem("chart.vwapBands"));
  ok("stored per anchor", /"ny":"none"/.test(stored ?? ""), stored ?? "(none)");

  // The other chart. ReplayChart carries the same knob through its own legend and
  // its own σ-line bookkeeping (BAND_KEYS / RING_OF), so passing on the Lab's
  // chart says nothing about it. The day replayer is the cheap way in — same
  // component as /charts, on a day this machine has a tape for.
  await page.evaluate(() => localStorage.removeItem("chart.vwapBands"));
  await page.reload({ waitUntil: "networkidle" });
  const toggle = page.locator("button", { hasText: "Show replay" }).first();
  await toggle.waitFor({ timeout: 30000 });
  await toggle.click();
  await page.waitForSelector("[data-day-replay]", { timeout: 30000 });
  await page.waitForTimeout(1500);
  const rep = page.locator("[data-day-replay]");
  const repHead = rep.locator(".chart-legend-head").first();
  if ((await repHead.getAttribute("aria-expanded")) !== "true") await repHead.click();
  await page.waitForTimeout(200);
  const repRow = () =>
    rep.locator(`[data-ind-item="vwapNy"] .chart-legend-row span:nth-child(2)`).first().innerText();
  const repBefore = await repRow();
  ok("replay NY row opens with both rings", /±1σ\s*±2σ/.test(repBefore), repBefore);
  const repDots = rep.locator(`[data-ind-item="vwapNy"] .chart-legend-dots`).first();
  if ((await repDots.getAttribute("aria-expanded")) !== "true") await repDots.click();
  await page.waitForTimeout(150);
  await rep.locator(`[data-ind-item="vwapNy"] .chart-set select`).first().selectOption("s1");
  await page.waitForTimeout(400);
  const repAfter = await repRow();
  ok("replay label drops ±2σ", repAfter.includes("±1σ") && !repAfter.includes("±2σ"), repAfter);
  await shot(page, "vwapband-replay-s1");
} catch (e) {
  ok("ran without throwing", false, String(e));
  await shot(page, "vwapband-threw");
} finally {
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} failed: ${fails.join(", ")}` : "\nall ok");
process.exit(fails.length ? 1 : 0);
