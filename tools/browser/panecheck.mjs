// The split pane, driven the way a user drives it.
//
// Turning it on is a button in the top bar, so this presses that button rather
// than a query param — the thing being checked is that the pane appears, draws a
// *different* bucketing from the trading pane, resizes, survives a reload, and
// keeps its own indicator preferences instead of the trading pane's.
//
// RUN IT AGAINST BOTH SERVERS. The dev server is the one with StrictMode, whose
// mount/unmount/remount is what catches a chart holding refs to series that died
// with a previous chart — a crash a production run cannot see. The production
// build is the one whose *timings* mean anything. The first version of this file
// only ever ran against production and passed a page that crashed on click.
//
//   node panecheck.mjs                              # dev server, :5173
//   APP_URL=http://localhost:4300 node panecheck.mjs # a production build
//   node panecheck.mjs --headed
import { launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const paneCount = () => page.locator(".sim-pane").count();
/** The x-axis labels a pane is showing, which is how two bucketings tell
 *  themselves apart: a 1m pane and a 5m pane over one tape never label the same
 *  span of clock in the same places. */
const axisOf = (i) =>
  page.evaluate((idx) => {
    const pane = document.querySelectorAll(".sim-pane")[idx];
    if (!pane) return null;
    const c = [...pane.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return null;
    const ctx = c.getContext("2d");
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let ink = 0;
    // A sampled ink count says "is anything drawn here". It is far too coarse to
    // say "did this repaint" — a 1x replay moves the forming bar by a tick, and
    // every 37th pixel can easily be identical either side of that. So carry a
    // hash over every pixel as well, which changes if any of them did.
    let hash = 0;
    for (let i = 4; i < d.length; i += 4 * 37) {
      if (d[i] !== d[0] || d[i + 1] !== d[1] || d[i + 2] !== d[2]) ink++;
    }
    for (let i = 0; i < d.length; i += 4) {
      hash = (hash * 31 + d[i] + d[i + 1] * 3 + d[i + 2] * 7) | 0;
    }
    return { ink, hash, w: Math.round(c.getBoundingClientRect().width) };
  }, i);

try {
  // Start from a known state: no stored pane preference of any kind.
  await openChart(page, "/charts/replay");
  await page.evaluate(() => {
    localStorage.removeItem("sim.prefs");
    localStorage.removeItem("chart.indicatorVisibility.b");
    localStorage.removeItem("chart.legendOpen.b");
  });
  await openChart(page, "/charts/replay");

  check("starts as one pane", (await paneCount()) === 1, `${await paneCount()} pane(s)`);

  const toggle = page.locator(".sim-pane-toggle");
  await toggle.click();
  await page.waitForTimeout(2500);
  check("the toggle adds a pane", (await paneCount()) === 2, `${await paneCount()} pane(s)`);

  const a = await axisOf(0);
  const b = await axisOf(1);
  check("both panes drew", !!a && !!b && a.ink > 300 && b.ink > 300, `ink ${a?.ink} / ${b?.ink}`);
  check(
    "the trading pane keeps the larger share",
    !!a && !!b && a.w > b.w,
    `${a?.w}px vs ${b?.w}px`,
  );

  // The context pane's own bucketing picker.
  // The shared TimeframeControl: the resident buttons plus the ⋯ holding the
  // rest, so every bucketing is reachable on the pane itself.
  const tfBtns = page.locator(".sim-pane-tf button");
  check(
    "the context pane has its own timeframe picker",
    (await tfBtns.count()) >= 3,
    `${await tfBtns.count()} buttons`,
  );
  const pressed = await page.locator('.sim-pane-tf button.active').textContent();
  check("it defaults to 5m", pressed?.trim() === "5m", `showing ${pressed?.trim()}`);
  await page.locator('.sim-pane-tf button:text-is("15m")').click();
  await page.waitForTimeout(1500);
  const after = await page.locator('.sim-pane-tf button.active').textContent();
  check("it re-buckets on click", after?.trim() === "15m", `showing ${after?.trim()}`);

  // The divider.
  const div = await page.locator(".sim-pane-divider").boundingBox();
  const box = await page.locator(".sim-chart").boundingBox();
  await page.mouse.move(div.x + div.width / 2, div.y + div.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.35, div.y + div.height / 2, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const a2 = await axisOf(0);
  check("the divider resizes", a2.w < a.w, `${a.w}px → ${a2.w}px`);

  // Both choices are settings, so they survive a reload.
  await openChart(page, "/charts/replay");
  const a3 = await axisOf(0);
  const kept = await page.locator('.sim-pane-tf button.active').textContent();
  check(
    "pane, size and bucketing all survive a reload",
    (await paneCount()) === 2 && Math.abs(a3.w - a2.w) < 30 && kept?.trim() === "15m",
    `${await paneCount()} panes, ${a3.w}px, ${kept?.trim()}`,
  );

  // Per-pane indicator preferences: hiding a layer on the context pane must not
  // hide it on the trading pane.
  const stores = await page.evaluate(() => ({
    shared: localStorage.getItem("chart.indicatorVisibility"),
    pane: localStorage.getItem("chart.indicatorVisibility.b"),
  }));
  check(
    "the context pane has not written over the shared indicator prefs",
    stores.pane === null || stores.shared !== stores.pane,
    stores.pane === null ? "no pane blob written yet" : "separate blobs",
  );

  // Read-only: the context pane draws no order dock.
  const docks = await page.evaluate(
    () => document.querySelectorAll(".sim-pane .sim-quick-btn").length,
  );
  const inSecond = await page.evaluate(
    () => document.querySelectorAll(".sim-pane:nth-of-type(3) .sim-quick-btn").length,
  );
  check("the order dock is on the trading pane only", docks > 0 && inSecond === 0, `${docks} buttons, ${inSecond} in the context pane`);

  // The gate, at 1× — the case that matters and the one the first version of
  // this check missed by playing at 30×. A 15m pane closes a bar every fifteen
  // minutes of session time, so if the pane only repainted on bar close it would
  // be visibly frozen here for a quarter of an hour of wall clock. Two seconds
  // is far too short to close one and far longer than the 200ms repaint floor,
  // so this passes only if the floor is doing its job.
  await page.selectOption(".sim-transport select", "1");
  const before = await axisOf(1);
  await page.keyboard.press("k");
  await page.waitForTimeout(3000);
  await page.keyboard.press("k");
  const after2 = await axisOf(1);
  check(
    "the pane repaints at 1x without waiting for a bar close",
    before.hash !== after2.hash,
    `hash ${before.hash} → ${after2.hash}`,
  );

  await shot(page, "panecheck");

  await toggle.click();
  await page.waitForTimeout(600);
  check("the toggle takes it away again", (await paneCount()) === 1, `${await paneCount()} pane(s)`);
  check("no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
