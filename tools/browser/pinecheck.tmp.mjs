// The Pine seam: a study we transcribed ourselves, from the picker to the canvas.
//
//   node tools/browser/pinecheck.tmp.mjs [--headed]
//
// What is worth establishing is that `pineStudy` really does produce a catalogue
// entry indistinguishable from a borrowed one — that the picker files it on our
// side of the line, and that everything downstream (the legend row, the label,
// the generated settings, the per-plot colour knobs, the per-pane spec, the
// reload) works on it without having been told it is different.
//
// The two plots are recoloured to probe colours before anything is counted. The
// study ships in the app's own emaPalette, which the fixed EMA layer also draws
// in, and "the lemon pixels went up" cannot tell the two apart — the same trap
// the External Chart layer's default bear hue set, where it matched the
// down-candle red exactly.
import { launch, openChart, shot } from "./lib.mjs";

const REPLAY = "/charts/replay";
const headed = process.argv.includes("--headed");

const FAST_PROBE = "#ff00ff";
const SLOW_PROBE = "#00ffff";

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

const panes = (page) =>
  page.evaluate(() => {
    const t = [...document.querySelectorAll("table")].sort(
      (a, b) => b.querySelectorAll("canvas").length - a.querySelectorAll("canvas").length,
    )[0];
    return t ? [...t.querySelectorAll("tr")].filter((r) => r.querySelector("canvas")).length : 0;
  });

const studyRows = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll(".chart-legend-row.study")].map((el) => el.textContent.trim()),
  );

const openPicker = async (page) => {
  await page.locator(".study-pick > button").click();
  await page.waitForSelector(".study-pop", { timeout: 10000 });
  await page.waitForFunction(() => document.querySelectorAll(".study-hit:not(.mine)").length > 0, null, {
    timeout: 30000,
  });
};

/** Which section head a titled hit sits under, by walking the rendered order —
 *  the sections are siblings in one flow, so membership is a DOM-order fact. */
const sectionOf = (page, title) =>
  page.evaluate((t) => {
    let head = null;
    for (const el of document.querySelector(".study-results").children) {
      if (el.classList.contains("study-head")) head = el.firstChild?.textContent?.trim() ?? "";
      else if (el.querySelector?.(".study-hit-title")?.textContent?.trim() === t) return head;
    }
    return null;
  }, title);

const setColour = async (page, label, hex) => {
  await page
    .locator(".chart-set-row")
    .filter({ has: page.locator(`.chart-set-label:text-is("${label}")`) })
    .first()
    .locator('input[type="color"]')
    .fill(hex);
  await page.waitForTimeout(900);
};

