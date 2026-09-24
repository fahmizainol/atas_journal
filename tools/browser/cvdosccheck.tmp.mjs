// Throwaway: the CVD oscillator (lib/cvdOsc) on the real replay chart.
//
// The layer makes three claims that only a browser can settle:
//
//   * it owns a *new pane* — it is built and torn down, not hidden, so switching
//     it on must add a pane and switching it off must give the height back;
//   * it draws its divergences **twice**, once on its own pane and once over the
//     candles, which is the part a unit test cannot see at all;
//   * the window knobs are a redraw, so turning one must move the row's numbers
//     without the chart losing anything.
//
// Everything else off, so a pane count or an ink delta is attributable.
//
//   node cvdosccheck.tmp.mjs
//   node cvdosccheck.tmp.mjs --headed
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

/** Every layer dark but this one. By name rather than Object.keys: a key absent
 *  from the stored blob keeps its default, and several default to on. */
const seed = async (page, { on = true, params = null } = {}) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ({ on, params }) => {
      const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
      for (const k of [
        "vwapGlobex", "vwapNy", "vwapWeekly", "vwapAnchored",
        "modernVwap", "modernVwapSignals", "dynamicSwingVwap",
        "developingProfileGlobex", "developingProfileNy", "developingProfileWeekly",
        "developingVpNy", "developingVpNyNodes", "initialBalance", "ibExtensions",
        "volumeProfile", "bigTrades", "volRuler",
        "compositeProfile", "compositeNodes", "sweepBursts", "absorption", "replayTrades",
      ]) vis[k] = false;
      // The cumulative pane stays down throughout: it is the layer this one is
      // most easily confused for, and it owns a pane of its own.
      vis.cvd = false;
      vis.cvdOsc = on;
      localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
      if (params) localStorage.setItem("chart.cvdOsc", JSON.stringify(params));
      else localStorage.removeItem("chart.cvdOsc");
    },
    { on, params },
  );
};

/** How many panes the chart is showing — the fact this layer's "built, not
 *  hidden" rule is about.
 *
 *  Rows are not panes: lightweight-charts puts a resize separator between every
 *  pair of panes, and those rows carry no canvas. So the panes are the rows that
 *  *do*, less the time axis at the bottom. */
const paneCount = (page) =>
  page.evaluate(() => {
    const t = document.querySelector("table");
    if (!t) return 0;
    const withCanvas = [...t.querySelectorAll("tr")].filter((r) => r.querySelector("canvas"));
    return Math.max(0, withCanvas.length - 1);
  });

/**
 * Ink on the price pane's **overlay** canvas.
 *
 * A pane row holds four canvases, in two stacked pairs: the chart cell's series
 * canvas and its overlay, then the right price scale's two. Primitives with
 * `zOrder: "top"` — which is what the divergence marks are — draw on the *chart
 * cell's* overlay, which is otherwise transparent.
 *
 * So neither "the biggest canvas" (probeChart's rule — that is the series
 * canvas, which cannot see a primitive at all) nor "the last canvas in the row"
 * (the price scale's overlay, permanently empty) finds it. Both were tried; the
 * first read a byte-identical count across two runs with visibly different marks
 * on them, the second read a flat zero. The one that matters is the second of
 * the two *wide* canvases, and counting its non-transparent pixels measures the
 * primitives alone, with the candles subtracted by construction.
 */
const priceMarkInk = (page) =>
  page.evaluate(() => {
    const row = [...document.querySelectorAll("table tr")].find((r) => r.querySelector("canvas"));
    if (!row) return -1;
    const cs = [...row.querySelectorAll("canvas")];
    const wide = Math.max(...cs.map((c) => c.width));
    const c = cs.filter((x) => x.width === wide)[1];
    if (!c) return -1;
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4 * 2) if (d[i] > 8) n++;
    return n;
  });

const oscRow = async (page) => {
  const el = page.locator(".chart-legend >> text=/CVD oscillator/").first();
  return (await el.count()) ? (await el.textContent()).trim() : "";
};

