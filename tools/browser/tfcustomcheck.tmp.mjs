// A bucketing typed into the picker, driven the way a user types one.
//
// The claim being checked is that the eight built-in bar sizes are a default and
// not a limit: a tape-bucketed chart builds every bar in the browser, so "45s"
// is as drawable as "1m". What can quietly go wrong is everything around that —
// the field rejecting a good string, the chart not re-bucketing, the choice not
// surviving a reload (the prefs loaders used to clamp to the list of eight and
// would have thrown a custom id away), and the × forgetting the wrong one.
//
//   node tools/browser/tfcustomcheck.tmp.mjs [--headed]
import { launch, openChart, probeChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const bar = ".chart-topbar .radio-group";
const activeLabel = () => page.locator(`${bar} button.active`).first().innerText();
const legendTf = () => page.locator(".chart-legend-tf").first().innerText();
const openPop = async () => {
  if (!(await page.locator(".tf-pop").count())) await page.locator(`${bar} button.tf-more`).click();
  await page.waitForTimeout(200);
};
const popLabels = () =>
  page.locator(".tf-pop button").allInnerTexts().then((t) => t.map((s) => s.trim()));

try {
  await openChart(page, "/charts/replay");
  const before = await probeChart(page);
  check("a chart is up", !before.error, await activeLabel());

  // --- typing one in -------------------------------------------------------
  await openPop();
  await page.locator(".tf-custom input").fill("45s");
  await page.locator(".tf-custom input").press("Enter");
  await page.waitForTimeout(1200);
  check("the picker takes 45s", (await activeLabel()).trim() === "45s", await activeLabel());
  check("the pane legend says so too", (await legendTf()).includes("45s"), await legendTf());

  const after = await probeChart(page);
  check(
    "the chart re-bucketed",
    !after.error && JSON.stringify(after.silhouette) !== JSON.stringify(before.silhouette),
    "silhouette moved",
  );
  await shot(page, "tfcustom-45s");

  // --- and the field saying no --------------------------------------------
  await openPop();
  await page.locator(".tf-custom input").fill("banana");
  await page.locator(".tf-custom input").press("Enter");
  await page.waitForTimeout(300);
  check(
    "a string that isn't a bar is refused",
    (await page.locator(".tf-custom input.bad").count()) === 1 &&
      (await activeLabel()).trim() === "45s",
    `still on ${(await activeLabel()).trim()}`,
  );
  await page.locator(".tf-custom input").fill("");
  await page.keyboard.press("Escape");

  // --- surviving a reload --------------------------------------------------
  await openChart(page, "/charts/replay");
  check("it is still there after a reload", (await activeLabel()).trim() === "45s", await activeLabel());

  // --- and being forgotten -------------------------------------------------
  await page.locator(`${bar} button`, { hasText: /^1m$/ }).first().click();
  await page.waitForTimeout(800);
  await openPop();
  check("the one you added is in the ⋯ list", (await popLabels()).includes("45s"), (await popLabels()).join(" "));
  const forget = page.locator(".tf-pop button.tf-forget").first();
  check("…with an × beside it", (await forget.count()) === 1);
  await forget.click();
  await page.waitForTimeout(300);
  check("× forgets it", !(await popLabels()).includes("45s"), (await popLabels()).join(" "));
  check(
    "and forgets it for good",
    await page.evaluate(() => localStorage.getItem("chart.customTimeframes") === "[]"),
    await page.evaluate(() => localStorage.getItem("chart.customTimeframes")),
  );

  check("no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
