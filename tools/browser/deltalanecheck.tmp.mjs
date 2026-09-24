// Throwaway: the delta lane's three reading knobs (lib/deltaFlow) on the real
// replay chart.
//
// The unit tests in tests/test_delta_flow.py settle the arithmetic. What only a
// browser can settle is that the arithmetic reaches the canvas:
//
//   * the lane is drawn from `LaneReading.frac` now, so switching the scale has
//     to visibly repaint it — a wiring mistake anywhere between the knob and
//     ProfileRenderer.draw shows up as two identical pictures;
//   * `net` is the lane as it was, so it has to be what the defaults give you;
//   * flags and verdicts add ink of their own, in the same strip;
//   * and the panel has to offer four rows once the lane is up, since three of
//     them are emitted conditionally.
//
// Two things this had to be built around, both of which produced a green run
// that measured nothing:
//
//   * the profile is a `zOrder: "bottom"` primitive, so it draws on the *series*
//     canvas — which has an opaque background. Counting alpha there returns the
//     whole strip on every run. The lane has to be found by colour instead:
//     pixels that are not the background.
//   * reloading between samples lets the replay's playhead move, and then two
//     lanes differ because the market did, not because the knob did. So the
//     knobs are turned *in one page session*, through the panel's own selects,
//     and every sample is of the same bars.
//
//   node deltalanecheck.tmp.mjs
//   node deltalanecheck.tmp.mjs --headed
import { launch, openChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

/** Every layer dark but the volume profile, lane on, knobs at their defaults.
 *
 *  The sitting is left alone: `sim.resume.*` is what pins the replay to a day,
 *  and clearing it would hand the run a different market. */
const seed = async (page) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
    for (const k of [
      "vwapGlobex", "vwapNy", "vwapWeekly", "vwapAnchored",
      "modernVwap", "modernVwapSignals", "dynamicSwingVwap",
      "developingProfileGlobex", "developingProfileNy", "developingProfileWeekly",
      "developingVpNy", "developingVpNyNodes", "initialBalance", "ibExtensions",
      "bigTrades", "volRuler", "compositeProfile", "compositeNodes",
      "sweepBursts", "absorption", "replayTrades", "cvd", "cvdOsc",
    ]) vis[k] = false;
    vis.volumeProfile = true;
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
    localStorage.removeItem("chart.profileDelta");
    localStorage.removeItem("chart.deltaLane");
  });
};

/**
 * The delta lane's strip, read by colour.
 *
 * Geometry is VolumeProfilePrimitive's: the volume rows span the rightmost
 * MAX_WIDTH_FRAC (0.11) of the pane and the delta lane hangs a gutter to their
 * left, DELTA_WIDTH_FRAC (0.6) as wide. [0.80W, 0.885W] covers the lane and the
 * verdict column beside it while stopping clear of the volume histogram, whose
 * bars are identical in every sample here and would only dilute the difference.
 *
 * The candles in that strip are ink too, and they are *supposed* to be constant:
 * every sample is the same bars, so anything that moves is the lane. `sig` is a
 * per-scanline count, which separates two lanes carrying the same total ink in
 * different rows — a count alone would call those equal.
 */
const laneInk = (page) =>
  page.evaluate(() => {
    const row = [...document.querySelectorAll("table tr")].find((r) => r.querySelector("canvas"));
    if (!row) return { n: -1, sig: "" };
    const cs = [...row.querySelectorAll("canvas")];
    const wide = Math.max(...cs.map((c) => c.width));
    const c = cs.find((x) => x.width === wide);
    if (!c) return { n: -1, sig: "" };
    const x0 = Math.floor(c.width * 0.8);
    const x1 = Math.floor(c.width * 0.885);
    const w = x1 - x0;
    const d = c.getContext("2d").getImageData(x0, 0, w, c.height).data;

    // The background is whatever colour holds the most pixels in the strip —
    // found rather than hard-coded, so this survives the light surface.
    const tally = new Map();
    for (let i = 0; i < d.length; i += 4) {
      const k = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2];
      tally.set(k, (tally.get(k) ?? 0) + 1);
    }
    let bg = 0;
    let most = -1;
    for (const [k, v] of tally) if (v > most) { most = v; bg = k; }

    let n = 0;
    const sig = [];
    for (let y = 0; y < c.height; y++) {
      let inRow = 0;
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        if (((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]) !== bg) inRow++;
      }
      n += inRow;
      if (inRow > 0) sig.push(`${y}:${inRow}`);
    }
    return { n, sig: sig.join(",") };
  });

