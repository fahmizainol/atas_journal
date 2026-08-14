// The replay account, driven through its four states.
//
// Everything here is stubbed **in the browser**, and that is the only honest way
// to check this surface: the states worth looking at are "an hour to wait", "the
// last account died of X", "the account is blown" and "this sitting owes a
// review", and reaching any of them for real means either blowing $2,000 on a
// replay or waiting a day. `page.route` gets there in a reload. Nothing is
// written to data/replays and no settings are touched — the server's own rules
// are covered by tests/test_replay_account.py, which is where they belong.
//
// What this file is for is the half a pytest cannot see: whether the chip, the
// band, the autopsy and the review panel actually appear, and whether the
// account's refusal reaches the order path before a fill does.
//
//   node accountcheck.mjs
//   node accountcheck.mjs --headed
//   APP_URL=http://localhost:4300 node accountcheck.mjs
import { launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const iso = (d) => new Date(d).toISOString().replace(/\.\d{3}Z$/, "Z");

/** A live, healthy account. Every state below is this with two fields moved. */
const healthy = () => ({
  now: iso(Date.now()),
  equity: 50_000,
  floor: 48_000,
  peak_close: 50_000,
  status: "live",
  day_net: 0,
  day_loss_remaining: 1_200,
  target_remaining: 3_000,
  next_sitting_at: null,
  cooldown_until: null,
  can_reset: false,
  review_block: null,
  epoch: { index: 0, started_at: iso(Date.now() - 864e5), sittings: 0, net: 0 },
  last_death: null,
  caps: { minis: 4, micros: 40 },
});

// Mutated between reloads; the route handler below always serves the current one.
let account = healthy();
// The attempt the review stub answers for. Filled in once a real session is on
// screen — review mode only holds while the sitting it names is the session
// being looked at, which is the point of that rule and also what makes it
// impossible to fake with a hardcoded id.
let reviewDetail = null;

// One handler for the whole family, dispatching on the path. Two `page.route`
// calls would collide: `**/replays/*` matches `/replays/account` as well, and
// which one wins is a fact about registration order rather than about intent.
await page.route("**/api/replays**", async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname.endsWith("/replays/account")) {
    return route.fulfill({ json: account });
  }
  if (reviewDetail && url.pathname.endsWith(`/replays/${reviewDetail.id}`)) {
    return route.fulfill({ json: reviewDetail });
  }
  return route.fallback();
});

// This instance's configured levels refuse every entry on NQ (a 100-tick stop
// floor against a $250 ceiling at $5/tick), which would mask the account's own
// refusal behind a shape refusal. Stubbed for the same browser-only reason
// panecheck stubs it, and nothing is written to settings.
//
// **Answered outright rather than fetched-and-patched**, which is where panecheck
// does it differently and is the one thing to know before copying this file:
// `/live/routing` reaches the broker session, and with no Rithmic login up it
// sits there until Playwright's 30s route timeout kills the whole run. The page
// only reads `guards` and `replay_guardrails` off this, so a synthetic answer is
// a complete one.
await page.route("**/live/routing", (route) =>
  route.fulfill({
    json: {
      replay_guardrails: true,
      guards: {
        daily_loss_stop: 500,
        daily_profit_lock: 0,
        slow_down_at: 300,
        min_gap_s: 120,
        min_target_ticks: 20,
        stop_ticks_min: 20,
        stop_ticks_max: 400,
        require_bracket: true,
        auto_flatten: true,
        max_risk_usd: 100_000,
        commission_per_side: 3.5,
      },
    },
  }),
);

const chip = () => page.locator(".chart-account-chip");
const blockText = async () =>
  ((await chip().locator("[data-account-block]").count())
    ? (await chip().locator("[data-account-block]").textContent())
    : ""
  ).trim();

/** Space + click a price on pane 0 — the harness notes explain every line of
 *  this; it is panecheck's gesture unchanged. */
const spaceClick = async (fx = 0.78, fy = 0.45) => {
  const box = await page.locator('.sim-pane[data-pane="0"]').boundingBox();
  const x = box.x + box.width * fx;
  const y = box.y + box.height * fy;
  await page.mouse.move(x, y - 40);
  await page.mouse.move(x, y);
  await page.waitForTimeout(250);
  await page.keyboard.down("Space");
  await page.waitForTimeout(150);
  await page.mouse.click(x, y);
  await page.keyboard.up("Space");
  await page.waitForTimeout(900);
};

/** Whatever the page last said it refused, off the discipline strip. */
const refusedText = async () => {
  const el = page.locator("text=/⚠ refused/").first();
  return (await el.count()) ? ((await el.textContent()) ?? "").trim() : "";
};

