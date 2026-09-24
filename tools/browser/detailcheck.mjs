// The revamped trade detail: two columns, a sticky journal rail, excursion in
// the header, and the claim shown read-only.
//
// What this asks, and why each is worth a browser rather than a type check:
//
//   1. the expanded row is a two-column grid at 1600px, and the rail is narrower
//      than the trade column — a grid whose second track collapsed to nothing is
//      still a valid grid, so only computed geometry can tell;
//   2. `position: sticky` actually engages. It resolves against the nearest
//      scroll container, so an ancestor with `overflow-x: auto` silently turns
//      it into `static` — the exact trap `.table-scroll-x-narrow` exists for,
//      and one that no amount of reading the rule can rule out;
//   3. MFE/MAE/exit efficiency are in the header, visible with the chart shut;
//   4. the AI panel renders without opening the chart, now that it no longer
//      rides that toggle;
//   5. the review rail is read-only here — the grade and level pickers are the
//      review card's controls, and authoring either beside the outcome would be
//      the same judgement made with less in front of it;
//   6. under 1100px the columns stack in DOM order, trade above journal.
//
// Run: node tools/browser/detailcheck.mjs [--headed]
import { launch, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const MODES = process.env.JOURNAL_MODES ?? "replay";

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** A DOMRect for one selector, or null. */
const box = (page, sel) =>
  page.$eval(sel, (e) => {
    const r = e.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  }).catch(() => null);

const { browser, page, errors } = await launch({ headed });
try {
  console.log(`trade detail — ${BASE}/trades?mode=${MODES}`);
  await page.goto(`${BASE}/trades?mode=${MODES}`, { waitUntil: "networkidle" });
  await page.locator("table tbody tr").first().click();
  await page.waitForSelector(".trade-detail-split", { timeout: 30000 });
  // The excursion query is a second round trip; the header cells hold "…" until
  // it lands, which would read as a missing number rather than a pending one.
  await page.waitForTimeout(2500);

  const split = await box(page, ".trade-detail-split");
  const main = await box(page, ".trade-detail-main");
  const rail = await box(page, ".trade-detail-rail");
  ok("two columns at 1600px", !!main && !!rail && rail.x > main.x + main.w - 2,
     rail && main ? `main ${Math.round(main.w)}px, rail ${Math.round(rail.w)}px` : "missing");
  ok("rail is the narrow one", !!main && !!rail && rail.w < main.w);
  ok("the split fits its panel", !!split && !!rail && rail.x + rail.w <= split.x + split.w + 2);

  // Sticky, resolved. A scroll-container ancestor would compute this as `static`
  // without changing a line of the rule.
  const stickyOk = await page.$eval(".trade-detail-rail", (e) => {
    if (getComputedStyle(e).position !== "sticky") return "position is not sticky";
    // Walk up looking for the clip that would scope it to a box with no scroll.
    for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p);
      if (o.overflowX !== "visible" || o.overflowY !== "visible") {
        if (p.scrollHeight <= p.clientHeight + 1) return `scoped to a non-scrolling ${p.className}`;
      }
    }
    return "";
  });
  ok("sticky rail is not clipped", stickyOk === "", stickyOk);

  // The replay is open from the start, on every mode.
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  ok("replay open by default", (await page.$(".chart-legend")) !== null);
  const kpis = await page.$$eval(".trade-detail-main .kpi-card", (els) =>
    els.map((e) => e.textContent.trim()));
  const kpiText = kpis.join(" | ");
  for (const label of ["MFE", "MAE", "Exit efficiency"]) {
    ok(`${label} in the header`, kpiText.includes(label));
  }
  ok("excursion resolved", !kpiText.includes("…"), kpiText.includes("…") ? "still pending" : "");

  // The AI panel, unbundled from the chart toggle: still there when the replay
  // is toggled off.
  await page.locator("button", { hasText: "Hide replay" }).first().click();
  ok("replay unmounts on hide", (await page.$(".chart-legend")) === null);
  const aiText = await page.$eval(".trade-detail-main", (e) => e.textContent).catch(() => "");
  ok("AI panel rendered with the chart shut", /analysis/i.test(aiText));

  // The journal form asks everything but the grade — the grade alone is
  // blind-captured at the recall front and never picked beside the P&L.
  ok("no grade picker on this page",
     (await page.$("[data-review-grade-pick]")) === null);
  ok("the form offers the level chips",
     (await page.$("[data-journal-levels] [data-review-level-pick]")) !== null);
  const railText = await page.$eval(".trade-detail-rail", (e) => e.textContent);
  const graded = await page.$("[data-detail-grade]");
  ok("review rail says something either way",
     graded !== null || railText.includes("Not reviewed yet") || !railText.includes("Review"),
     railText.slice(0, 80));

  // The model + rules fold, and the note left outside it.
  ok("model + rules are folded", (await page.$(".trade-detail-rail .journal-fold")) !== null);
  const foldSummary = await page.$eval(".trade-detail-rail .journal-fold summary",
                                       (e) => e.textContent.trim()).catch(() => "");
  ok("the fold's summary carries the binding", /^Model:/.test(foldSummary), foldSummary);
  ok("the note stayed outside the fold",
     (await page.$(".trade-detail-rail textarea")) !== null &&
     (await page.$(".trade-detail-rail .journal-fold textarea")) === null);

  await shot(page, "detail-wide");

  // With the replay open: it belongs to the left column, arrives already armed
  // on the trade (playing), and the header's excursion numbers print once.
  await page.locator("button", { hasText: "Show replay" }).first().click();
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(1500);
  const chartBox = await box(page, ".chart-legend");
  const mainC = await box(page, ".trade-detail-main");
  ok("the replay is in the left column",
     !!chartBox && !!mainC && chartBox.x >= mainC.x - 2 && chartBox.x < mainC.x + mainC.w);
  ok("the replay opens on the tick bar",
     (await page.$eval(".trade-detail-main .chart-legend", (e) => e.textContent)).includes("500t"));
  ok("the replay has a transport",
     (await page.$(".trade-detail-main .sim-transport")) !== null);
  ok("the transport offers ⟲ Restart",
     (await page.$("[data-trade-replay-restart]")) !== null);
  const mfeCount = await page.$$eval(".trade-detail-main .kpi-card", (els) =>
    els.filter((e) => e.textContent.includes("MFE")).length);
  ok("MFE is printed once", mfeCount === 1, `${mfeCount} cards`);
  await shot(page, "detail-chart");

  // Backtest: same layout, chart open from the start, no recording to offer.
  console.log(`\nbacktest — ${BASE}/trades?mode=backtest`);
  await page.goto(`${BASE}/trades?mode=backtest`, { waitUntil: "networkidle" });
  await page.locator("table tbody tr").first().click();
  await page.waitForSelector(".trade-detail-split", { timeout: 30000 });
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  ok("backtest gets the same split", (await page.$(".trade-detail-rail")) !== null);
  ok("backtest offers no recording",
     (await page.locator("button", { hasText: "Show recording" }).count()) === 0);
  await shot(page, "detail-backtest");
  await page.goto(`${BASE}/trades?mode=${MODES}`, { waitUntil: "networkidle" });
  await page.locator("table tbody tr").first().click();
  await page.waitForSelector(".trade-detail-split", { timeout: 30000 });

  // Stacked, in DOM order.
  await page.setViewportSize({ width: 900, height: 900 });
  await page.waitForTimeout(400);
  const mainN = await box(page, ".trade-detail-main");
  const railN = await box(page, ".trade-detail-rail");
  ok("stacks under 1100px", !!mainN && !!railN && railN.y > mainN.y,
     mainN && railN ? `main y=${Math.round(mainN.y)}, rail y=${Math.round(railN.y)}` : "missing");
  ok("trade first, journal last", !!mainN && !!railN && railN.y >= mainN.y + mainN.h - 2);
  await shot(page, "detail-narrow");
} catch (e) {
  console.error(e);
  await shot(page, "detail-error");
  fails.push(`threw: ${e.message}`);
} finally {
  if (errors.length) {
    console.log(`\npage errors (${errors.length}):`);
    for (const m of errors.slice(0, 10)) console.log(`  ${m}`);
  }
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} FAILED: ${fails.join(", ")}` : "\nall ok");
process.exit(fails.length ? 1 : 0);
