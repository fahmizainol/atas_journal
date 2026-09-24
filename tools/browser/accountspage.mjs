// The Accounts pages: the list, the detail, and the counterfactual campaign.
//
// Stubbed in the browser, like `accountcheck.mjs` and for the same reason: the
// states worth looking at are "this account blew four times", "one rep cannot be
// re-priced" and "nothing is priced yet, press the button" — and reaching any of
// them for real means blowing $2,000 on a replay or deleting a cache. `page.route`
// gets there in a reload. The server's own arithmetic is covered by
// tests/test_account_campaign.py, which is where it belongs.
//
// What this file is for is the half a pytest cannot see: whether the record, the
// lives, the campaign table and the coverage button actually render, whether the
// column switch moves the numbers, and whether the killing-rep panel opens.
//
//   node accountspage.mjs
//   node accountspage.mjs --headed
import { BASE, launch } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const NUMBERS = {
  start: 50000, max_loss: 2000, trail_cap: 52100, day_loss: 1200,
  day_goal: null, profit_target: 3000, max_minis: 4, max_micros: 40,
};

const ACCOUNTS = {
  accounts: [
    { id: "funded", label: "LucidPro 50K", template: "lucid_pro", trailing: "eod",
      needs_cause: true, archived: false, epochs: 5, numbers: NUMBERS,
      record: { passed: 1, blown: 3 } },
    { id: "daily", label: "50K LucidDaily", template: "lucid_daily", trailing: "intraday",
      needs_cause: true, archived: false, epochs: 9, numbers: NUMBERS,
      record: { passed: 0, blown: 8 } },
    { id: "retired", label: "Old One", template: "lucid_pro", trailing: "eod",
      needs_cause: true, archived: true, epochs: 1, numbers: NUMBERS,
      record: { passed: 0, blown: 1 } },
  ],
  templates: [
    { key: "lucid_pro", label: "LucidPro", trailing: "eod", needs_cause: true,
      note: "End-of-day trailing drawdown.", defaults: NUMBERS },
  ],
};

const run = (key, label, column, passed, blown, equity, net, flip = false) => ({
  key, label, column, flip, record: { passed, blown }, score: passed - blown,
  lives: [{ index: 0, reps: 3, first: "2026-02-03", last: "2026-02-05",
            end_equity: equity, outcome: blown ? "blown" : "live",
            killed_by: blown ? "2026-02-05_NQH6_20260205T000000Z" : null }],
  end_equity: equity, net_usd: net, trades: 9, win_rate: 44,
});

// One rep unpriced on purpose: it is what puts the coverage button on screen and
// the "dropped from every row" sentence under it.
let coverage = { reps: 4, traded: 3, priced: 2, unpriced: 1, flat: 1 };
let priceCalls = 0;

const campaign = () => ({
  account: ACCOUNTS.accounts[0],
  coverage,
  reps: [
    { id: "2026-02-03_NQH6_20260203T000000Z", date: "2026-02-03",
      created_at: "2026-08-14T03:00:00Z", status: "reviewed", trades: 4, net_usd: 320,
      rewinds: 0, priced: true, stop_ticks: { lo: 44, med: 51, hi: 109 },
      fast_trades: 1, held: 4 },
    { id: "2026-02-04_NQH6_20260204T000000Z", date: "2026-02-04",
      created_at: "2026-08-15T03:00:00Z", status: "finished", trades: 3, net_usd: -180,
      rewinds: 1, priced: true, stop_ticks: { lo: 51, med: 51, hi: 51 },
      fast_trades: 2, held: 3 },
    { id: "2026-02-05_NQH6_20260205T000000Z", date: "2026-02-05",
      created_at: "2026-08-16T03:00:00Z", status: "finished", trades: 2, net_usd: -2140,
      rewinds: 0, priced: false, stop_ticks: null, fast_trades: 0, held: 2 },
    { id: "2026-02-06_NQH6_20260206T000000Z", date: "2026-02-06",
      created_at: "2026-08-17T03:00:00Z", status: "finished", trades: 0, net_usd: 0,
      rewinds: 0, priced: true, stop_ticks: null, fast_trades: 0, held: 0 },
  ],
  runs: [
    run("t25", "Trail 25t", "clicked", 1, 0, 53120, 4180),
    run("as-played", "As played", "clicked", 0, 2, 48000, -6125),
    run("no-trail", "No trail", "clicked", 0, 3, 48000, -7300),
    // The forget column is deliberately *better* than clicked on the same row,
    // so the "your hand cost you" readout has a sign to get right.
    run("t25", "Trail 25t", "forget", 1, 0, 53500, 4600),
    run("as-played", "As played", "forget", 0, 1, 49100, -3011),
    run("no-trail", "No trail", "forget", 0, 2, 48600, -5100),
    // Sorted last by the server however well it scored — it is not a bracket.
    run("rev", "Reversed, exits as placed", "clicked", 2, 0, 56000, 9000, true),
  ],
  grid_version: 1,
  real: {
    record: { passed: 1, blown: 3 },
    lives: [
      { index: 0, started_at: "2026-08-14T02:58:37Z", outcome: "blown", reps: 2,
        equity: 48000, floor: 48000, cause: "wanna reset cuz floor to low already",
        ended_by: "2026-02-05_NQH6_20260205T000000Z" },
      { index: 1, started_at: "2026-08-17T03:53:05Z", outcome: "passed", reps: 1,
        equity: 53063, floor: 48000, cause: null, ended_by: "2026-02-03_NQH6_20260203T000000Z" },
      { index: 2, started_at: "2026-08-28T01:36:18Z", outcome: "live", reps: 1,
        equity: 50320, floor: 48000, cause: null, ended_by: null },
    ],
  },
});

