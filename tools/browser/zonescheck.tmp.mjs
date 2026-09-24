// The ranked S/R zones layer, end to end on /charts/replay.
//
//   node tools/browser/zonescheck.tmp.mjs [--headed]
//
// lib/rankedZones is pinned by tools/ranked-zones/check.mjs — the arithmetic is
// not what this is for. This is for the half that check cannot see: that the
// layer registers, that the primitive puts its borders on the canvas, that the
// knobs reach the walk rather than only the label, and that a knob which only
// changes drawing does not pay for a recompute it doesn't need.
//
// Probing the zone *borders*, which are opaque strokes. The fills are 11% washes
// and blend into whatever is behind them; the strength bars are 50%. Border
// green is deliberately off the candle schemes' greens (see theme's rankedZones
// note) — nearest is classic's #21c07a, 32 apart on the green channel against a
// tolerance of 26.
import { launch, openChart, shot } from "./lib.mjs";

const REPLAY = "/charts/replay";
const headed = process.argv.includes("--headed");
const SUPPORT = "#15a06b";
const RESISTANCE = "#c2453a";

const colourPixels = (page, hex) =>
  page.evaluate((h) => {
    const want = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4)
      if (
        Math.abs(d[i] - want[0]) < 26 &&
        Math.abs(d[i + 1] - want[1]) < 26 &&
        Math.abs(d[i + 2] - want[2]) < 26
      )
        n++;
    return n;
  }, hex);

const legendRow = (page) =>
  page.evaluate(() => {
    const r = [...document.querySelectorAll(".chart-legend-row")].find((e) =>
      e.textContent.includes("Ranked S/R zones"),
    );
    return r ? { text: r.textContent.trim(), off: r.className.includes("off") } : null;
  });

const toggleFromPicker = async (page, name) => {
  await page.locator(".study-pick > button").click();
  await page.waitForSelector(".study-pop", { timeout: 10000 });
  await page.locator(".study-hit.mine").filter({ hasText: name }).first().click();
  await page.waitForTimeout(600);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
};

const openKnobs = async (page) =>
  page
    .locator(".chart-legend-item")
    .filter({ has: page.locator(".chart-legend-row", { hasText: "Ranked S/R zones" }) })
    .first()
    .locator(".chart-legend-dots")
    .click();

const setKnob = async (page, label, value) => {
  await page
    .locator(".chart-set-row")
    .filter({ has: page.locator(`.chart-set-label:text-is("${label}")`) })
    .first()
    .locator("select")
    .selectOption(String(value));
  await page.waitForTimeout(1400);
};