try {
  // --- a healthy account is nearly invisible --------------------------------
  await openChart(page, "/charts/replay");
  await page.evaluate(() => {
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.resume");
  });
  await openChart(page, "/charts/replay");

  check("the chip is on the bar", (await chip().count()) === 1, `${await chip().count()} chip(s)`);
  check("a healthy account says nothing else", (await blockText()) === "", `said "${await blockText()}"`);
  check("no band above the tape", (await page.locator(".sim-account-note").count()) === 0);

  // Which session is actually loaded — the review stub has to name it.
  const title = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  const [symbol, date] = title.split("·").map((s) => s.trim());
  check("a session is loaded", /^[A-Z0-9]+$/.test(symbol) && /^\d{4}-\d{2}-\d{2}$/.test(date), title.trim());

  // --- the hour gate --------------------------------------------------------
  account = { ...healthy(), next_sitting_at: iso(Date.now() + 42 * 60_000) };
  await openChart(page, "/charts/replay");
  const wait = await blockText();
  check("the chip counts down to the next sitting", /^next in 4[12]m/.test(wait), `chip says "${wait}"`);

  await spaceClick();
  const refused = await refusedText();
  check(
    "the account refuses the entry before the guardrails do",
    refused.includes("an hour between sittings"),
    refused.slice(0, 80),
  );
  check("…and nothing rested", (await page.evaluate(() =>
    Number(document.querySelector(".sim-rail-badge")?.textContent?.trim()) || 0)) === 0);

  // --- the last account's epitaph -------------------------------------------
  account = {
    ...healthy(),
    epoch: { index: 1, started_at: iso(Date.now() - 864e5), sittings: 2, net: -300 },
    last_death: {
      at: iso(Date.now() - 3 * 864e5),
      attempt_id: "2026-02-03_NQH5_20260203T140000Z",
      equity: 47_900,
      floor: 48_000,
      epoch: 0,
      cause_of_death: "sized up to get back to flat after two stops",
    },
  };
  await openChart(page, "/charts/replay");
  const pinned = page.locator(".sim-account-note.pinned");
  check(
    "the cause of death is pinned across the next account",
    (await pinned.count()) === 1 &&
      ((await pinned.textContent()) ?? "").includes("sized up to get back to flat"),
    ((await pinned.textContent()) ?? "").trim().slice(0, 70),
  );

  // --- blown ----------------------------------------------------------------
  account = {
    ...healthy(),
    status: "blown",
    equity: 47_900,
    floor: 48_000,
    cooldown_until: iso(Date.now() + 20 * 3600_000),
    last_death: {
      at: iso(Date.now() - 3600_000),
      attempt_id: "2026-02-03_NQH5_20260203T140000Z",
      equity: 47_900,
      floor: 48_000,
      epoch: 0,
      cause_of_death: null,
    },
  };
  await openChart(page, "/charts/replay");
  check("a blown account gets a red band", (await page.locator(".sim-account-note.dead").count()) === 1);
  check("the autopsy opens itself", (await page.locator("[data-autopsy]").count()) === 1);
  check("it asks for a sentence", (await page.locator("[data-autopsy-cause]").count()) === 1);
  check(
    "the ticket is gone while it is up",
    (await page.locator(".sim-ticket").count()) === 0,
    `${await page.locator(".sim-ticket").count()} ticket(s)`,
  );
  const fileBtn = page.locator("[data-autopsy-file]");
  check("and will not take an empty one", await fileBtn.isDisabled());

  // --- the forced review ----------------------------------------------------
  const attemptId = `${date}_${symbol}_20260203T140000Z`;
  reviewDetail = {
    id: attemptId,
    created_at: iso(Date.now() - 7200_000),
    updated_at: iso(Date.now() - 3600_000),
    finished_at: iso(Date.now() - 3600_000),
    symbol,
    root: symbol.slice(0, 2),
    date,
    tz: "New York",
    engine_version: 1,
    // Deliberately not this tape's fingerprint: an unusable log costs the
    // trades and never the review, and the panel is what is being checked.
    tape: { n: 1, t0: 0, end: 1, rth_open_ms: 0 },
    prefs: {},
    status: "finished",
    started_ms: 0,
    clock_ms: 0,
    repeat_index: 0,
    note: "",
    model_id: null,
    rewinds: [],
    discarded_trades: 0,
    summary: {},
    log: { orders: [], closes: [], brackets: [] },
    trades: [],
    discarded: [],
    flags: [
      { kind: "trade", trade_id: 1, ms: 0, pnl: -400, label: "LONG 1 — $400 down", reasons: ["resolved in 6s"] },
      { kind: "rewind", trade_id: null, ms: 0, pnl: 0, label: "rewind #1 — 2 trades un-happened", reasons: ["a seek back over your own fills"] },
    ],
  };
  account = { ...healthy(), review_block: { attempt_id: attemptId, flags: reviewDetail.flags } };
  await page.evaluate(
    ([id, sym, d]) => {
      localStorage.setItem("sim.review", JSON.stringify({ attemptId: id }));
      localStorage.setItem(
        "sim.resume",
        JSON.stringify({ symbol: sym, date: d, clockMs: 0, attemptId: id, contextTicks: 0 }),
      );
    },
    [attemptId, symbol, date],
  );
  await openChart(page, "/charts/replay");

  const panel = page.locator("[data-review-panel]");
  check("review mode opens the panel", (await panel.count()) === 1);
  check("every flag is listed", (await page.locator("[data-review-flag]").count()) === 2,
    `${await page.locator("[data-review-flag]").count()} flag(s)`);
  check(
    "the ticket is gone",
    (await page.locator(".sim-ticket").count()) === 0,
    `${await page.locator(".sim-ticket").count()} ticket(s)`,
  );
  const file = page.locator("[data-review-file]");
  check("filing is refused until every flag is answered", await file.isDisabled());

  await page.locator('[data-review-flag="0"] [data-verdict="leak"]').click();
  await page.waitForTimeout(200);
  check("a partial review is still refused", await file.isDisabled(),
    (await page.locator("[data-review-progress]").textContent())?.trim());
  await page.locator('[data-review-flag="1"] [data-verdict="justified"]').click();
  await page.waitForTimeout(200);
  check("a complete one is not", !(await file.isDisabled()),
    (await page.locator("[data-review-progress]").textContent())?.trim());

  // Placing must still be refused, and by the review rather than by the account.
  await spaceClick();
  const rr = await refusedText();
  check("an order in review mode is refused as a review", rr.includes("this is a review"), rr.slice(0, 80));

  check("no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  // Leave no marks behind: a review marker in localStorage would make every
  // later visit to the page a review.
  await page.evaluate(() => {
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.resume");
  }).catch(() => {});
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