const { browser, page, errors } = await launch({ headed });
const out = [];
const ok = (label, pass) => out.push([label, pass]);
try {
  await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.removeItem("chart.studies"));
  await openChart(page, REPLAY);

  const basePanes = await panes(page);
  const magentaBase = await colourPixels(page, FAST_PROBE);
  const cyanBase = await colourPixels(page, SLOW_PROBE);

  // --- the picker files it on our side of the line ---------------------------
  await openPicker(page);
  const heads = (await page.locator(".study-head").allTextContents()).map((h) =>
    h.replace(/\s+/g, " ").trim(),
  );
  ok(
    `three sections, ours before theirs (${heads.join(" | ")})`,
    heads.length === 3 &&
      /chart/i.test(heads[0]) &&
      /transcribed/i.test(heads[1]) &&
      /community/i.test(heads[2]),
  );
  ok(`EMA pair is under ours (${await sectionOf(page, "EMA pair")})`,
     /transcribed/i.test((await sectionOf(page, "EMA pair")) ?? ""));

  // Searched together, filed apart — the point of one merged catalogue.
  await page.fill(".study-search", "ema");
  await page.waitForTimeout(400);
  const mineHits = await page.evaluate(() => {
    let head = null, mine = 0, theirs = 0;
    for (const el of document.querySelector(".study-results").children) {
      if (el.classList.contains("study-head")) head = el.firstChild?.textContent?.trim() ?? "";
      else if (el.classList.contains("study-hit") && !el.classList.contains("mine"))
        /transcribed/i.test(head) ? mine++ : theirs++;
    }
    return { mine, theirs };
  });
  ok(`one search reaches both (${mineHits.mine} ours, ${mineHits.theirs} theirs)`,
     mineHits.mine === 1 && mineHits.theirs > 3);

  await page
    .locator(".study-hit")
    .filter({ has: page.locator('.study-hit-title:text-is("EMA pair")') })
    .first()
    .click();
  await page.waitForTimeout(1200);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  // --- and the rest of the chart treats it like any other study --------------
  ok(`stayed an overlay (${await panes(page)} panes)`, (await panes(page)) === basePanes);
  const rows = await studyRows(page);
  ok(`one legend row, labelled from its own inputs (${rows.join(" | ")})`,
     rows.length === 1 && /EMA×2 \(9, 21\)/.test(rows[0]));
  ok(`it drew (no error on the row)`,
     !(await page.locator(".chart-legend-row.study").first().getAttribute("class"))?.includes("off"));

  await page.locator(".chart-legend-item").filter({ has: page.locator(".chart-legend-row.study") })
    .first().locator(".chart-legend-dots").click();
  await page.waitForSelector(".chart-set", { timeout: 5000 });
  const labels = (await page.locator(".chart-set .chart-set-label").allTextContents()).map((l) => l.trim());
  ok(`settings generated from the script (${labels.join(", ")})`,
     labels.join(",") === "Fast length,Slow length,Source,Fast colour,Slow colour");

  // --- two plots, coloured independently -------------------------------------
  await setColour(page, "Fast colour", FAST_PROBE);
  await setColour(page, "Slow colour", SLOW_PROBE);
  const magenta = await colourPixels(page, FAST_PROBE);
  const cyan = await colourPixels(page, SLOW_PROBE);
  ok(`the fast line is its own plot (${magentaBase} -> ${magenta} px)`, magenta > magentaBase + 150);
  ok(`the slow line is another (${cyanBase} -> ${cyan} px)`, cyan > cyanBase + 150);

  // Re-tuning a Pine input reaches the runtime, not just the label.
  await page.locator(".chart-set-row").filter({ has: page.locator('.chart-set-label:text-is("Fast length")') })
    .first().locator('input[type="number"]').fill("50");
  await page.waitForTimeout(1200);
  const retunedPx = await colourPixels(page, FAST_PROBE);
  ok(`re-tuning relabels the row (${(await studyRows(page))[0]})`, /EMA×2 \(50, 21\)/.test((await studyRows(page))[0] ?? ""));
  ok(`...and recomputes the line (${magenta} -> ${retunedPx} px)`, retunedPx !== magenta);
  await page.keyboard.press("Escape");
  await shot(page, "pine-study");

  // --- persisted like any other spec ------------------------------------------
  const saved = await page.evaluate(() => localStorage.getItem("chart.studies"));
  ok(`saved under its prefixed key (${saved?.slice(0, 60)}…)`, /"key":"pine:emaPair"/.test(saved ?? ""));

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(3500);
  ok(`survived a reload (${(await studyRows(page))[0]})`,
     /EMA×2 \(50, 21\)/.test((await studyRows(page))[0] ?? "") &&
     (await colourPixels(page, FAST_PROBE)) > magentaBase + 150);

  ok(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  ok(`threw: ${e.message}`, false);
  await shot(page, "pine-threw").catch(() => {});
} finally {
  await browser.close();
}

let failed = 0;
console.log("\npine seam");
for (const [label, pass] of out) {
  console.log(`  ${pass ? "ok  " : "FAIL"} ${label}`);
  if (!pass) failed++;
}
process.exit(failed ? 1 : 0);
