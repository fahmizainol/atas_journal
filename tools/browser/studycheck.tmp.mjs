// The indicator catalogue and the legend rows it feeds, driven end to end on
// /charts/replay.
//
//   node tools/browser/studycheck.tmp.mjs [--headed]
//
// The design under test: the ƒ is add/remove only, the pane's own legend is where
// everything on the chart is listed and tuned, and both halves act on the focused
// pane. So the things worth establishing are the ones that span the two surfaces —
// add in the ƒ, see it on the legend; toggle on the legend, see the ƒ agree; and
// the app's own layers behaving the same way through the same button.
//
// Pane count is read off lightweight-charts' own DOM (each pane is a table row
// with its own canvases) because a study claiming a pane is the one structural
// fact a screenshot cannot settle.
import { launch, openChart, probeChart, shot } from "./lib.mjs";

const REPLAY = "/charts/replay";
const headed = process.argv.includes("--headed");

/** Moving Average Exponential's own blue (its plotConfig[0]) — nothing else on
 *  this chart draws in it, which is what makes counting it an observable. An
 *  overlay is a 1px line running mostly inside the candle bodies, so whole-canvas
 *  ink moves by tens of samples out of thirty thousand and two runs of the same
 *  build straddle the difference. */
const EMA_BLUE = "#2962FF";

const colourPixels = (page, hex) =>
  page.evaluate((h) => {
    const want = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (
        Math.abs(d[i] - want[0]) < 26 &&
        Math.abs(d[i + 1] - want[1]) < 26 &&
        Math.abs(d[i + 2] - want[2]) < 26
      )
        n++;
    }
    return n;
  }, hex);

const panes = (page) =>
  page.evaluate(() => {
    const t = [...document.querySelectorAll("table")].sort(
      (a, b) => b.querySelectorAll("canvas").length - a.querySelectorAll("canvas").length,
    )[0];
    if (!t) return 0;
    return [...t.querySelectorAll("tr")].filter((r) => r.querySelector("canvas")).length;
  });

const openPicker = async (page) => {
  await page.locator(".study-pick > button").click();
  await page.waitForSelector(".study-pop", { timeout: 10000 });
};
const closePicker = async (page) => {
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
};

/** The legend's rows, as text. The list starts collapsed on a touch pointer and
 *  open on a mouse; this harness is a mouse. */
const legendRows = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll(".chart-legend-item .chart-legend-row")]
      .map((el) => el.textContent.trim())
      .filter(Boolean),
  );
const studyRows = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll(".chart-legend-row.study")].map((el) => el.textContent.trim()),
  );

const addStudy = async (page, short) => {
  await page.fill(".study-search", short);
  await page.waitForFunction(
    (s) => [...document.querySelectorAll(".study-hit-short")].some((e) => e.textContent === s),
    short,
    { timeout: 30000 },
  );
  await page
    .locator(".study-hit")
    .filter({ has: page.locator(`.study-hit-short:text-is("${short}")`) })
    .first()
    .click();
  await page.waitForTimeout(900);
};

