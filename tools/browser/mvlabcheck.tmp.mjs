// Throwaway: Modern VWAP on the Interactions Lab's session chart, and the same
// layer still behaving on the replay after it was pulled into a shared module.
//
// Two halves:
//   1. /interactions → first session row → the two MV legend rows exist, the
//      line draws (ink goes up when the eye opens), a knob turned through the
//      row's "…" re-derives it without costing the visible range, and the
//      choice survives a reload.
//   2. /charts/replay → the row still quotes what it always quoted.
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

const seedVis = async (page, on) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate((v) => {
    const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
    vis.modernVwap = v;
    vis.modernVwapSignals = v;
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
    localStorage.removeItem("chart.modernVwap");
  }, on);
};

const openLab = async (page) => {
  await page.goto(`${BASE}/interactions`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("tbody tr", { timeout: 60000 });
  await page.locator("tbody tr").first().click();
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(3000);
};

const rowText = async (page, re) =>
  (await page.locator(`.chart-legend >> text=${re}`).first().textContent())?.trim();

const { browser, page, errors } = await launch();
const report = {};

// --- the Lab, layer off ------------------------------------------------------
await seedVis(page, false);
await openLab(page);
const off = await probeChart(page);
report.rowsOff = await page.locator(".chart-legend-row").allTextContents();
report.hasMvRows = report.rowsOff.filter((r) => r.includes("Modern VWAP")).length === 2;
report.inkOff = off.ink;
await shot(page, "mvlab-1-off");

// --- turn the line on through its own eye ------------------------------------
await page.locator(".chart-legend-row").filter({ hasText: "Modern VWAP · " }).first().click();
await page.waitForTimeout(800);
const on = await probeChart(page);
report.inkOn = on.ink;
report.drew = on.ink > off.ink + 200;
report.legendOn = await rowText(page, "/Modern VWAP · /");
await shot(page, "mvlab-2-on");

// --- the triggers ------------------------------------------------------------
await page
  .locator(".chart-legend-row")
  .filter({ hasText: "Modern VWAP signals" })
  .first()
  .click();
await page.waitForTimeout(800);
report.legendSignals = await rowText(page, "/Modern VWAP signals/");
report.signalsFired = /· \d+ through the gate/.test(report.legendSignals ?? "");
await shot(page, "mvlab-3-signals");

// --- a knob is a redraw, never a rebuild -------------------------------------
// Framed away from the opening fit first: a rebuild resets the visible range,
// and a chart that happens to be sitting on its default frame cannot show that.
// The knob turned here (a signals-row one, with the triggers put back away)
// changes no pixels at all, so any silhouette change *is* the lost frame.
await page
  .locator(".chart-legend-row")
  .filter({ hasText: "Modern VWAP signals" })
  .first()
  .click();
const box = await page.locator("canvas").first().boundingBox();
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
for (let i = 0; i < 6; i++) await page.mouse.wheel(0, -120);
await page.waitForTimeout(600);
const framed = await probeChart(page);
const sigItem = page.locator('[data-ind-item="modernVwapSignals"]');
await sigItem.locator(".chart-legend-dots").click();
await page.waitForTimeout(200);
// Whichever select it is, take an option it isn't already on — the panel's
// fields come and go with the anchor, so naming one by position would rot.
report.knobTurned = await sigItem.locator("select").evaluateAll((sels) => {
  // Skipping the first: that is the anchor, which both rows carry, and turning
  // it moves the line — the one knob here that isn't invisible.
  for (const s of sels.slice(1)) {
    const other = [...s.options].find((o) => o.value !== s.value);
    if (!other) continue;
    s.value = other.value;
    s.dispatchEvent(new Event("change", { bubbles: true }));
    return `${sels.indexOf(s)} → ${other.value}`;
  }
  return null;
});
await page.waitForTimeout(800);
report.frameKept = (await probeChart(page)).silhouette === framed.silhouette;
await page.keyboard.press("Escape");
await shot(page, "mvlab-4-frame");

// --- the anchor, the knob every other one is measured against ----------------
const mvItem = page.locator('[data-ind-item="modernVwap"]');
await mvItem.locator(".chart-legend-dots").click();
await page.waitForTimeout(200);
const anchorSel = mvItem.locator("select").first();
report.anchorOptions = await anchorSel.locator("option").evaluateAll((o) => o.map((x) => x.value));
await anchorSel.selectOption("globex");
await page.waitForTimeout(800);
report.legendAfterKnob = await rowText(page, "/Modern VWAP · /");
report.knobRedrew = report.legendAfterKnob !== report.legendOn;
await shot(page, "mvlab-5-globex");
await page.keyboard.press("Escape");

// --- and it sticks ------------------------------------------------------------
await openLab(page);
report.legendAfterReload = await rowText(page, "/Modern VWAP · /");
report.persisted = (report.legendAfterReload ?? "").includes("globex");
const reloaded = await probeChart(page);
report.inkAfterReload = reloaded.ink;
await shot(page, "mvlab-6-reload");

// --- the replay, unchanged ----------------------------------------------------
await seedVis(page, true);
await openChart(page, REPLAY);
await page.waitForTimeout(1500);
report.replayRows = (await page.locator(".chart-legend-row").allTextContents()).filter((r) =>
  r.includes("Modern VWAP"),
);
const rep = await probeChart(page);
report.replayInk = rep.ink;
await shot(page, "mvlab-7-replay");

console.log(JSON.stringify(report, null, 2));
console.log("errors:", errors.length ? errors : "none");
await browser.close();
