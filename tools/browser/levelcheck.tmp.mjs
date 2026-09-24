/** Does the measured-levels panel actually reach the screen?
 *
 * Everything upstream checks out — the API serves 14 rows for this trade, the
 * dev server serves the component, the route renders it — so the only thing left
 * unproven is the render itself. That is exactly the gap a browser closes and
 * static reading cannot.
 *
 * Usage: node tools/browser/levelcheck.tmp.mjs [--headed]
 */
import { launch, BASE } from "./lib.mjs";

const DAY = process.env.DAY ?? "2024-06-19";

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
try {
  await page.goto(`${BASE}/calendar/${DAY}?mode=replay`, {
    waitUntil: "networkidle", timeout: 60000,
  });

  const rows = page.locator("table tbody tr");
  const n = await rows.count();
  console.log(`rows in "Trades this day": ${n}`);
  if (!n) throw new Error("no trade rows on the day page");

  await rows.first().click();
  await page.waitForTimeout(2500);

  const panel = page.getByText("Measured levels", { exact: false });
  const seen = await panel.count();
  console.log(`"Measured levels" headings found: ${seen}`);

  if (seen) {
    const box = panel.first();
    console.log("visible:", await box.isVisible());
    const text = await box.locator("xpath=..").innerText();
    console.log("--- panel text ---");
    console.log(text);
  } else {
    // Say what DID render, so the failure names itself instead of being "nothing".
    const body = await page.locator("body").innerText();
    const marks = ["Journal", "Direction", "Net PnL", "Loading trade"];
    console.log("panel absent; page contains:",
                marks.filter((m) => body.includes(m)).join(", ") || "(none of the markers)");
  }
  console.log("console errors:", errors.length ? errors.slice(0, 8) : "none");
} finally {
  await browser.close();
}
