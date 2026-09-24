// Throwaway: "File review" saves the cards itself.
//
// It used to refuse to light up until every card had been Saved by hand — a
// seventeen-trade review was seventeen clicks of bookkeeping before the one
// click that meant anything. Now a card that is *answered* on screen counts,
// and File writes whatever is still dirty before it files. What this drives:
//
//   1. two cards answered (model + tag), **neither one Saved**
//   2. File is offered anyway
//   3. pressing it PUTs both rows, then PATCHes the attempt to `reviewed`
//   4. and the PUTs carry what was typed, not what was stored
//
// **It writes nothing.** Every /api/replays and /api/notes call is answered
// here, against a made-up attempt id — the point of the check is the requests
// that leave the browser, which is exactly what a stub can see.
import { launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const ID = "2026-01-02_NQH6_20260102T000000Z";
const puts = [];
let patched = null;

const row = (key, clock, dir) => ({
  trade_key: key,
  direction: dir,
  entry_ts_local: `2026-01-02 ${clock}:00`,
  net_pnl: dir === "long" ? 120 : -80,
  model_id: null,
  rules_met: [],
  reviewed: false,
  note: "",
  tags: [],
  setups: [],
  confluences: [],
});
const detail = {
  id: ID,
  symbol: "NQH6",
  date: "2026-01-02",
  root: "NQ",
  tz: "New York",
  mode: "replay",
  engine_version: 1,
  // Deliberately not this tape's fingerprint: an unusable log costs the trades
  // on the chart and never the review, which is what is being checked.
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
  summary: { trades: 2 },
  log: { orders: [], closes: [], brackets: [] },
  trades: [
    { id: 1, entryMs: 0, exitMs: 0, side: "long", size: 1, entry: 0, exit: 0, pnl: 120 },
    { id: 2, entryMs: 0, exitMs: 0, side: "short", size: 1, entry: 0, exit: 0, pnl: -80 },
  ],
  discarded: [],
  // No flags: this file is about the per-trade half. accountcheck drives the
  // flag/verdict machinery.
  flags: [],
};

await page.route("**/api/notes/**", (route) => {
  if (route.request().method() === "PUT") {
    puts.push({ url: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
    return route.fulfill({ json: { ok: true } });
  }
  return route.fallback();
});

await page.route("**/api/replays**", (route) => {
  const url = new URL(route.request().url());
  const method = route.request().method();
  if (url.pathname.endsWith("/replays/account"))
    return route.fulfill({
      json: {
        now: "2026-01-02T12:00:00Z",
        equity: 50000, floor: 48000, peak_close: 50000, status: "live",
        day_net: 0, day_loss_remaining: 1200, target_remaining: 3000,
        next_sitting_at: null, cooldown_until: null, can_reset: false,
        epoch: { index: 0, started_at: "2026-01-01T00:00:00Z", sittings: 0, net: 0 },
        last_death: null, caps: { minis: 4, micros: 40 }, counted_ids: [],
        review_block: { attempt_id: ID, flags: [] },
      },
    });
  if (url.pathname.endsWith(`/replays/${ID}/journal`))
    return route.fulfill({ json: { trades: [row("k1", "09:38", "long"), row("k2", "09:44", "short")] } });
  if (method === "PATCH" && url.pathname.endsWith(`/replays/${ID}`)) {
    patched = route.request().postDataJSON();
    return route.fulfill({ json: { ...detail, status: "reviewed" } });
  }
  if (url.pathname.endsWith(`/replays/${ID}`)) return route.fulfill({ json: detail });
  if (url.pathname.endsWith("/api/replays") && method === "GET")
    return route.fulfill({ json: { attempts: [detail] } });
  // Nothing else may reach the real API with a made-up id.
  if (method !== "GET") return route.fulfill({ json: { id: ID, status: "finished" } });
  return route.fallback();
});

const file = page.locator("[data-review-file]");

try {
  await page.goto("http://localhost:5173/charts/replay", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    for (const k of ["sim.review", "sim.review.replay", "sim.resume", "sim.resume.replay"])
      localStorage.removeItem(k);
  });
  await openChart(page, "/charts/replay");
  await page.waitForTimeout(2000);

  const cards = await page.locator("[data-review-trade]").count();
  check("the owed sitting opens as a review with both cards", cards === 2, `${cards} card(s)`);
  check("nothing is answered yet, so File is refused", await file.isDisabled(),
    (await file.textContent())?.trim());

  // Answer both cards — model and one tag each — and **do not press Save**.
  for (const i of [0, 1]) {
    const card = page.locator(`[data-review-trade="${i}"]`);
    await card.locator("[data-review-model]").selectOption("none");
    const tags = card.locator("input").first();
    await tags.fill(`tag-${i}`);
    await tags.press("Enter");
    await page.waitForTimeout(150);
  }
  const saveBtns = page.locator("[data-review-save]");
  const unsaved = await saveBtns.evaluateAll((els) => els.filter((e) => e.textContent.trim() === "Save").length);
  check("both cards are dirty (Save never pressed)", unsaved === 2, `${unsaved} showing "Save"`);
  check("and File is offered anyway", !(await file.isDisabled()), (await file.textContent())?.trim());

  await file.click();
  await page.waitForTimeout(1500);

  check(`filing wrote both rows (${puts.length} PUT(s))`, puts.length === 2,
    puts.map((p) => p.url.split("/").pop()).join(", "));
  check("with the tags that were typed",
    puts.every((p, i) => p.body?.tags?.includes(`tag-${i}`)),
    JSON.stringify(puts.map((p) => p.body?.tags)));
  check("and the attempt was filed as reviewed", patched?.status === "reviewed",
    JSON.stringify(patched)?.slice(0, 120) ?? "no PATCH");

  check(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  console.log(`\n!! ${e?.stack ?? e}`);
  results.push(["ran to the end", false, String(e?.message ?? e)]);
} finally {
  console.log(`\n${results.filter((r) => r[1]).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.every((r) => r[1]) ? 0 : 1);
}
