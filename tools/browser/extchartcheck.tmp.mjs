// Throwaway: the External Chart overlay (lib/externalChart) on the real replay
// chart, and on a journal day chart.
//
// The layer is pure canvas — hollow rectangles drawn by ExternalChartPrimitive —
// so nothing in the DOM says whether it rendered, rendered at the right period,
// or rendered at all. What is checkable is the ink it puts down in its own two
// hues, which nothing else on a stripped chart draws:
//
//   * on, at 15m, over a pinned session: blue/red box pixels appear;
//   * at 1h the same session must draw *fewer* boxes than at 15m — that is the
//     grouping actually grouping, and it is the one assertion that would catch
//     the period knob being wired to nothing;
//   * off: that ink goes back to zero;
//   * the legend row quotes the period it is drawing at.
//
// The day is pinned by writing the bookmark before load: the replay picks a
// random session otherwise, and a box count compared across two different days
// says nothing.
import { launch, openChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";
const SYMBOL = "NQZ5";
const DAY = "2025-12-17";
// Mid-session rather than the open: the replay resumes paused, and at clock zero
// there is one bar on the chart — nothing to group, so every count would be a
// count of an empty overlay. 18:00Z is 13:00 ET, well into RTH.
const [Y, M, D] = DAY.split("-").map(Number);
const CLOCK = Date.UTC(Y, M - 1, D, 18, 0, 0);

// The layer drawn in two hues nothing else on this chart owns. The authored
// bear is palette.red — which is also the default *down candle*, so probing the
// theme cut counts candles and reports a layer that is switched off. Driving the
// custom palette instead makes the ink attributable, and exercises that path.
const BULL = "#00ffff";
const BEAR = "#ff00ff";

/** The layer on and every other one dark. An ink delta on the default chart is
 *  not attributable — the profiles and the composite have already inked most of
 *  the pane, and a hollow box landing on those pixels does not move a count. */
const seed = async (page, ext) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([e, sym, day, clk]) => {
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: clk, attemptId: null, contextTicks: 0 }),
      );
      const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
      // Every key by name, not Object.keys: a layer absent from the stored blob
      // keeps its default, and several default to on.
      for (const k of [
        "vwapGlobex", "vwapNy", "vwapWeekly", "vwapAnchored",
        "modernVwap", "modernVwapSignals", "dynamicSwingVwap",
        "developingProfileGlobex", "developingProfileNy", "developingProfileWeekly",
        "developingVpNy", "developingVpNyNodes", "initialBalance", "ibExtensions",
        "volumeProfile", "volumeShelf", "volumeShelfBoxes", "bigTrades",
        "cvd", "cvdOsc", "volRuler",
        "compositeProfile", "compositeNodes", "sweepBursts", "absorption", "replayTrades",
      ]) vis[k] = false;
      vis.externalChart = e.on;
      localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
      localStorage.setItem("chart.externalChart", JSON.stringify(e.params));
    },
    [ext, SYMBOL, DAY, CLOCK],
  );
};

/** Count pixels in the layer's two hues, and how many distinct x-columns carry
 *  them. The column count is the useful one: a box is a rectangle, so the number
 *  of *vertical edges* is roughly twice the number of boxes on screen — which is
 *  what has to fall when the period gets coarser. */
const probeBoxes = (page, hues) =>
  page.evaluate((hh) => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return { error: "no canvas" };
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const near = (r, g, b, h) =>
      Math.abs(r - h[0]) < 40 && Math.abs(g - h[1]) < 40 && Math.abs(b - h[2]) < 40;
    let px = 0;
    const cols = new Set();
    // A vertical edge is a column with a tall run of the hue in it. Counting
    // those rather than every inked column keeps the horizontal top/bottom rules
    // of the box out of the tally, which is what makes the number track boxes
    // rather than box *width*.
    for (let x = 0; x < c.width; x++) {
      let run = 0;
      for (let y = 0; y < c.height; y++) {
        const i = (y * c.width + x) * 4;
        const hit = hh.some((h) => near(d[i], d[i + 1], d[i + 2], h));
        if (hit) {
          px++;
          run++;
        }
      }
      if (run > 12) cols.add(x);
    }
    return { px, edges: cols.size };
  }, hues);

const HUES = [
  [0, 255, 255],
  [255, 0, 255],
];

const results = [];
const check = (name, ok, detail = "") => {
  results.push([name, ok]);
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
};

const P = (period) => ({
  period,
  grid: true,
  fill: false,
  above: false,
  palette: "custom",
  bull: BULL,
  bear: BEAR,
});

const { browser, page } = await launch({ headed: process.argv.includes("--headed") });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

try {
  // --- off: the baseline ----------------------------------------------------
  await seed(page, { on: false, params: P(900) });
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  const off = await probeBoxes(page, HUES);
  await shot(page, "extchart-off");

  // --- on, at 15m -----------------------------------------------------------
  await seed(page, { on: true, params: P(900) });
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  const m15 = await probeBoxes(page, HUES);
  await shot(page, "extchart-15m");
  const row15 = (
    await page.locator(".chart-legend >> text=/External chart/").first().textContent()
  ).trim();

  // --- on, at 1h ------------------------------------------------------------
  await seed(page, { on: true, params: P(3600) });
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  const h1 = await probeBoxes(page, HUES);
  await shot(page, "extchart-1h");
  const row1h = (
    await page.locator(".chart-legend >> text=/External chart/").first().textContent()
  ).trim();

  console.log(
    `\noff ${off.px}px/${off.edges}col · 15m ${m15.px}px/${m15.edges}col · 1h ${h1.px}px/${h1.edges}col\n`,
  );

  check("off draws none of the layer's ink", off.px < 200, `${off.px}px`);
  check("on at 15m draws the boxes", m15.px > 1000 && m15.edges >= 4, `${m15.px}px / ${m15.edges}col`);
  check("1h groups coarser than 15m", h1.edges < m15.edges, `${h1.edges} vs ${m15.edges} columns`);
  check("1h still draws something", h1.px > 300, `${h1.px}px`);
  check("the row quotes 15m", /External chart · 15m/.test(row15), row15);
  check("the row quotes 1h", /External chart · 1h/.test(row1h), row1h);
  check("nothing threw", errors.length === 0, errors[0] ?? "");
} finally {
  await page
    .evaluate(() => {
      localStorage.removeItem("sim.resume.replay");
      localStorage.removeItem("chart.externalChart");
    })
    .catch(() => {});
  await browser.close();
}

const bad = results.filter(([, ok]) => !ok).length;
console.log(`\n${results.length - bad}/${results.length} passed`);
process.exitCode = bad ? 1 : 0;
