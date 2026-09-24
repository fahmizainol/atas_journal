/** The /trades expanded row after collapsing the recording.
 *
 * Two things to prove, and neither is visible from the source: the recording is
 * gone by default but recoverable, and the detail below it is no longer wedged
 * into the two-column layout that existed only to sit beside a player.
 */
import { launch, BASE } from "./lib.mjs";

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
try {
  await page.goto(`${BASE}/trades?mode=replay`, { waitUntil: "networkidle", timeout: 60000 });
  const rows = page.locator("table tbody tr");
  console.log("trade rows:", await rows.count());
  await rows.first().click();
  await page.waitForTimeout(2500);

  const toggle = page.getByRole("button", { name: /Show recording|Hide recording/ });
  console.log("recording toggle:", await toggle.count(), "label:",
              (await toggle.count()) ? await toggle.first().innerText() : "—");
  console.log("two-column grid still used:",
              await page.locator(".trade-detail-summary-grid").count());
  console.log("Measured levels visible:",
              await page.getByText("Measured levels").first().isVisible().catch(() => false));

  // And it must come back.
  if (await toggle.count()) {
    await toggle.first().click();
    await page.waitForTimeout(1500);
    console.log("after click, label:", await toggle.first().innerText());
    console.log("player present:", await page.locator("video").count());
  }
  console.log("console errors:", errors.length ? errors.slice(0, 8) : "none");
} finally {
  await browser.close();
}
