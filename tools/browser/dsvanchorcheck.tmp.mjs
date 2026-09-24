// Throwaway: does the Dynamic Swing VWAP's anchor timeframe make two panes agree?
//
// Two panes on one session, one drawn at 1m and one at 15m, both with the row on.
// With the knob unset they hunt swings on their own bars and must disagree about
// how many times the structure flipped. Set to 15m, the 1m pane must find exactly
// what the 15m pane finds — and say so, because the row only prints '@15m' when
// the regrouping actually happened, which on the 15m pane itself it cannot.
//
// One page load per setting, so both panes are reading the same random day and
// the comparison is between bucketings rather than between sessions.
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

const seed = async (page, anchorTf) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate((anchor) => {
    // Both panes: visibility and the legend's own expansion are per pane (the
    // `prefsPane` suffix), and a secondary pane starts collapsed — with no rows
    // rendered there is nothing to read the flip count off.
    for (const pane of ["", ".p1"]) {
      const vis = JSON.parse(localStorage.getItem(`chart.indicatorVisibility${pane}`) || "{}");
      vis.dynamicSwingVwap = true;
      vis.modernVwap = false;
      vis.modernVwapSignals = false;
      localStorage.setItem(`chart.indicatorVisibility${pane}`, JSON.stringify(vis));
      localStorage.setItem(`chart.legendOpen${pane}`, "1");
    }
    const prefs = JSON.parse(localStorage.getItem("sim.prefs") || "{}");
    // Two panes side by side: pane 0 on 1m, pane 1 on 15m.
    prefs.layout = "col2";
    prefs.timeframe = "1m";
    prefs.paneTfs = ["1m", "15m", "15m", "1h"];
    prefs.dynamicSwingVwap = {
      swingPeriod: 20,
      anchorTf: anchor,
      apt: 20,
      adaptApt: false,
      volBias: 10,
      bands: 0,
      flags: "labels",
    };
    localStorage.setItem("sim.prefs", JSON.stringify(prefs));
  }, anchorTf);
};

/** The DSV row of pane `i`, as text. */
const dsvRow = async (page, i) =>
  (
    await page
      .locator(".sim-pane")
      .nth(i)
      .locator("[data-ind-item='dynamicSwingVwap']")
      .first()
      .textContent()
  ).trim();

const num = (s, re) => {
  const m = s.match(re);
  return m ? +m[1] : null;
};
const read = (row) => ({
  row,
  flips: num(row, /(\d+)⚑/),
  bull: num(row, /(\d+)% bull/),
  anchored: /@\d+[smh]/.test(row),
});

const { browser, page, errors } = await launch();
const report = {};

// A throwaway load first. The replay draws a *random* day and then bookmarks the
// sitting, so the first load of a fresh profile picks a market and every load
// after it resumes that one — and without this the three settings below would be
// measured on two different sessions and compared as though they were one.
await seed(page, "");
await openChart(page, REPLAY);

// Both panes on their own bars.
await seed(page, "");
await openChart(page, REPLAY);
report.canvas = await probeChart(page).then((p) => ({ w: p.w, h: p.h, ink: p.ink }));
report.off = { p1m: read(await dsvRow(page, 0)), p15m: read(await dsvRow(page, 1)) };
await shot(page, "dsvanchor-off");

// Both panes anchored to 15m.
await seed(page, "15m");
await openChart(page, REPLAY);
report.on = { p1m: read(await dsvRow(page, 0)), p15m: read(await dsvRow(page, 1)) };
await shot(page, "dsvanchor-15m");

// And to 5m, which is coarser than one pane and finer than the other — the 15m
// pane must be untouched by it while the 1m pane moves onto it.
await seed(page, "5m");
await openChart(page, REPLAY);
report.on5 = { p1m: read(await dsvRow(page, 0)), p15m: read(await dsvRow(page, 1)) };
await shot(page, "dsvanchor-5m");

const fail = [];
const ok = (cond, msg) => {
  if (!cond) fail.push(msg);
};

ok(report.canvas.ink > 0, "canvas drew nothing");
for (const [k, r] of Object.entries(report)) {
  if (k === "canvas") continue;
  ok(r.p1m.flips !== null && r.p15m.flips !== null, `${k}: a pane quoted no flip count`);
}

// Unset: two bucketings, two structures. A 1m pane at swing 20 reads 20 minutes
// of tape and a 15m pane reads five hours, so the fast pane must find more.
ok(
  report.off.p1m.flips > report.off.p15m.flips,
  `unset: 1m found ${report.off.p1m.flips} flips, 15m found ${report.off.p15m.flips} — expected the fast pane to find more`,
);
ok(!report.off.p1m.anchored && !report.off.p15m.anchored, "unset: a row claimed an anchor");

// Anchored to 15m: the same structure on both, and the row says so on the pane
// that was actually regrouped.
ok(
  report.on.p1m.flips === report.on.p15m.flips,
  `anchored 15m: 1m pane ${report.on.p1m.flips} flips vs 15m pane ${report.on.p15m.flips}`,
);
// Within a point or two, not to the point: the share is over *drawn* bars, and a
// flip that lands inside a 15m bar is a whole bar there and a fraction of one on
// the 1m pane. Same structure, counted on different rulers.
ok(
  Math.abs(report.on.p1m.bull - report.on.p15m.bull) <= 2,
  `anchored 15m: bullish share ${report.on.p1m.bull}% vs ${report.on.p15m.bull}%`,
);
ok(report.on.p1m.anchored, "anchored 15m: the 1m row did not name the grid");
ok(!report.on.p15m.anchored, "anchored 15m: the 15m row named a grid it was already on");
ok(
  report.on.p15m.flips === report.off.p15m.flips,
  `anchored 15m: the 15m pane's own structure moved (${report.off.p15m.flips} -> ${report.on.p15m.flips})`,
);

// Anchored to 5m: coarser than the 1m pane, finer than the 15m one.
ok(report.on5.p1m.anchored, "anchored 5m: the 1m row did not name the grid");
ok(!report.on5.p15m.anchored, "anchored 5m: the 15m pane claimed a finer grid");
ok(
  report.on5.p15m.flips === report.off.p15m.flips,
  `anchored 5m: a finer grid moved the 15m pane (${report.off.p15m.flips} -> ${report.on5.p15m.flips})`,
);
// And the grids order the way the tape does: a 5m structure is finer than a 15m
// one, so it cannot find fewer flips.
ok(
  report.on5.p1m.flips >= report.on.p1m.flips,
  `anchored 5m found ${report.on5.p1m.flips} flips, fewer than 15m's ${report.on.p1m.flips}`,
);

console.log(JSON.stringify(report, null, 2));
if (errors.length) console.log("console errors:", errors);
ok(errors.length === 0, `console errors: ${errors.join(" | ")}`);
await browser.close();

if (fail.length) {
  console.log("\nFAIL\n" + fail.map((f) => ` - ${f}`).join("\n"));
  process.exit(1);
}
console.log("\nok");
