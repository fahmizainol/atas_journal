// The review flow's two seams, born as the probe for two real bugs
// (2026-08-17): the cancel cue "randomly playing", and End attempt never
// showing the review.
//
//   1. **A refused create is one answer.** The account refusing to open a
//      sitting (409 on POST /replays) used to be retried by every debounced
//      autosave — and each retry replayed the refusal and its cue for as long
//      as you kept trading. The recorder now seals after the first no
//      (`createRefusedRef` in useReplayAttempt); asserted here as exactly one
//      POST across several debounce windows of further trading.
//   2. **End attempt offers the review.** It used to *land* in it — every
//      traded sitting owed one. Since 2026-08-25 reviewing is a choice, so
//      ending a sitting settles it and puts two buttons on the recap card, and
//      Review now is what performs the openReview-style reload into the tape.
//      Both halves are checked: that nothing opens by itself, and that the
//      button still gets there.
//
// Everything stubbed in the browser; nothing writes to data/replays. This
// check trades, so it follows the harness rules for that (see
// accountcheck.mjs): stubbed POST/PUT, storage cleared from a non-sim page.
//
//   node reviewflowcheck.mjs
//   node reviewflowcheck.mjs --headed
import { BASE, launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};
const iso = (d) => new Date(d).toISOString().replace(/\.\d{3}Z$/, "Z");

const healthy = () => ({
  now: iso(Date.now()), equity: 50_000, floor: 48_000, peak_close: 50_000,
  status: "live", day_net: 0, day_loss_remaining: 1_200, target_remaining: 3_000,
  next_sitting_at: null, counted_ids: [], cooldown_until: null, can_reset: false,
  review_flagged: null,
  epoch: { index: 0, started_at: iso(Date.now() - 864e5), sittings: 0, net: 0 },
  last_death: null, caps: { minis: 4, micros: 40 },
  // The account's own numbers. `liveAccount` reads `rules.trailing` on every
  // render, so a stub without this is a crash rather than a shortfall — the
  // same note accountcheck.mjs carries. (It was missing here from the account
  // registry until 2026-08-25, which meant this whole check died on load.)
  rules: {
    template: "lucid_pro", template_label: "LucidPro", trailing: "eod",
    real_time: true, start: 50_000, max_loss: 2_000, trail_cap: 52_100, day_loss: 1_200,
    day_goal: null, profit_target: 3_000, max_minis: 4, max_micros: 40,
  },
  account: "funded",
  label: "LucidPro 50K",
});

const ID = "2026-01-01_NQH5_20260101T000000Z";
let scenario = 1;   // 1 = refuse creates; 2 = accept and serve back
let postCount = 0;
let lastPut = null;
let lastPatch = null;
let sessSymbol = "NQH5";
let sessDate = "2026-01-01";

await page.route("**/api/replays**", (route) => {
  const url = new URL(route.request().url());
  const method = route.request().method();
  if (url.pathname.endsWith("/replays/account")) return route.fulfill({ json: healthy() });
  if (method === "POST" && url.pathname.endsWith("/api/replays")) {
    postCount += 1;
    if (scenario === 1)
      return route.fulfill({
        status: 409,
        // A refusal that can still happen: the review gate is gone, the blown
        // one is the last left. What this scenario is about is the *seal* — one
        // no is the whole answer — and that is indifferent to the reason.
        json: { detail: { code: "blown", message: "the account is blown — write what killed it before anything else.", until: null } },
      });
    return route.fulfill({ json: { id: ID, status: "active", repeat_index: 0, note: "", model_id: null } });
  }
  if (method === "PUT" && url.pathname.endsWith(`/replays/${ID}`)) {
    lastPut = route.request().postDataJSON() ?? null;
    return route.fulfill({ json: { id: ID, status: lastPut?.status ?? "active", repeat_index: 0, note: "", model_id: null } });
  }
  // Review later's mark. Served back on the record so the button can read its
  // own state, the way the real PATCH does.
  if (method === "PATCH" && url.pathname.endsWith(`/replays/${ID}`)) {
    lastPatch = route.request().postDataJSON() ?? null;
    return route.fulfill({ json: {
      id: ID, status: lastPut?.status ?? "finished", repeat_index: 0, note: "",
      model_id: null, review_later: !!lastPatch?.review_later,
    } });
  }
  if (method === "GET" && url.pathname.endsWith(`/replays/${ID}/journal`)) {
    const trades = lastPut?.trades ?? [];
    return route.fulfill({ json: { trades: trades.map((t, i) => ({
      trade_key: `k${i}`, direction: t.side, entry_ts_local: "2025-01-01 10:00:00",
      net_pnl: t.pnl, model_id: null, rules_met: [], reviewed: false,
      note: "", tags: [], setups: [], confluences: [],
    })) } });
  }
  if (method === "GET" && url.pathname.endsWith(`/replays/${ID}`)) {
    return route.fulfill({ json: {
      id: ID, created_at: iso(Date.now() - 3600_000), updated_at: iso(Date.now()),
      finished_at: iso(Date.now()), symbol: sessSymbol, root: sessSymbol.slice(0, 2),
      date: sessDate, tz: "New York", engine_version: 1,
      tape: { n: 1, t0: 0, end: 1, rth_open_ms: 0 }, prefs: {},
      status: "finished", started_ms: 0, clock_ms: 0, repeat_index: 0, note: "",
      model_id: null, mode: "replay", rewinds: [], discarded_trades: 0,
      summary: lastPut?.summary ?? {}, log: lastPut?.log ?? { orders: [], closes: [], brackets: [] },
      trades: lastPut?.trades ?? [], discarded: [], flags: [],
    } });
  }
  if (method !== "GET") return route.fulfill({ json: { ok: true } });
  return route.fallback();
});