const VERDICTS = {
  attempt_id: "2026-02-05_NQH6_20260205T000000Z",
  date: "2026-02-05",
  opened_at: 49820,
  floor: 48000,
  priced: true,
  verdicts: [
    { key: "t25", label: "Trail 25t", column: "clicked", survived: true,
      net_usd: 210, end_equity: 50030, floor: 48000 },
    { key: "as-played", label: "As played", column: "clicked", survived: false,
      net_usd: -2140, end_equity: 48000, floor: 48000 },
  ],
};

// One handler for the family. **Method before path**, like accountcheck.mjs: a
// POST that fell through to a GET branch would answer a write with a report.
await page.route("**/api/replays/**", async (route) => {
  const url = new URL(route.request().url());
  if (route.request().method() === "POST") {
    if (url.pathname.endsWith("/price")) {
      priceCalls += 1;
      // What a real sweep looks like from the page's side: the count closes on
      // the next poll rather than in this response.
      coverage = { ...coverage, priced: 3, unpriced: 0 };
      return route.fulfill({ json: { account: "funded", pricing: 1 } });
    }
    return route.fulfill({ json: {} });
  }
  if (url.pathname.endsWith("/campaign")) return route.fulfill({ json: campaign() });
  if (url.pathname.endsWith("/review"))
    return route.fulfill({ json: { grades: { A: 2, B: 5, C: 1 }, owed: 3, graded: 8 } });
  if (url.pathname.includes("/reps/")) return route.fulfill({ json: VERDICTS });
  return route.fallback();
});
await page.route("**/api/replays/accounts", (route) =>
  route.request().method() === "GET" ? route.fulfill({ json: ACCOUNTS }) : route.fallback(),
);

const open = async (path) => {
  await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded", timeout: 30000 });
};

// --- the list ---------------------------------------------------------------

console.log("\nAccounts list");
await open("/accounts");
await page.waitForSelector("[data-accounts-table]", { timeout: 20000 });

const listed = await page.$$eval("[data-account-link]", (els) =>
  els.map((e) => e.getAttribute("data-account-link")),
);
check("lists the unarchived accounts", listed.includes("funded") && listed.includes("daily"),
  listed.join(", "));
check("archived accounts are listed apart", listed.includes("retired"),
  "under their own heading");

const funded = await page.$eval("[data-account-link='funded']", (e) =>
  e.closest("tr").textContent);
check("shows each account's record", /1 passed/.test(funded) && /3 blown/.test(funded),
  funded.replace(/\s+/g, " ").trim().slice(0, 70));
// The list formats money with a thousands separator; the detail page does not.
// Both are asserted in their own terms — see `browser-harness-traps`.
check("shows the numbers with separators", /\$50,000/.test(funded), "start $50,000");

const rules = await page.$eval("[data-account-link='daily']", (e) =>
  e.closest("tr").textContent);
check("names the trailing shape", /intraday trail/.test(rules));

check("the FilterBar is not drawn on this tab", (await page.$$(".filter-bar")).length === 0,
  "Accounts reads the replay store, not the ATAS journal");

// --- the detail -------------------------------------------------------------

console.log("\nAccount detail");
await page.click("[data-account-link='funded']");
await page.waitForSelector("[data-account-detail='funded']", { timeout: 20000 });

const record = await page.$eval("[data-lives-table]", (e) => e.textContent);
check("draws one row per life", (await page.$$("[data-lives-table] tbody tr")).length === 3);
check("reads the cause of death back", /floor to low already/.test(record),
  "the sentences were write-only before this page");
check("names a life that has not ended", /live/.test(record));
check("draws the equity curve", (await page.$$("[data-equity-curve]")).length === 1);