const { browser, page, errors } = await launch({ headed });
const out = [];
const ok = (label, pass) => out.push([label, pass]);
try {
  await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    localStorage.removeItem("chart.studies");
    localStorage.removeItem("chart.indicatorVisibility");
  });
  await openChart(page, REPLAY);

  const basePanes = await panes(page);
  const baseRows = (await legendRows(page)).length;

  // --- the catalogue's two sections -----------------------------------------
  await openPicker(page);
  // Not `.study-hit` — the 21 app rows satisfy that on their own, and the
  // community half is a separate 1.8 MB import that may still be in flight.
  await page.waitForFunction(
    () => document.querySelectorAll(".study-hit:not(.mine)").length > 0,
    null,
    { timeout: 30000 },
  );
  const heads = await page.locator(".study-head").allTextContents();
  // Three now: the app's own layers, the Pine we transcribed (see
  // tools/browser/pinecheck.tmp.mjs, which owns that section), then the borrowed
  // catalogue. Ordered by how much is known about what they draw.
  ok(
    `three sections, ours first (${heads.map((h) => h.trim()).join(" | ")})`,
    heads.length === 3 &&
      /chart/i.test(heads[0]) &&
      /transcribed/i.test(heads[1]) &&
      /community/i.test(heads[2]),
  );
  const mineCount = await page.locator(".study-hit.mine").count();
  ok(`all 21 of ours listed (${mineCount})`, mineCount === 21);
  const placeholder = await page.getAttribute(".study-search", "placeholder");
  ok(`searches both (${placeholder})`, /43\d|4[0-9]{2}/.test(placeholder ?? ""));

  // Search reaches across the rule: "vwap" is ours *and* theirs.
  await page.fill(".study-search", "vwap");
  await page.waitForTimeout(300);
  const vwapMine = await page.locator(".study-hit.mine").count();
  const vwapTheirs = await page.locator(".study-hit:not(.mine)").count();
  ok(`one search, both sides (${vwapMine} ours, ${vwapTheirs} theirs)`, vwapMine >= 4 && vwapTheirs >= 1);
  await page.fill(".study-search", "");

  // On, but with nothing to draw yet. Which layers those are depends on the
  // sitting — the IB before the hour is up, the composite with no context days,
  // your own fills before you have taken a trade — so this asserts the *rule*
  // rather than a hand-picked layer: every row the catalogue marks as waiting is
  // on, and has no legend row, and every row it does not mark either is off or
  // has one. Naming a layer here was the first version and it was wrong twice
  // over (Playwright's hasText is a case-insensitive substring, so "Trades"
  // matched "Big trades" first).
  const waiting = await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".study-hit.mine")];
    const legend = [...document.querySelectorAll(".chart-legend-row")].map((e) =>
      e.textContent.trim(),
    );
    const bad = [];
    let marked = 0;
    for (const r of rows) {
      const name = r.querySelector(".study-hit-title").textContent.trim();
      const on = r.className.includes(" on");
      const note = !!r.querySelector(".study-hit-where");
      const drawn = legend.some((l) => l.startsWith(name));
      if (note) {
        marked++;
        if (!on || drawn) bad.push(`${name}: marked but on=${on} drawn=${drawn}`);
      } else if (on && !drawn) {
        bad.push(`${name}: on and undrawn but unmarked`);
      }
    }
    return { marked, bad, total: rows.length };
  });
  ok(
    `"nothing to draw yet" matches the legend exactly (${waiting.marked} of ${waiting.total} waiting)` +
      (waiting.bad.length ? ` — ${waiting.bad.join("; ")}` : ""),
    waiting.bad.length === 0,
  );

  // --- an app layer, switched on and off from the ƒ --------------------------
  // Modern VWAP: offered on both pages (so always drawable) and off by default,
  // which makes it the one layer whose row can be made to appear on demand.
  const mvRow = page.locator(".study-hit.mine").filter({ hasText: "Modern VWAP" }).first();
  const mvBefore = (await mvRow.getAttribute("class"))?.includes(" on");
  await mvRow.click();
  await page.waitForTimeout(500);
  ok(
    `an app layer toggles from the ƒ (${mvBefore} -> ${(await mvRow.getAttribute("class"))?.includes(" on")})`,
    mvBefore !== (await mvRow.getAttribute("class"))?.includes(" on"),
  );
  await closePicker(page);
  const rowsWithMv = await legendRows(page);
  ok(
    `...and the chart drew it (${baseRows} rows, eye open on Modern VWAP)`,
    rowsWithMv.some((r) => r.startsWith("Modern VWAP")) &&
      (await page
        .locator(".chart-legend-row")
        .filter({ hasText: "Modern VWAP · " })
        .first()
        .getAttribute("class"))?.includes("off") === false,
  );
  // Off again, from the legend this time — the two surfaces are one state.
  await page.locator(".chart-legend-row").filter({ hasText: "Modern VWAP · " }).first().click();
  await page.waitForTimeout(400);
  await openPicker(page);
  ok(
    `the legend's eye and the ƒ are one state (${(await mvRow.getAttribute("class"))?.includes(" on")})`,
    (await mvRow.getAttribute("class"))?.includes(" on") === mvBefore,
  );

  // --- a community study -----------------------------------------------------
  await addStudy(page, "RSI");
  const withRsi = await panes(page);
  ok(`RSI claimed a pane (${basePanes} -> ${withRsi})`, withRsi === basePanes + 1);
  const blueBefore = await colourPixels(page, EMA_BLUE);
  await addStudy(page, "EMA");
  const blueAfter = await colourPixels(page, EMA_BLUE);
  ok(`EMA stayed an overlay (${await panes(page)} panes)`, (await panes(page)) === withRsi);
  ok(`EMA drew its line (${blueBefore} -> ${blueAfter} px of its own blue)`, blueAfter > blueBefore + 200);
  await closePicker(page);

  // --- and they are legend rows, not picker rows ------------------------------
  const studies = await studyRows(page);
  ok(`both are rows on the chart (${studies.join(" | ")})`, studies.length === 2);
  ok(`labelled with their inputs (${studies[0] ?? ""})`, /\(\d/.test(studies[0] ?? ""));
  ok(`under a community rule`, (await page.locator(".chart-legend-rule").count()) === 1);
  ok(`each has an ×`, (await page.locator(".chart-legend-x").count()) === 2);
  await shot(page, "studies-legend");

  // The eye hides it *and* stops computing it — the pane goes with it.
  await page.locator(".chart-legend-row.study").filter({ hasText: "RSI" }).first().click();
  await page.waitForTimeout(700);
  ok(`hiding a study drops its pane (${await panes(page)})`, (await panes(page)) === basePanes);
  await page.locator(".chart-legend-row.study").filter({ hasText: "RSI" }).first().click();
  await page.waitForTimeout(700);
  ok(`...and showing it brings it back (${await panes(page)})`, (await panes(page)) === withRsi);

  // Settings, generated from the study's own inputConfig, in the app's own panel.
  await page
    .locator(".chart-legend-item")
    .filter({ has: page.locator(".chart-legend-row.study", { hasText: "RSI" }) })
    .first()
    .locator(".chart-legend-dots")
    .click();
  await page.waitForTimeout(300);
  const fields = await page.locator(".chart-set .chart-set-row").count();
  ok(`settings open in the app's own panel (${fields} fields)`, fields > 0);
  await page.locator('.chart-set input[type="number"]').first().fill("7");
  await page.waitForTimeout(800);
  const retuned = (await studyRows(page)).find((r) => r.startsWith("RSI")) ?? "";
  ok(`re-tuning relabels the row (${retuned})`, retuned.includes("7"));
  await page.keyboard.press("Escape");

  // --- reload, then removal ---------------------------------------------------
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(3000);
  ok(`survived a reload (${(await studyRows(page)).length} rows, ${await panes(page)} panes)`,
     (await studyRows(page)).length === 2 && (await panes(page)) === withRsi);

  await page
    .locator(".chart-legend-item")
    .filter({ has: page.locator(".chart-legend-row.study", { hasText: "RSI" }) })
    .first()
    .locator(".chart-legend-x")
    .click();
  await page.waitForTimeout(800);
  ok(`× removes it and gives the pane back (${await panes(page)})`, (await panes(page)) === basePanes);
  ok(`one study left`, (await studyRows(page)).length === 1);

  // --- per pane ---------------------------------------------------------------
  // The whole point of the per-pane model: pane 2 starts empty even though pane 1
  // is carrying an EMA.
  await page.locator(".chart-layout-btn").click();
  await page.locator('.chart-layout-menu button[title="Two side by side"]').click();
  await page.waitForTimeout(3500);
  const perPane = await page.evaluate(() =>
    [...document.querySelectorAll(".sim-pane")].map(
      (el) => el.querySelectorAll(".chart-legend-row.study").length,
    ),
  );
  ok(`studies are per pane (${perPane.join(", ")})`, perPane.length === 2 && perPane[0] === 1 && perPane[1] === 0);
  await shot(page, "studies-per-pane");

  ok(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  ok(`threw: ${e.message}`, false);
  await shot(page, "studies-threw").catch(() => {});
} finally {
  await browser.close();
}

let failed = 0;
console.log("\nstudies");
for (const [label, pass] of out) {
  console.log(`  ${pass ? "ok  " : "FAIL"} ${label}`);
  if (!pass) failed++;
}
process.exit(failed ? 1 : 0);
