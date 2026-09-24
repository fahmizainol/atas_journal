// The shelf knobs: does the panel show what you picked, and does the SECOND
// change land as well as the first?
//
// Both reported symptoms came from one missing memo dependency: the pages build
// `indicatorSettings` with useMemo and neither Simulator nor LiveChart listed
// `shelfParams`/`patchShelf`, so the spec — including every `value:` the panel
// renders — was frozen at mount while `onChange` (a stable useCallback) went on
// working. Hence "the change applied but the dropdown didn't move".
//
// So the assertion is on the SELECT's own value as much as on the effect: a
// check that only watched the chart would have passed on the broken build for
// the first change and is the reason this went unnoticed.
//
// Run: node tools/browser/shelfknobcheck.tmp.mjs [--headed]
import { launch, openChart, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const { browser, page, errors } = await launch({ headed });
try {
  await page.goto(`${BASE}/`);
  await page.evaluate(() => {
    const KEY = "chart.indicatorVisibility";
    let vis = {};
    try { vis = JSON.parse(localStorage.getItem(KEY) ?? "{}"); } catch { vis = {}; }
    localStorage.setItem(KEY, JSON.stringify({ ...vis, volumeShelf: true, volumeShelfBoxes: true }));
    // A known starting point, so "it changed" is not measured against whatever
    // the last session happened to leave behind.
    localStorage.setItem("chart.shelfParams", JSON.stringify({
      windowMin: 30, zMin: 2, minTicks: 4, minHoldMin: 10, smooth: 0.04, stepSec: 60,
    }));
    localStorage.setItem("sim.resume.replay", JSON.stringify({
      symbol: "NQZ5", date: "2025-12-17", clockMs: 0, contextTicks: 0,
    }));
  });
  await openChart(page, "/charts/replay", { timeout: 180000, waitUntil: "domcontentloaded" });

  const row = page.locator('[data-ind-item="volumeShelf"]');
  await row.waitFor({ timeout: 30000 });
  // The legend collapses to a few rows until opened; the shelves row lives below
  // the fold.
  if (!(await row.locator(".chart-legend-dots").count())) {
    await page.locator(".chart-legend").first().click().catch(() => {});
  }
  await row.locator(".chart-legend-dots").click();

  const win = row.locator("select").first();
  await win.waitFor({ timeout: 10000 });
  ok("panel opens on the shelves row", (await win.count()) === 1);

  const label = async () => (await row.locator(".chart-legend-row span").nth(1).innerText()).trim();
  const shown = async () => win.inputValue();

  ok("starts where it was told to", (await shown()) === "30", `select reads ${await shown()}`);

  // --- first change ---
  await win.selectOption("60");
  await page.waitForTimeout(1200);
  ok("1st change: select shows the pick", (await shown()) === "60", `select reads ${await shown()}`);
  ok("1st change: legend follows", (await label()).includes("60m"), await label());

  // --- second change: the one that needed a refresh ---
  await win.selectOption("120");
  await page.waitForTimeout(1200);
  ok("2nd change: select shows the pick", (await shown()) === "120", `select reads ${await shown()}`);
  ok("2nd change: legend follows", (await label()).includes("120m"), await label());

  // --- third, and a different knob: the hold, which the replay tracker baked in
  //     at mount and could only change on a reload ---
  const hold = row.locator("select").nth(2);
  await hold.selectOption("5");
  await page.waitForTimeout(1200);
  ok("hold knob: select shows the pick", (await hold.inputValue()) === "5", await hold.inputValue());
  const boxRow = page.locator('[data-ind-item="volumeShelfBoxes"] .chart-legend-row span').nth(1);
  ok("hold knob: boxes' legend follows", (await boxRow.innerText()).includes("held 5m"),
    (await boxRow.innerText()).trim());

  // --- and does the hold knob reach the DRAWING? -----------------------------
  //
  // Everything above passes on a build where it does not. The legend label reads
  // `shelfParamsRef`, which follows the prop whatever the tracker is doing, so
  // the label moves either way — and the replay's tracker took its hold as a
  // *constructor* argument, built once at mount. Only the boxes actually drawn
  // can tell the two apart: at a 30-minute hold nothing has held long enough to
  // be one, at "off" every detected band is.
  const strokes = () =>
    page.evaluate(async () => {
      const proto = CanvasRenderingContext2D.prototype;
      const orig = proto.strokeRect;
      let n = 0, frames = 0;
      proto.strokeRect = function (...a) { n++; return orig.apply(this, a); };
      const t0 = performance.now();
      await new Promise((done) => {
        const tick = () => {
          frames++;
          if (performance.now() - t0 >= 1500) return done();
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
      proto.strokeRect = orig;
      return frames ? n / frames : -1;
    });

  await win.selectOption("30");
  await hold.selectOption("30");
  const play = page.locator("button", { hasText: "Play" }).first();
  if (await play.count()) await play.click().catch(() => {});
  await page.waitForTimeout(120000);           // ~1h of tape at 30x
  const atLongHold = await strokes();
  ok("30m hold draws no boxes yet", atLongHold === 0, `${atLongHold.toFixed(2)} strokeRect/frame`);

  // Clicking Play landed outside the panel, which closes it — the same
  // dismiss-on-outside-click every settings popover here uses.
  await row.locator(".chart-legend-dots").click();
  const hold2 = row.locator("select").nth(2);
  await hold2.waitFor({ timeout: 10000 });

  // A knob change resets and re-walks the bars already on screen, so this needs
  // no further tape — only the gate they are measured against has changed.
  await hold2.selectOption("0");
  await page.waitForTimeout(3000);
  const atNoHold = await strokes();
  ok("turning the hold off draws them", atNoHold > 0,
    `${atLongHold.toFixed(2)} -> ${atNoHold.toFixed(2)} strokeRect/frame`);

  ok("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} finally {
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} FAILED` : "\nall ok");
process.exit(fails.length ? 1 : 0);