const { browser, page, errors } = await launch({ headed });
const out = [];
const ok = (label, pass) => out.push([label, pass]);
try {
  await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    localStorage.removeItem("chart.rankedZones");
    localStorage.removeItem("chart.indicatorVisibility");
  });
  await openChart(page, REPLAY);

  const sBase = await colourPixels(page, SUPPORT);
  // An off layer still has a legend row — dimmed, with its eye shut. That is how
  // every layer on this chart reads, and the first version of this file asserted
  // the row was absent.
  const before = await legendRow(page);
  ok(`off by default (row present, dimmed: ${before?.off})`, before !== null && before.off === true);
  // Not zero: the replay opens on a random day and something green on some of
  // them lands inside the probe's tolerance. The signal is the ratio to the
  // on-state, which is two orders of magnitude up.
  ok(`and drawing nothing (${sBase} px)`, sBase < 500);

  await toggleFromPicker(page, "Ranked S/R zones");
  const row = await legendRow(page);
  ok(`the layer switched on and named itself (${row?.text})`, !!row && /Ranked S\/R zones · top \d+/.test(row.text));
  const top = Number(row.text.match(/top (\d+)/)[1]);
  ok(`it found zones to draw (top ${top})`, top > 0 && top <= 8);
  ok(`...and the row is not dimmed`, row.off === false);

  const s1 = await colourPixels(page, SUPPORT);
  ok(`support borders on the canvas (${sBase} -> ${s1} px)`, s1 > sBase + 100);
  // Not probed by colour: #c2453a reads ~13k pixels on a bare chart, so
  // something already drawn sits inside the tolerance and the number means
  // nothing. The resistance half is covered by the Sides filter below and by
  // tools/ranked-zones/check.mjs, which counts both directions directly.
  await shot(page, "ranked-zones");

  // --- the knobs reach the walk, not just the label -------------------------
  await openKnobs(page);
  await page.waitForSelector(".chart-set", { timeout: 5000 });
  const labels = (await page.locator(".chart-set .chart-set-label").allTextContents()).map((l) => l.trim());
  ok(`knobs generated (${labels.length}: ${labels.slice(0, 4).join(", ")}…)`,
     labels.length === 14 && labels.includes("Rank by") && labels[0] === "Show top zones" && labels.includes("Break buffer"));

  await setKnob(page, "Show top zones", 3);
  const capped = await legendRow(page);
  ok(`the draw cap bites (${capped?.text})`, /top [1-3]$/.test(capped?.text ?? ""));
  const s2 = await colourPixels(page, SUPPORT);
  ok(`...and the canvas lost zones (${s1} -> ${s2} px)`, s2 < s1);

  // A pivot length change re-runs the whole walk; the zone set must actually move.
  await setKnob(page, "Pivot length", 20);
  const s3 = await colourPixels(page, SUPPORT);
  const r3 = await colourPixels(page, RESISTANCE);
  ok(`a longer pivot changes the zone set (${s2} -> ${s3} support px, ${r3} resistance)`, s3 !== s2);

  // Both directions are really there. Asserted as an identity rather than as
  // "the count drops", because it need not: at a long pivot length this tape
  // leaves only support zones alive, and hiding an empty half changes nothing —
  // which the first version of this file scored as a failure. Uncapped first, so
  // the draw limit cannot truncate any of the three counts.
  await setKnob(page, "Pivot length", 5);
  await setKnob(page, "Show top zones", 30);
  const count = async () => Number((await legendRow(page)).text.match(/top (\d+)/)[1]);
  const bothN = await count();
  await setKnob(page, "Sides", "support");
  const supN = await count();
  await setKnob(page, "Sides", "resistance");
  const resN = await count();
  ok(`the Sides filter partitions the set (${supN} support + ${resN} resistance = ${bothN})`,
     bothN > 1 && supN + resN === bothN);
  await setKnob(page, "Sides", "all");

  // The flow ranking: the chart has to hand the walk its tape, or the legend
  // admits it fell back. Capped at 3 so a re-rank can move what is drawn.
  await setKnob(page, "Show top zones", 3);
  const scorePx = await colourPixels(page, SUPPORT);
  await setKnob(page, "Rank by", "flow");
  const flowRow = await legendRow(page);
  ok(`rank by flow reads the tape (${flowRow?.text})`, / by flow$/.test(flowRow?.text ?? ""));
  ok(`...and redraws (${scorePx} -> ${await colourPixels(page, SUPPORT)} support px)`,
     /top [1-3]/.test(flowRow?.text ?? ""));
  await shot(page, "ranked-zones-flow");
  await setKnob(page, "Rank by", "score");
  ok(`and back (${(await legendRow(page))?.text})`, !/flow/.test((await legendRow(page))?.text ?? ""));
  await setKnob(page, "Show top zones", 30);

  // A draw-only knob must change the picture without breaking it.
  await setKnob(page, "Strength bars", 0);
  await page.waitForTimeout(600);
  ok(`strength bars toggle off cleanly (${await colourPixels(page, SUPPORT)} px, row ${(await legendRow(page))?.text})`,
     (await legendRow(page)) !== null);
  await page.keyboard.press("Escape");

  // --- persistence ------------------------------------------------------------
  const saved = await page.evaluate(() => localStorage.getItem("chart.rankedZones"));
  ok(`params persisted (${saved?.slice(0, 80)}…)`, /"visibleLimit":30/.test(saved ?? "") && /"strengthBars":false/.test(saved ?? ""));

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(3500);
  const after = await legendRow(page);
  ok(`survived a reload, still on (${after?.text})`, after !== null && after.off === false && /top [1-9]/.test(after.text));
  ok(`still drawing (${await colourPixels(page, SUPPORT)} px)`, (await colourPixels(page, SUPPORT)) > sBase + 30);

  ok(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  ok(`threw: ${e.message}`, false);
  await shot(page, "ranked-zones-threw").catch(() => {});
} finally {
  await browser.close();
}

let failed = 0;
console.log("\nranked zones layer");
for (const [label, pass] of out) { console.log(`  ${pass ? "ok  " : "FAIL"} ${label}`); if (!pass) failed++; }
process.exit(failed ? 1 : 0);
