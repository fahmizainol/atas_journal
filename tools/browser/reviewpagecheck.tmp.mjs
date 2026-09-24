// The Review page (top-down reviewed trades) + the Trades table's cut.
//
//   1. /review renders: facet rail groups, the setup×discipline matrix, the
//      ledger table with vocab-ordered setup buckets and an (unanswered) row;
//   2. clicking a setup facet writes rsetup= into the URL, the cut count bar
//      appears, and the ledger shrinks to the cut;
//   3. hopping to /trades with the same querystring shows the cut chips and a
//      table filtered to the same trades, Review column showing the axes;
//   4. the grade ledger defaults to blind-only and offers the hindsight toggle.
//
// Run: node tools/browser/reviewpagecheck.tmp.mjs [--headed]
import { launch, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const { browser, page, errors } = await launch({ headed });

// mode=all so the replay-era reviews (where the axes live) are in scope.
await page.goto(`${BASE}/review?mode=all`, { waitUntil: "networkidle" });
await page.waitForSelector("[data-review-page]", { timeout: 30000 });

ok("facet rail renders", (await page.locator(".facet-group").count()) === 5);
const setupFacets = page.locator(".facet-group:nth-child(2) .facet");
ok("six setup facets", (await setupFacets.count()) === 6);

ok("matrix renders cells", (await page.locator(".matrix-cell").count()) > 0);

const buckets = await page.$$eval("[data-review-page] .panel:nth-of-type(2) tbody tr td:first-child",
  (els) => els.map((e) => e.textContent.trim()));
ok("ledger groups by setup", buckets.some((b) => b.includes("faded rally")), buckets.join(" | "));
ok("ledger owns the debt", buckets.some((b) => b.includes("(unanswered)")));

// 2. click the faded-rally facet
const before = buckets.length;
await setupFacets.filter({ hasText: "faded rally" }).first().click();
await page.waitForTimeout(300);
ok("cut writes the URL", page.url().includes("rsetup=faded_rally"), page.url());
ok("cut bar appears", await page.locator(".review-cut-bar").isVisible());
const after = await page.$$eval("[data-review-page] .panel:nth-of-type(2) tbody tr",
  (els) => els.length);
ok("ledger narrows to the cut", after < before, `${before} -> ${after}`);
const cutText = await page.locator(".review-cut-label").textContent();
const cutN = Number(/([\d,]+) trade/.exec(cutText)?.[1]?.replace(",", "") ?? NaN);
ok("cut count is a number", Number.isFinite(cutN) && cutN > 0, cutText);

// 3. same querystring on /trades
const search = new URL(page.url()).search;
await page.goto(`${BASE}/trades${search}`, { waitUntil: "networkidle" });
await page.waitForSelector("[data-review-cut-bar]", { timeout: 15000 });
ok("trades shows the cut chip", (await page.locator(".review-cut-chip").count()) === 1);
const tradeRows = await page.$$eval(".panel tbody tr", (els) => els.length);
ok("trades table filtered to the cut", tradeRows === cutN, `${tradeRows} vs ${cutN}`);
const reviewCells = await page.$$eval(".review-cell", (els) => els.map((e) => e.textContent));
ok("review column shows the setup", reviewCells.some((t) => t.includes("faded rally")));

// 4. grade ledger era split
await page.goto(`${BASE}/review?mode=all`, { waitUntil: "networkidle" });
await page.waitForSelector("[data-review-page]", { timeout: 30000 });
await page.locator(".review-ledger-head .radio-group button", { hasText: "Grade" }).click();
await page.waitForSelector("[data-review-grade-era]");
const eraText = await page.locator("[data-review-grade-era]").textContent();
ok("grade cut names the era split", /hindsight/.test(eraText), eraText.trim());

// 5. state facets on the rail
const stateFacets = await page.$$eval(".facet-group:nth-child(1) .facet", (els) =>
  els.map((e) => e.textContent));
ok("three state facets", stateFacets.length === 3, stateFacets.join(" | "));
ok("owed count in rail", stateFacets.some((t) => /owes review/.test(t)));

// 6. the Trades page's standing state filter — the partial-finder — and the
//    hop from a trade to its day replay (where the review is written).
await page.goto(`${BASE}/trades?mode=all`, { waitUntil: "networkidle" });
await page.waitForSelector("[data-review-cut-bar]", { timeout: 15000 });
const allRows = await page.$$eval(".panel tbody tr", (els) => els.length);
await page.locator('[data-review-state-pick="owed"]').click();
await page.waitForTimeout(300);
ok("state filter writes the URL", page.url().includes("rstate=owed"), page.url());
const owedRows = await page.$$eval(".panel tbody tr", (els) => els.length);
ok("owed filter narrows the table", owedRows > 0 && owedRows < allRows,
  `${allRows} -> ${owedRows}`);
await page.locator(".panel tbody tr").first().click();
await page.waitForSelector("[data-detail-day-link]", { timeout: 15000 });
const dayHref = await page.locator("[data-detail-day-link]").getAttribute("href");
ok("trade links to its day replay", /\/calendar\/\d{4}-\d{2}-\d{2}\?/.test(dayHref), dayHref);
await page.locator("[data-detail-day-link]").click();
await page.waitForSelector("[data-day-replay], .panel", { timeout: 20000 });
ok("day page opens with per-trade Replay",
  (await page.getByText("Replay", { exact: true }).count()) > 0, page.url());

ok("no console/page errors", errors.length === 0, errors.slice(0, 3).join(" ; "));
await browser.close();
process.exit(fails.length ? 1 : 0);