const num = (s, re) => {
  const m = s.match(re);
  return m ? +m[1] : null;
};
const read = (row) => ({
  row,
  period: num(row, /(?:periodic|EMA) (\d+)/),
  fractal: num(row, /fractal (\d+)/),
  divs: num(row, /(\d+) divergences?/),
  ema: /· EMA /.test(row),
});

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
const report = {};

// --- off, as the baseline ----------------------------------------------------
await seed(page, { on: false });
await openChart(page, REPLAY);
report.off = {
  panes: await paneCount(page),
  markInk: await priceMarkInk(page),
  ...read(await oscRow(page)),
};

// --- on, at the defaults -----------------------------------------------------
await seed(page, { on: true });
await openChart(page, REPLAY);
report.on = {
  panes: await paneCount(page),
  canvasInk: (await probeChart(page)).ink,
  markInk: await priceMarkInk(page),
  ...read(await oscRow(page)),
};
await shot(page, "cvdosc-default");

// --- the price-pane marks ----------------------------------------------------
// Same layer, fractal 1 — a looser fractal confirms more pivots, so it can only
// find at least as many divergences, and each one is drawn over the candles as
// well as on its own pane. Same pane layout as the run above, so the overlay ink
// is comparable and the only thing that changed is how many marks there are.
await seed(page, { on: true, params: { mode: "periodic", period: 21, fractalN: 1 } });
await openChart(page, REPLAY);
report.fractal1 = {
  panes: await paneCount(page),
  markInk: await priceMarkInk(page),
  ...read(await oscRow(page)),
};
await shot(page, "cvdosc-fractal1");

// --- the window knobs --------------------------------------------------------
// EMA mode and a different period must both be reported, and neither may cost a
// pane: they are a redraw of the series that is already there.
await seed(page, { on: true, params: { mode: "ema", period: 55, fractalN: 2 } });
await openChart(page, REPLAY);
report.ema55 = { panes: await paneCount(page), ...read(await oscRow(page)) };
await shot(page, "cvdosc-ema55");

const fail = [];
const ok = (cond, msg) => {
  if (!cond) fail.push(msg);
};
// The row is listed either way — it is the layer's switch, and a layer you can't
// see is a layer you can't turn on. What must not appear with it off is the
// *reading*: a divergence count nobody can look at is a signal, not a legend.
ok(report.off.row !== "", "no CVD oscillator row to switch the layer on with");
ok(report.off.divs === null, `the row reported a reading with the layer off: ${report.off.row}`);
ok(report.on.row !== "", "no CVD oscillator row in the legend with the layer on");
ok(
  report.on.panes === report.off.panes + 1,
  `pane count ${report.off.panes} → ${report.on.panes}; the layer must add exactly one`,
);
ok(report.on.period === 21 && report.on.fractal === 2, `defaults not quoted: ${report.on.row}`);
ok(report.on.divs !== null && report.on.divs > 0, `no divergences found: ${report.on.row}`);
ok(
  report.fractal1.divs >= report.on.divs,
  `fractal 1 found fewer divergences (${report.fractal1.divs}) than fractal 2 (${report.on.divs})`,
);
ok(report.on.canvasInk > 0, "the price pane drew nothing");
// The candles are the same candles in both runs and every other layer is dark,
// so the overlay ink is the divergence marks and nothing else. More divergences
// found must mean more of them drawn over the price.
ok(
  report.on.markInk > report.off.markInk,
  `no marks over the candles with the layer on (${report.off.markInk} → ${report.on.markInk})`,
);
ok(
  report.fractal1.markInk > report.on.markInk,
  `28 divergences inked the price pane no more than 17 (${report.on.markInk} → ${report.fractal1.markInk})`,
);
ok(report.ema55.ema && report.ema55.period === 55, `EMA 55 not quoted: ${report.ema55.row}`);
ok(
  report.ema55.panes === report.on.panes,
  `a knob turn changed the pane count (${report.on.panes} → ${report.ema55.panes})`,
);
ok(errors.length === 0, `console errors: ${errors.slice(0, 3).join(" | ")}`);

console.log(JSON.stringify(report, null, 2));
for (const f of fail) console.log(`  ✗ ${f}`);
console.log(fail.length ? `\nFAIL (${fail.length})` : "\nPASS");
await browser.close();
process.exit(fail.length ? 1 : 0);
