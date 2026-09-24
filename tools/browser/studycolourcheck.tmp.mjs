// Recolouring a community study, driven end to end on /charts/replay.
//
//   node tools/browser/studycolourcheck.tmp.mjs [--headed]
//
// The thing under test is the one the package cannot do for us: it colours by
// export, so two EMAs are two lines in the identical blue. The override lives on
// the spec, which means the fact worth establishing is that it reaches all three
// places a colour is visible — the canvas, the legend swatch, and localStorage —
// and that it lands on the plot it was set on rather than the study.
//
// Pixels are counted off lightweight-charts' own canvas, the same observable
// studycheck uses: a line's colour is not a DOM fact anywhere else.
import { launch, openChart, shot } from "./lib.mjs";

const REPLAY = "/charts/replay";
const headed = process.argv.includes("--headed");

/** EMA's shipped blue, and a colour nothing on this chart draws in. Magenta is
 *  chosen for being far from every hue the app owns — the candles, the VWAPs and
 *  the profile are all warm/cool primaries, so a near-miss cannot be scored. */
const EMA_BLUE = "#2962FF";
const PICKED = "#ff00ff";

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

const addStudy = async (page, short) => {
  await page.locator(".study-pick > button").click();
  await page.waitForSelector(".study-pop", { timeout: 10000 });
  await page.waitForFunction(() => document.querySelectorAll(".study-hit:not(.mine)").length > 0, null, {
    timeout: 30000,
  });
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
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
};

const openSettings = async (page, label) =>
  page
    .locator(".chart-legend-item")
    .filter({ has: page.locator(".chart-legend-row.study", { hasText: label }) })
    .first()
    .locator(".chart-legend-dots")
    .click();

/** The legend swatch's background, as the browser resolved it. */
const swatch = (page) =>
  page.evaluate(() => {
    const row = [...document.querySelectorAll(".chart-legend-row.study")].find((r) =>
      r.textContent.includes("EMA"),
    );
    const el = row?.querySelector(".chart-legend-swatch");
    return el ? getComputedStyle(el).backgroundColor : "";
  });

const { browser, page, errors } = await launch({ headed });
const out = [];
const ok = (label, pass) => out.push([label, pass]);
try {
  await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.removeItem("chart.studies"));
  await openChart(page, REPLAY);

  const magentaBase = await colourPixels(page, PICKED);
  const blueBase = await colourPixels(page, EMA_BLUE);
  await addStudy(page, "EMA");
  const blueDrawn = await colourPixels(page, EMA_BLUE);
  ok(`EMA drew in the package's blue (${blueBase} -> ${blueDrawn} px)`, blueDrawn > blueBase + 200);

  // --- the panel now carries a colour per line the study draws ---------------
  await openSettings(page, "EMA");
  await page.waitForSelector(".chart-set", { timeout: 5000 });
  const labels = await page.locator(".chart-set .chart-set-label").allTextContents();
  const colours = await page.locator('.chart-set input[type="color"]').count();
  ok(
    `a colour row per plot, after the inputs (${labels.filter((l) => /colour/i.test(l)).join(", ")})`,
    colours === 4 && /colour/i.test(labels[labels.length - 1] ?? ""),
  );
  ok(
    `the inputs still come first (${labels.slice(0, 2).join(", ")})`,
    !/colour/i.test(labels[0] ?? "x colour"),
  );

  // --- pick one -------------------------------------------------------------
  const emaRow = page
    .locator(".chart-set-row")
    .filter({ has: page.locator('.chart-set-label:text-is("EMA colour")') })
    .first();
  ok(`the first line's row is named for it`, (await emaRow.count()) === 1);
  await emaRow.locator('input[type="color"]').fill(PICKED);
  await page.waitForTimeout(1200);

  const blueGone = await colourPixels(page, EMA_BLUE);
  const magenta = await colourPixels(page, PICKED);
  ok(`the line repainted (${magentaBase} -> ${magenta} px of the picked colour)`, magenta > magentaBase + 200);
  ok(`and stopped drawing in the old one (${blueDrawn} -> ${blueGone} px)`, blueGone < blueBase + 200);

  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  const sw = await swatch(page);
  ok(`the legend swatch followed it (${sw})`, sw === "rgb(255, 0, 255)");
  await shot(page, "study-colour");

  // --- it is a property of the spec, so it is persisted ----------------------
  const saved = await page.evaluate(() => localStorage.getItem("chart.studies"));
  ok(`stored on the spec, one plot only (${saved})`, /"colors":\{"plot0":"#ff00ff"\}/.test(saved ?? ""));

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(3000);
  const afterReload = await colourPixels(page, PICKED);
  ok(`survived a reload (${afterReload} px)`, afterReload > magentaBase + 200);
  ok(`swatch too (${await swatch(page)})`, (await swatch(page)) === "rgb(255, 0, 255)");

  ok(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  ok(`threw: ${e.message}`, false);
  await shot(page, "study-colour-threw").catch(() => {});
} finally {
  await browser.close();
}

let failed = 0;
console.log("\nstudy colour");
for (const [label, pass] of out) {
  console.log(`  ${pass ? "ok  " : "FAIL"} ${label}`);
  if (!pass) failed++;
}
process.exit(failed ? 1 : 0);