const profileRow = async (page) => {
  const el = page.locator(".chart-legend >> text=/Volume profile/").first();
  return (await el.count()) ? (await el.textContent()).trim() : "";
};

const dots = (page) => page.locator("button[title='Settings for Volume profile']").first();

const openPanel = async (page) => {
  await dots(page).click();
  await page.waitForTimeout(250);
};
const closePanel = async (page) => {
  await dots(page).click();
  await page.waitForTimeout(150);
};

/** Set one knob through the panel's own select, by its row label. */
const setKnob = async (page, label, value) => {
  const sel = page.locator(`.chart-set-row:has(.chart-set-label:text-is("${label}")) select`);
  await sel.selectOption(value);
  await page.waitForTimeout(400);
};

const fieldLabels = (page) =>
  page.locator(".chart-set-row .chart-set-label").allTextContents();

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
const report = {};

await seed(page);
await openChart(page, REPLAY);

// --- lane off (the default), as the baseline ---------------------------------
const off = await laneInk(page);
report.off = { n: off.n, row: await profileRow(page) };

// --- switch the lane on through the panel ------------------------------------
await openPanel(page);
report.fieldsWhileOff = await fieldLabels(page);
await setKnob(page, "Delta lane", "1");
report.fieldsWhileOn = await fieldLabels(page);
await closePanel(page);

const net = await laneInk(page);
report.net = { n: net.n, row: await profileRow(page) };
await shot(page, "deltalane-net");

// --- same bars, imbalance scale ----------------------------------------------
await openPanel(page);
await setKnob(page, "Lane scale", "imbalance");
await closePanel(page);
const imb = await laneInk(page);
report.imbalance = { n: imb.n, row: await profileRow(page) };
await shot(page, "deltalane-imbalance");

// --- same bars again, plus flags and verdicts --------------------------------
await openPanel(page);
await setKnob(page, "Flag rows", "2");
await setKnob(page, "Classify", "1");
await closePanel(page);
const marked = await laneInk(page);
report.marked = { n: marked.n, row: await profileRow(page) };
await shot(page, "deltalane-marked");

// --- same bars, lane re-cut to the trailing 15 minutes -----------------------
await openPanel(page);
await setKnob(page, "Lane window", "m15");
await closePanel(page);
const m15 = await laneInk(page);
report.m15 = { n: m15.n, row: await profileRow(page) };
await shot(page, "deltalane-m15");

// --- and to each row's latest visit ------------------------------------------
await openPanel(page);
await setKnob(page, "Lane window", "visit");
await closePanel(page);
const visit = await laneInk(page);
report.visit = { n: visit.n, row: await profileRow(page) };
await shot(page, "deltalane-visit");

const rows = (s) => s.split(",").filter(Boolean).length;
const checks = [
  [`lane off draws less than lane on (${off.n} < ${net.n})`, off.n < net.n],
  [`the lane is substantial (${net.n - off.n} px of it)`, net.n - off.n > 500],
  ["off/on are different pictures", off.sig !== net.sig],
  [`imbalance repaints the lane (${net.n} → ${imb.n})`, imb.sig !== net.sig],
  [`imbalance occupies the same rows (${rows(net.sig)} vs ${rows(imb.sig)})`,
    Math.abs(rows(net.sig) - rows(imb.sig)) <= 2],
  [`flags + verdicts add ink (${imb.n} → ${marked.n})`, marked.n > imb.n],
  [`the 15m window repaints the lane (${marked.n} → ${m15.n})`, m15.sig !== marked.sig],
  [`the visit window repaints it again (${m15.n} → ${visit.n})`, visit.sig !== m15.sig],
  [`one field while off (${report.fieldsWhileOff.join("/")})`,
    report.fieldsWhileOff.length === 1],
  [`five fields while on (${report.fieldsWhileOn.join("/")})`,
    report.fieldsWhileOn.length === 5],
  [`legend names the scale (${report.imbalance.row})`,
    /delta:imbalance/.test(report.imbalance.row)],
  [`legend names the window (${report.m15.row} / ${report.visit.row})`,
    /delta:imbalance@15m/.test(report.m15.row) && /delta:visit/.test(report.visit.row)],
  [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
];

console.log(JSON.stringify(report, null, 2));
for (const [what, ok] of checks) console.log(`  ${ok ? "ok  " : "FAIL"}  ${what}`);
await browser.close();
process.exit(checks.every(([, ok]) => ok) ? 0 : 1);
