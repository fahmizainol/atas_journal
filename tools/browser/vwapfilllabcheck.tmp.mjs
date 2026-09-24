// The same knob on the *other* chart component. ReplayChart and CandlestickChart
// draw the three fixed anchors from one preference, so a fill turned down in the
// Charts workspace has to arrive turned down in the Interactions Lab — and the
// Lab's own copy of the panel has to move its own bands.
//
// The Lab is a static chart: no tape to play, the whole session is drawn at
// once, so every anchor has its full span of points from the first frame.
import { launch, shot, BASE } from "./lib.mjs";

const STEPS = ["full (default)", "dimmed", "faint", "off (lines only)"];

const meanInk = (page) =>
  page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let sum = 0;
    for (let i = 0; i < d.length; i += 4) sum += d[i] + d[i + 1] + d[i + 2];
    return sum / (d.length / 4);
  });

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
const results = [];
try {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  // Arrive carrying a choice made elsewhere: weekly already off, the rest full.
  await page.evaluate(() =>
    localStorage.setItem("chart.vwapFill", JSON.stringify({ globex: 1, ny: 1, weekly: 0 })),
  );
  await page.goto(`${BASE}/interactions`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("tbody tr", { timeout: 60000 });
  await page.locator("tbody tr").first().click();
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(3000);
  await shot(page, "vwapfilllab-1-weekly-off");

  const dots = (key) =>
    page.locator(`.chart-legend-item[data-ind-item='${key}'] .chart-legend-dots`);
  const sel = page.locator(`.chart-set-row:has(.chart-set-label:text-is("Band fill")) select`);

  // The stored choice is what the Lab's panel reports — one preference, not two.
  await dots("vwapWeekly").click();
  results.push([
    `weekly panel opens on the stored choice`,
    (await sel.inputValue()) === "0",
  ]);
  const withWeeklyOff = await meanInk(page);
  await dots("vwapWeekly").click();

  // And moving it here moves this chart's own band.
  const seen = [];
  for (const step of [...STEPS].reverse()) {
    await dots("vwapWeekly").click();
    await sel.selectOption({ label: step });
    await page.waitForTimeout(300);
    await dots("vwapWeekly").click();
    await page.waitForTimeout(200);
    seen.push(Math.round((await meanInk(page)) * 1000) / 1000);
  }
  results.push([
    `weekly fill rises as the knob goes up — ${seen.join(" < ")}`,
    seen.every((v, i) => i === 0 || v > seen[i - 1]),
  ]);
  results.push([`off matched the seeded state (${seen[0]} vs ${withWeeklyOff})`, seen[0] === Math.round(withWeeklyOff * 1000) / 1000]);
  await shot(page, "vwapfilllab-2-weekly-full");

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
