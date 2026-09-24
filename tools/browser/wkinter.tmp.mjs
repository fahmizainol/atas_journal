// Throwaway: the weekly developing VA on the Interactions Lab day chart.
//
// Drives the real page: sets a one-day range with the weekly source checked,
// runs the study, and asserts the session chart's legend grew the
// "Developing VA · Weekly" row (and that the weekly rows arrive from the API).
import { launch, BASE, SHOTS } from "./lib.mjs";
import { mkdir } from "node:fs/promises";

const { browser, page, errors } = await launch();
await mkdir(SHOTS, { recursive: true });

await page.goto(`${BASE}/interactions`, { waitUntil: "networkidle", timeout: 60000 });

// Config: NQ, one cached day, sources ny+globex+weekly.
await page.fill("label:has-text('Start') input", "2026-06-24");
await page.fill("label:has-text('End') input", "2026-06-24");
const weeklyBox = page.locator("label:has-text('Weekly profile') input");
if (!(await weeklyBox.isChecked())) await weeklyBox.check();
await page.click("button:has-text('Run tracking')");

// The run computes (cached from the earlier API hit), then the day chart loads.
await page.waitForSelector("text=Developing VA · Weekly", { timeout: 120000 });
const report = {};
report.weeklyRow = await page.locator("text=Developing VA · Weekly").count();
report.legendRows = await page.$$eval(".chart-legend-row, .chart-legend [class*=row]",
  (r) => r.length).catch(() => -1);
// Weekly touches in the touch table, if the table is on screen
report.weeklyLabelMentions = await page
  .locator("text=/Weekly (VAH|VAL|POC)/")
  .count();
await page.waitForTimeout(800);
await page.screenshot({ path: `${SHOTS}/lab-weekly-va.png`, fullPage: false });

console.log(JSON.stringify(report, null, 2));
console.log("errors:", errors.length ? errors : "none");
await browser.close();
