// One-off check: the vol ruler pane mounts on /charts/replay, its legend row
// exists, and toggling the row drops the pane. Screenshots land in shots/.
import { launch, openChart, probeChart, shot } from "./lib.mjs";

const { browser, page, errors } = await launch();
try {
  await openChart(page, "/charts/replay");
  await page.waitForTimeout(2500);
  const p = await probeChart(page);
  console.log(`canvas ${p.w}x${p.h}, ink ${p.ink}`);

  // The legend row (expand the list first if it's collapsed).
  const head = page.locator(".chart-legend-head");
  const row = page.locator(".chart-legend-row", { hasText: "Vol ruler" });
  if ((await row.count()) === 0 && (await head.count()) > 0) {
    await head.first().click();
    await page.waitForTimeout(300);
  }
  const rowCount = await row.count();
  console.log(`legend row: ${rowCount ? "present" : "MISSING"}`);

  // Pane separators: n panes leave n-1 separators.
  const seps = await page.evaluate(
    () => document.querySelectorAll(".sim-chart table tr").length,
  );
  console.log(`chart table rows (panes proxy): ${seps}`);
  await shot(page, "volruler-on");

  if (rowCount) {
    await row.first().click();
    await page.waitForTimeout(500);
    await shot(page, "volruler-off");
    await row.first().click();
    await page.waitForTimeout(500);
  }
  console.log(`console errors: ${errors.length ? errors.join(" | ") : "none"}`);
} finally {
  await browser.close();
}
