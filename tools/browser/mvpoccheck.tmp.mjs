// Throwaway: the Modern VWAP `poc` anchor on the real replay chart.
//
// Seeds prefs before load (MV rows on, anchor=poc, anchor marks on), opens the
// Simulator, and asserts off the legend row — which quotes the anchor count —
// plus the usual "drew something, threw nothing" facts. Then flips through the
// re-arm options via seeded prefs reloads and checks the count moves the way
// the study page says it must (off ≥ 25t ≥ 50t).
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

const seed = async (page, mvPatch) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate((patch) => {
    const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
    vis.modernVwap = true;
    vis.modernVwapSignals = false;
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
    const prefs = JSON.parse(localStorage.getItem("sim.prefs") || "{}");
    prefs.modernVwap = {
      ...(prefs.modernVwap || {}),
      anchor: "poc",
      anchorMarks: true,
      bands: 2,
      ...patch,
    };
    localStorage.setItem("sim.prefs", JSON.stringify(prefs));
  }, mvPatch);
};

const mvRow = async (page) =>
  page.locator(".chart-legend >> text=/Modern VWAP/").first().textContent();

const { browser, page, errors } = await launch();
const report = {};

await seed(page, { rearmTicks: 25 });
await openChart(page, REPLAY);
const p = await probeChart(page);
report.canvas = { w: p.w, h: p.h, ink: p.ink };
report.legend25 = (await mvRow(page)).trim();
await shot(page, "mv-poc-25t");

const anchorsOf = (s) => {
  const m = s.match(/(\d+)⚓/);
  return m ? +m[1] : null;
};
report.anchors = { t25: anchorsOf(report.legend25) };

for (const [key, rearmTicks] of [["t0", 0], ["t50", 50]]) {
  await seed(page, { rearmTicks });
  await openChart(page, REPLAY);
  const row = (await mvRow(page)).trim();
  report[`legend${rearmTicks}`] = row;
  report.anchors[key] = anchorsOf(row);
}
await shot(page, "mv-poc-off");

// Weekly source: the Developing VA · Weekly row appears, and the MV anchor
// reads the weekly POC (different anchor count than the session POC's).
await seed(page, { rearmTicks: 25, pocSource: "weekly" });
await page.evaluate(() => {
  const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
  vis.developingProfileWeekly = true;
  localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
});
await openChart(page, REPLAY);
report.legendWeekly = (await mvRow(page)).trim();
report.weeklyVaRow = (await page
  .locator(".chart-legend >> text=/Developing VA · Weekly/")
  .count()) > 0;
report.anchors.weekly = anchorsOf(report.legendWeekly);
await shot(page, "mv-poc-weekly");

// And back to the published default: swing must still say what it always said.
await seed(page, { anchor: "swing", rearmTicks: 25 });
await openChart(page, REPLAY);
report.legendSwing = (await mvRow(page)).trim();
await shot(page, "mv-swing-back");

report.ordering =
  report.anchors.t0 != null &&
  report.anchors.t0 >= report.anchors.t25 &&
  report.anchors.t25 >= report.anchors.t50;

console.log(JSON.stringify(report, null, 2));
console.log("errors:", errors.length ? errors : "none");
await browser.close();
