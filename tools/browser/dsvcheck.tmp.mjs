// Throwaway: the Dynamic Swing Anchored VWAP [Zeiierman] on the real replay chart.
//
// Seeds prefs before load (the row on, Modern VWAP off so the two lines can't be
// confused for each other), opens the Simulator, and asserts off the legend row —
// which quotes the flip count, the effective half-life and the bullish share —
// plus the usual "drew something, threw nothing" facts.
//
// Then it moves the two knobs that must move the numbers, and checks they do:
//
//   * a longer swing period must not find *more* flips than a shorter one;
//   * with the ATR adjustment on, the effective half-life the row quotes must
//     differ from the knob's nominal one (a session that never leaves its own
//     average ATR would be a legitimate exception, so this is checked as "moved
//     at some bias", not "moved at every bias").
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

const seed = async (page, patch) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate((p) => {
    const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
    vis.dynamicSwingVwap = true;
    // Off, so anything drawn in blue is not this indicator and the two legend
    // rows can't be read for each other.
    vis.modernVwap = false;
    vis.modernVwapSignals = false;
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
    const prefs = JSON.parse(localStorage.getItem("sim.prefs") || "{}");
    prefs.dynamicSwingVwap = {
      swingPeriod: 50,
      apt: 20,
      adaptApt: true,
      volBias: 1,
      bands: 2,
      flags: "labels",
      ...p,
    };
    localStorage.setItem("sim.prefs", JSON.stringify(prefs));
  }, patch);
};

/** Same seed, but with every other layer dark. An ink delta on the busy default
 *  chart is not attributable — a faint dashed ghost lands on pixels the profiles
 *  and the composite have already inked, and the sampled count does not move. */
const seedAlone = async (page, patch) => {
  await seed(page, patch);
  await page.evaluate(() => {
    const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
    // Every key by name, not Object.keys(vis): a layer absent from the stored
    // blob keeps its default, and several default to on. Iterating what happens
    // to be stored leaves a busy chart and makes any ink delta unattributable.
    for (const k of [
      "vwapGlobex", "vwapNy", "vwapWeekly", "vwapAnchored",
      "modernVwap", "modernVwapSignals",
      "developingProfileGlobex", "developingProfileNy", "developingProfileWeekly",
      "developingVpNy", "developingVpNyNodes", "initialBalance", "ibExtensions",
      "volumeProfile", "bigTrades", "cvd", "volRuler",
      "compositeProfile", "compositeNodes", "sweepBursts", "absorption", "replayTrades",
    ]) vis[k] = false;
    vis.dynamicSwingVwap = true;
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
  });
};

const dsvRow = async (page) =>
  (await page.locator(".chart-legend >> text=/Dynamic Swing VWAP/").first().textContent()).trim();

const num = (s, re) => {
  const m = s.match(re);
  return m ? +m[1] : null;
};
const read = (row) => ({
  row,
  swing: num(row, /swing (\d+)/),
  apt: num(row, /APT ([\d.]+)b/),
  flips: num(row, /(\d+)⚑/),
  bull: num(row, /(\d+)% bull/),
});

const { browser, page, errors } = await launch();
const report = {};

await seed(page, {});
await openChart(page, REPLAY);
const p = await probeChart(page);
report.canvas = { w: p.w, h: p.h, ink: p.ink };
report.base = read(await dsvRow(page));
await shot(page, "dsv-swing50");

// A longer swing window cannot find more flips than a shorter one on the same
// tape — the structure it is reading is strictly coarser.
for (const [key, swingPeriod] of [
  ["s10", 10],
  ["s120", 120],
]) {
  await seed(page, { swingPeriod });
  await openChart(page, REPLAY);
  report[key] = read(await dsvRow(page));
}
await shot(page, "dsv-swing120");

