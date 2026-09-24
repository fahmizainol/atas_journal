// Does the per-anchor band-fill knob actually take the wash down?
//
// Driven through the legend's "…" rather than by writing localStorage and
// reloading: the replay draws a *random* day on every load, so two loads are two
// different markets and no pixel comparison between them means anything. Turning
// the knob leaves the tape where it is and repaints, which is also the gesture a
// reader makes.
//
// The measurement is the canvas's mean channel value. A wash is a fraction of
// the distance between the anchor's hue and the surface, so taking it down moves
// every pixel under it back toward the background — monotonically, in the same
// direction, whatever the day happens to look like. Counting "non-background
// pixels" would not do: at 0.3 the wash is already inside the background
// tolerance, which is the point of that setting.
import { launch, openChart, shot, BASE } from "./lib.mjs";

const ROUTE = "/charts/replay";
const ANCHORS = [
  { key: "vwapNy", title: "NY VWAP" },
  { key: "vwapGlobex", title: "Globex VWAP" },
  { key: "vwapWeekly", title: "Weekly VWAP" },
];
const STEPS = ["full (default)", "dimmed", "faint", "off (lines only)"];

/** Mean r+g+b over the price canvas. */
async function meanInk(page) {
  return page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let sum = 0;
    for (let i = 0; i < d.length; i += 4) sum += d[i] + d[i + 1] + d[i + 2];
    return sum / (d.length / 4);
  });
}

async function setFill(page, key, label) {
  const dots = page.locator(`.chart-legend-item[data-ind-item='${key}'] .chart-legend-dots`);
  await dots.click();
  const sel = page.locator(
    `.chart-set-row:has(.chart-set-label:text-is("Band fill")) select`,
  );
  await sel.selectOption({ label });
  await page.waitForTimeout(350);
  await dots.click(); // close, so the panel never covers the canvas we sample
  await page.waitForTimeout(250);
}

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
const results = [];
try {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.removeItem("chart.vwapFill"));
  await openChart(page, ROUTE);

  // Every anchor's row must be up, or its wash isn't on the pane to measure.
  for (const a of ANCHORS) {
    const row = page.locator(`.chart-legend-item[data-ind-item='${a.key}'] .chart-legend-row`);
    if ((await row.count()) === 0) {
      results.push([`${a.key}: row present`, false]);
      continue;
    }
    if ((await row.getAttribute("class")).includes("off")) await row.click();
    results.push([`${a.key}: row present and on`, true]);
  }
  // Play a while before measuring. The replay opens at the bell, so the NY
  // anchor is one bar wide there and its wash covers no pixels — a real fact
  // about the pane, and one that would read here as "the knob does nothing".
  await page.keyboard.press("k");
  await page.waitForTimeout(8000);
  await page.keyboard.press("k");
  await page.waitForTimeout(600);
  await shot(page, "vwapfill-full");
  const nyPix = await page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    // Magenta-leaning: the NY anchor's hue is the only one on this pane with red
    // and blue both well over green.
    for (let i = 0; i < d.length; i += 4)
      if (d[i] > d[i + 1] + 20 && d[i + 2] > d[i + 1] + 20) n++;
    return n;
  });
  results.push([`NY has a wash to measure (${nyPix} magenta-ish px)`, nyPix > 500]);

  for (const a of ANCHORS) {
    const seen = [];
    for (const step of STEPS) {
      await setFill(page, a.key, step);
      seen.push(Math.round((await meanInk(page)) * 1000) / 1000);
    }
    // Strictly down at every step, then back up when the knob goes home.
    const down = seen.every((v, i) => i === 0 || v < seen[i - 1]);
    results.push([`${a.key}: mean ink falls at every step — ${seen.join(" > ")}`, down]);
    await setFill(page, a.key, "full (default)");
    const back = Math.round((await meanInk(page)) * 1000) / 1000;
    results.push([`${a.key}: full restores the wash (${back} vs ${seen[0]})`, back > seen[3]]);
  }

  // Off keeps the anchor drawn: the five lines are untouched, only the fill goes.
  for (const a of ANCHORS) await setFill(page, a.key, "off (lines only)");
  await page.waitForTimeout(300);
  await shot(page, "vwapfill-off");
  const lines = await page.evaluate(() =>
    [...document.querySelectorAll(".chart-legend-item[data-ind-item^='vwap'] .chart-legend-row")]
      .map((el) => `${el.textContent.slice(0, 18)}:${el.className.includes("off") ? "off" : "on"}`)
      .join(" "),
  );
  results.push([`rows still on with fills off — ${lines}`, !lines.includes(":off")]);

  const stored = await page.evaluate(() => localStorage.getItem("chart.vwapFill"));
  results.push([`persisted ${stored}`, JSON.parse(stored ?? "{}").ny === 0]);
  results.push([`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0]);
} finally {
  await browser.close();
}

let bad = 0;
for (const [label, ok] of results) {
  if (!ok) bad++;
  console.log(`${ok ? "  ok" : "FAIL"}  ${label}`);
}
console.log(bad ? `\n${bad} failed` : "\nall passed");
process.exit(bad ? 1 : 0);