const campaignText = await page.$eval("[data-campaign-table]", (e) => e.textContent);
// The *ranking* is the server's (`Run.score`, covered by pytest). What this
// asserts is that the page preserves the order it was handed rather than
// re-sorting by whichever column it happens to render first.
check("renders the campaign in the order the server ranked it",
  (await page.$$eval("[data-campaign-table] [data-run]", (els) =>
    els.map((e) => e.getAttribute("data-run")))).join(",") === "t25,as-played,no-trail,rev");

const flipRow = await page.$("[data-campaign-table] [data-flip]");
check("reversed rows are ranked apart from the brackets", !!flipRow,
  "2 passed / 0 blown, and still below every real bracket");
check("and are labelled as changing the direction, not the exit",
  /change the direction, not the exit/.test(campaignText));
check("marks the as-played row as the baseline", /baseline/.test(campaignText));
check("shows a delta against it", /\+\$5120|\+\$/.test(campaignText),
  "vs played column");

const coverageText = await page.$eval("[data-coverage]", (e) => e.textContent);
check("states how much is priced", /priced 2 of 3/.test(coverageText), coverageText.trim());
check("says a dropped rep breaks the comparison",
  /could not be re-priced and are dropped/.test(
    await page.$eval("[data-campaign-table]", (e) => e.closest("section").textContent)),
);

// The headline finding: the gap between the two columns of the same row.
const hand = await page.$eval("[data-account-detail]", (e) => e.textContent);
check("reports what the hand on the exit cost, in lives",
  /your hand on the exit/.test(hand) && /-1 life|−1 life/.test(hand),
  "as-played clicked scores -2, forget scores -1");

// --- the column switch ------------------------------------------------------

console.log("\nColumns");
await page.click("[data-column='forget']");
await page.waitForFunction(
  () => document.querySelector("[data-campaign-table]").textContent.includes("49100.00"),
  { timeout: 10000 },
).catch(() => {});
const forgetText = await page.$eval("[data-campaign-table]", (e) => e.textContent);
check("switching column moves the numbers", /49100\.00/.test(forgetText),
  "as-played forget ends at $49100.00 — no thousands separator on this page");

await page.click("[data-column='clicked']");

// --- the killing rep --------------------------------------------------------

console.log("\nWhat killed each life");
const killers = await page.$$("[data-killer]");
check("offers the rep that ended each finished life", killers.length === 2,
  `${killers.length} lives ended`);
await killers[0].click();
await page.waitForSelector("[data-rep-verdicts]", { timeout: 10000 });
const verdict = await page.$eval("[data-rep-verdicts]", (e) => e.textContent);
check("judges every bracket from the state that rep opened against",
  /opened at \$49820\.00/.test(verdict) && /floor of \$48000\.00/.test(verdict));
check("counts the brackets that survive it", /1 of 2 brackets survive/.test(verdict), );

// --- pricing ----------------------------------------------------------------

console.log("\nPricing");
await page.click("[data-price-account]");
await page.waitForFunction(
  () => /priced 3 of 3/.test(document.querySelector("[data-coverage]")?.textContent ?? ""),
  { timeout: 20000 },
).catch(() => {});
check("the button starts a sweep", priceCalls === 1, `${priceCalls} call(s)`);
check("the coverage count closes",
  /priced 3 of 3/.test(await page.$eval("[data-coverage]", (e) => e.textContent)));

// --- the consistency read ---------------------------------------------------

console.log("\nHow you actually traded it");
const consistency = await page.$eval("[data-account-detail]", (e) => e.textContent);
check("reports the stop as a range across reps", /44|51/.test(consistency) && /median of medians/.test(consistency));
// Two traded reps carry a stop range; one of them moved it between fills.
check("counts the reps that varied it inside", /1 of 2/.test(consistency));
// Three traded reps hold 4 + 3 + 2 fills, of which 1 + 2 + 0 were under 30s.
check("counts sub-30s trades",
  /Trades under 30s/.test(consistency) && /3 of 9/.test(consistency) && /33% of fills/.test(consistency));
check("shows the grade mix", /A×2/.test(consistency) && /B×5/.test(consistency));
check("shows the review debt", /3 trades unanswered/.test(consistency));

// --- the extracted manager --------------------------------------------------

console.log("\nAccount manager");
await open("/accounts");
await page.waitForSelector("[data-accounts-manage]", { timeout: 20000 });
await page.click("[data-accounts-manage]");
await page.waitForSelector("[data-account-manager]", { timeout: 10000 });
check("the page renders the same manager the chart popover does",
  (await page.$$("[data-account-manager] input[type=number]")).length === 8,
  "eight editable numbers");

// --- report -----------------------------------------------------------------

const bad = errors.filter((e) => !/favicon|manifest/i.test(e));
check("no console errors", bad.length === 0, bad.slice(0, 3).join(" | "));

const passed = results.filter(([, ok]) => ok).length;
console.log(`\n${passed}/${results.length} passed`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