// The ATR adjustment: nominal vs effective half-life. With it off the row must
// quote the knob exactly; with it on, at some bias, it must not.
await seed(page, { adaptApt: false });
await openChart(page, REPLAY);
report.fixed = read(await dsvRow(page));
// 1 and his default of 10. The ratio is a smoothed ATR over its own smoothed
// average, so it lives near 1 and the rounded half-life often does not budge
// at a weak exponent — 10 is the setting that exercises the mechanism.
for (const bias of [1, 10]) {
  await seed(page, { adaptApt: true, volBias: bias });
  await openChart(page, REPLAY);
  report[`bias${bias}`] = read(await dsvRow(page));
}
await shot(page, "dsv-adaptive");

// Envelope scope is a drawing knob: putting rings on every frozen segment must
// add ink and must not move a single number the row quotes.
await seed(page, { bandScope: "all" });
await openChart(page, REPLAY);
const pAll = await probeChart(page);
report.bandsAll = { ...read(await dsvRow(page)), ink: pAll.ink };
await shot(page, "dsv-bands-all");

// The pending-anchor shadow: more ink, same numbers. It is a preview of a flip
// that has not happened, so it must not touch the flip count.
await seedAlone(page, { shadow: false });
await openChart(page, REPLAY);
report.aloneNoShadow = { ...read(await dsvRow(page)), ink: (await probeChart(page)).ink };
await seedAlone(page, { shadow: true });
await openChart(page, REPLAY);
report.shadow = { ...read(await dsvRow(page)), ink: (await probeChart(page)).ink };
await shot(page, "dsv-shadow");

// Flags off must still draw the line — they are a drawing knob, not the layer.
await seed(page, { flags: "off" });
await openChart(page, REPLAY);
const pOff = await probeChart(page);
report.flagsOff = { ...read(await dsvRow(page)), ink: pOff.ink };

const fail = [];
const ok = (cond, msg) => {
  if (!cond) fail.push(msg);
};
ok(report.canvas.ink > 0, "canvas drew nothing");
ok(report.base.flips !== null, "legend row quoted no flip count");
ok(report.base.flips > 0, `no flips found at swing 50 (row: ${report.base.row})`);
ok(report.base.bull !== null && report.base.bull >= 0 && report.base.bull <= 100,
   `bullish share out of range: ${report.base.bull}`);
ok(report.s10.flips >= report.s120.flips,
   `a 120-bar swing found more flips (${report.s120.flips}) than a 10-bar one (${report.s10.flips})`);
ok(report.fixed.apt === 20, `fixed tracking must quote the knob, got ${report.fixed.apt}`);
ok(Number.isInteger(report.bias10.apt), `half-life must be a whole number, got ${report.bias1.apt}`);
ok(report.bias1.apt !== 20 || report.bias10.apt !== 20,
   `ATR adjustment moved the half-life at neither bias (${report.bias1.apt}, ${report.bias10.apt})`);
ok(report.flagsOff.ink > 0, "flags off blanked the layer");
ok(/±2σ/.test(report.base.row), `envelope not reported on the row: ${report.base.row}`);
ok(/±2σ all/.test(report.bandsAll.row), `scope not reported on the row: ${report.bandsAll.row}`);
ok(report.bandsAll.flips === report.base.flips && report.bandsAll.bull === report.base.bull,
   "envelope scope changed the numbers — it is a drawing knob");
ok(report.bandsAll.ink > report.canvas.ink,
   `rings on every segment drew no extra ink (${report.canvas.ink} → ${report.bandsAll.ink})`);
ok(report.shadow.flips === report.aloneNoShadow.flips &&
   report.shadow.bull === report.aloneNoShadow.bull,
   "the shadow changed the numbers — it previews a flip that has not happened");
ok(report.shadow.ink > report.aloneNoShadow.ink,
   `the shadow drew no extra ink (${report.aloneNoShadow.ink} → ${report.shadow.ink})`);
ok(report.flagsOff.flips === report.base.flips,
   "flags off changed the flip count — it is a drawing knob");
// PostHog's CDN 404s intermittently and has nothing to do with the chart. Every
// other error still fails the run.
const ours = errors.filter((e) => !e.includes("posthog.com"));
ok(ours.length === 0, `page errors: ${ours.slice(0, 3).join(" | ")}`);

console.log(JSON.stringify({ report, errors, fail }, null, 2));
await browser.close();
process.exit(fail.length ? 1 : 0);