await page.route("**/api/notes/**", (route) => {
  const url = new URL(route.request().url());
  if (route.request().method() === "GET" && url.pathname.endsWith("/notes/tags"))
    return route.fulfill({ json: { tags: [] } });
  if (route.request().method() === "PUT") return route.fulfill({ json: { ok: true } });
  return route.fallback();
});

await page.route("**/live/routing", (route) =>
  route.fulfill({ json: { replay_guardrails: true, guards: {
    daily_loss_stop: 5_000, daily_profit_lock: 0, slow_down_at: 300, min_gap_s: 0,
    min_target_ticks: 20, stop_ticks_min: 20, stop_ticks_max: 400,
    require_bracket: false, auto_flatten: true, max_risk_usd: 100_000,
    commission_per_side: 3.5,
  } } }),
);

const refusedText = async () => {
  const el = page.locator("text=/⚠ refused/").first();
  return (await el.count()) ? ((await el.textContent()) ?? "").trim() : "";
};

try {
  // --- scenario 1: a refused create is one refusal, not a loop --------------
  await page.goto(`${BASE}/`);
  await page.evaluate(() => {
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.review.replay");
    localStorage.removeItem("sim.review.drill");
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
  });
  await openChart(page, "/charts/replay");
  const title = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  [sessSymbol, sessDate] = title.split("·").map((s) => s.trim());

  await page.locator('.sim-pane[data-pane="0"]').hover();
  await page.keyboard.press("w");
  for (let i = 0; i < 4; i++) { await page.keyboard.press("."); await page.waitForTimeout(400); }
  await page.waitForTimeout(2500);  // past the debounce → the create fires and 409s
  check("the refused create is said once", (await refusedText()).includes("account is blown"),
    (await refusedText()).slice(0, 80));
  const after1 = postCount;
  // Keep trading across several debounce windows — before the seal, every one
  // of these retried the create and replayed the cue.
  await page.keyboard.press("q");
  for (let i = 0; i < 3; i++) { await page.keyboard.press("."); await page.waitForTimeout(400); }
  await page.keyboard.press("w");
  for (let i = 0; i < 3; i++) { await page.keyboard.press("."); await page.waitForTimeout(400); }
  await page.waitForTimeout(4000);
  check("…and the create is never retried", postCount === after1 && after1 === 1,
    `${postCount} POST(s)`);

  // --- scenario 2: End attempt offers the review ----------------------------
  scenario = 2;
  postCount = 0;
  lastPut = null;
  lastPatch = null;
  await page.goto(`${BASE}/`);
  await page.evaluate(() => {
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.review.replay");
    localStorage.removeItem("sim.review.drill");
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
  });
  await openChart(page, "/charts/replay");
  // A fresh visit draws a fresh day — the detail stub has to name *this* one
  // or review mode will (correctly) refuse the mismatch.
  const t2 = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  [sessSymbol, sessDate] = t2.split("·").map((s) => s.trim());
  await page.locator('.sim-pane[data-pane="0"]').hover();
  await page.keyboard.press("w");
  for (let i = 0; i < 4; i++) { await page.keyboard.press("."); await page.waitForTimeout(400); }
  await page.keyboard.press("q");     // close → a booked trade
  await page.waitForTimeout(2500);    // let the autosave land
  await page.locator('button[title="Show the ticket and blotter"]').click();
  await page.waitForSelector(".sim-panel.open", { timeout: 5000 });
  await page.waitForTimeout(400);
  // Dispatched rather than clicked: in this viewport an empty overlay of the
  // chart card sits over the sheet and Playwright's hit-test refuses the
  // click a real pointer would land.
  await page.locator('button:has-text("End attempt")').evaluate((el) => el.click());
  // The finish flush, and then the recap card — not the review.
  await page.waitForSelector("[data-review-now]", { timeout: 20_000 });
  check("the finish settled the sitting first",
    lastPut?.status === "finished" && (lastPut?.trades?.length ?? 0) === 1,
    `last PUT status=${lastPut?.status} trades=${lastPut?.trades?.length}`);
  check("End attempt does not open the review by itself",
    (await page.locator("[data-review-panel]").count()) === 0);
  check("…it offers it", (await page.locator("[data-review-later]").count()) === 1);

  // Review later marks the sitting and leaves you where you are. The PATCH is
  // asserted rather than the label, because the label is the thing most likely
  // to be reworded and the mark is the thing the history page reads.
  await page.locator("[data-review-later]").click();
  await page.waitForTimeout(600);
  check("Review later marks the sitting", lastPatch?.review_later === true,
    JSON.stringify(lastPatch));
  check("…and stays out of the review",
    (await page.locator("[data-review-panel]").count()) === 0);

  // Review now is the door that still leads in, through the same reload.
  await page.locator("[data-review-now]").click();
  await page.waitForSelector("[data-review-panel]", { timeout: 20_000 });
  check("Review now lands in the review", true);
  check("the ended sitting's trade has a card",
    (await page.locator("[data-review-trade]").count()) >= 1,
    `${await page.locator("[data-review-trade]").count()} card(s)`);
  check("the ticket is gone", (await page.locator(".sim-ticket").count()) === 0);
  // Scenario 1's 409 is deliberate — the browser logs it as a console error
  // and that is the one entry this file is allowed to see.
  const unexpected = errors.filter((e) => !e.includes("409"));
  check("no console errors beyond the deliberate 409", unexpected.length === 0,
    unexpected.slice(0, 2).join(" | "));
} finally {
  await page.goto(`${BASE}/`).catch(() => {});
  await page.evaluate(() => {
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.review.replay");
    localStorage.removeItem("sim.review.drill");
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
  }).catch(() => {});
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
