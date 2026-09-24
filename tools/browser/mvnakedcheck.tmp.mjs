// Throwaway: the POC anchor's second re-arm rule ("the POC moves", the naked
// one) on both charts that draw the layer.
//
// The legend row quotes the anchor count, which is the whole observable: the new
// rule should anchor far less than the published distance one on the same tape,
// and "any move" should sit above "25 ticks" because in balance the developing
// POC wobbles a tick at a time. The synthetic proof of the semantics is a
// scratchpad probe; this is the same claim against real NQ.
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

const seed = async (page, mv, { lab = false } = {}) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ({ mv, lab }) => {
      const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
      vis.modernVwap = true;
      vis.modernVwapSignals = false;
      localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
      const base = { anchor: "poc", anchorMarks: true, bands: 2, ...mv };
      if (lab) {
        localStorage.setItem("chart.modernVwap", JSON.stringify(base));
      } else {
        const prefs = JSON.parse(localStorage.getItem("sim.prefs") || "{}");
        prefs.modernVwap = { ...(prefs.modernVwap || {}), ...base };
        localStorage.setItem("sim.prefs", JSON.stringify(prefs));
      }
    },
    { mv, lab },
  );
};

const mvRow = async (page) =>
  (await page.locator(".chart-legend >> text=/Modern VWAP · /").first().textContent())?.trim();
const anchorsOf = (s) => {
  const m = (s ?? "").match(/(\d+)⚓/);
  return m ? +m[1] : null;
};

const openLab = async (page) => {
  await page.goto(`${BASE}/interactions`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("tbody tr", { timeout: 60000 });
  await page.locator("tbody tr").first().click();
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(3000);
};

const { browser, page, errors } = await launch();
const report = { replay: {}, lab: {} };

// --- the replay ---------------------------------------------------------------
for (const [key, mv] of [
  ["distance25", { rearmMode: "distance", rearmTicks: 25 }],
  ["naked25", { rearmMode: "pocMove", rearmTicks: 25 }],
  ["nakedAny", { rearmMode: "pocMove", rearmTicks: 0 }],
]) {
  await seed(page, mv);
  await openChart(page, REPLAY);
  await page.waitForTimeout(1200);
  const row = await mvRow(page);
  report.replay[key] = { row, anchors: anchorsOf(row) };
  await shot(page, `mvnaked-replay-${key}`);
}
const r = report.replay;
report.replayOrdering =
  r.naked25.anchors != null &&
  r.naked25.anchors <= r.distance25.anchors &&
  r.naked25.anchors <= r.nakedAny.anchors;
report.saysNaked = (r.naked25.row ?? "").includes("naked POC");

// --- the Lab ------------------------------------------------------------------
await seed(page, { rearmMode: "distance", rearmTicks: 25 }, { lab: true });
await openLab(page);
report.lab.distance25 = await mvRow(page);
const item = page.locator('[data-ind-item="modernVwap"]');
await item.locator(".chart-legend-dots").click();
await page.waitForTimeout(200);
// Second select in the panel: anchor, then POC source, then the re-arm mode.
const modeSel = item.locator("select").nth(2);
report.lab.modeOptions = await modeSel.locator("option").evaluateAll((o) => o.map((x) => x.value));
report.lab.ticksLabelBefore = await item.locator("label, .ind-field").allTextContents();
await modeSel.selectOption("pocMove");
await page.waitForTimeout(800);
report.lab.naked25 = await mvRow(page);
report.lab.ticksLabelAfter = await item.locator("label, .ind-field").allTextContents();
await shot(page, "mvnaked-lab");
report.labFewer =
  anchorsOf(report.lab.naked25) != null &&
  anchorsOf(report.lab.naked25) <= anchorsOf(report.lab.distance25);
const p = await probeChart(page);
report.labInk = p.ink;

console.log(JSON.stringify(report, null, 2));
console.log("errors:", errors.length ? errors : "none");
await browser.close();
