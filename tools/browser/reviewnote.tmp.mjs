// Throwaway: the review's session box — one field for the whole sitting.
//
// What it drives:
//
//   1. an owed sitting opens in review with its two cards
//   2. the session box is seeded with the note the attempt already carried
//   3. answering both cards (grade · level · tag) offers File
//   4. pressing it PATCHes `note` alongside `status: reviewed` — one act
//
// **It writes nothing.** Every /api/replays call is answered here against a
// made-up attempt id, and the per-trade PUTs are swallowed; the point of the
// check is the request that leaves the browser, which is what a stub can see.
import { launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const ID = "2026-01-02_NQH6_20260102T000000Z";
const STORED_NOTE = "stored note from an earlier visit";
const TYPED = "chop until 10:30, then it trended and I kept fading it";
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
  grade: null,
  watched_level: null,
  levels: [],
});
const ROWS = [
  row("k1", "09:38", "long"), row("k2", "09:44", "short"), row("k3", "09:51", "long"),
  row("k4", "10:02", "short"), row("k5", "10:19", "long"), row("k6", "10:41", "short"),
];
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
  note: STORED_NOTE,
  model_id: null,
  rewinds: [],
  discarded_trades: 0,
  summary: { trades: 6 },
  log: { orders: [], closes: [], brackets: [] },
  trades: ROWS.map((r, i) => ({
    id: i + 1, entryMs: 0, exitMs: 0, side: r.direction, size: 1, entry: 0, exit: 0, pnl: r.net_pnl,
  })),
  discarded: [],
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
    return route.fulfill({ json: { trades: ROWS } });
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
const box = page.locator("[data-review-session-note]");

try {
  await page.goto("http://localhost:5173/charts/replay", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    for (const k of ["sim.review", "sim.review.replay", "sim.resume", "sim.resume.replay"])
      localStorage.removeItem(k);
  });
  await openChart(page, "/charts/replay");
  await page.waitForTimeout(2000);

  const cards = await page.locator("[data-review-trade]").count();
  check("the owed sitting opens as a review with every card", cards === ROWS.length, `${cards} card(s)`);
  // Presence is not the question — a box below the fold of a rail that does not
  // scroll is a box nobody can reach.
  const vp = page.viewportSize();
  const bb = await box.boundingBox();
  console.log("    viewport", JSON.stringify(vp), "box", JSON.stringify(bb));
  check("the session box is visible on screen without scrolling",
    !!bb && bb.y >= 0 && bb.y + bb.height <= vp.height, JSON.stringify(bb));
  const fbb = await file.boundingBox();
  check("and File review is still on screen under it",
    !!fbb && fbb.y + fbb.height <= vp.height, JSON.stringify(fbb));
  await page.screenshot({ path: "shots/reviewnote.png", fullPage: false });
  const probe = await page.evaluate(() => {
    const panel = document.querySelector(".sim-panel");
    const list = document.querySelector("[data-review-panel] [data-review-list]")
      || document.querySelector("[data-review-panel] > div:nth-child(3)");
    const cs = getComputedStyle(panel);
    return {
      page: document.querySelector(".sim-page")?.className,
      panel: { h: panel.clientHeight, scroll: panel.scrollHeight, overflowY: cs.overflowY, position: cs.position, maxH: cs.maxHeight },
      list: list && { h: list.clientHeight, scroll: list.scrollHeight, overflowY: getComputedStyle(list).overflowY },
    };
  });
  console.log("    probe", JSON.stringify(probe));
  check("there is one session box, not one per trade",
    (await box.count()) === 1, `${await box.count()}`);
  // Seeded, because filing sends whatever is in it: a review filed on a sitting
  // that already carried a note must not erase it.
  check("seeded with the note the attempt already carried",
    (await box.inputValue()) === STORED_NOTE, await box.inputValue());

  // Answer every card — grade, level, tag — and **do not press Save**.
  for (let i = 0; i < ROWS.length; i++) {
    const card = page.locator(`[data-review-trade="${i}"]`);
    await card.locator('[data-review-grade-pick="B"]').click();
    // Nothing was measured for these stub rows, so "no level" is the only chip
    // — and it is a real answer, which is the point of it always being offered.
    await card.locator("[data-review-level-pick]").last().click();
    const tags = card.locator("[data-tag-input] input");
    await tags.fill(`tag-${i}`);
    await tags.press("Enter");
    await page.waitForTimeout(150);
  }
  check("answering the cards offers File", !(await file.isDisabled()),
    (await file.textContent())?.trim());

  // The note is optional and nothing gates on it — File was already lit.
  await box.fill(TYPED);
  await page.waitForTimeout(100);

  await file.click();
  await page.waitForTimeout(1500);

  check(`filing wrote every row (${puts.length} PUT(s))`, puts.length === ROWS.length,
    puts.map((p) => p.url.split("/").pop()).join(", "));
  check("and the attempt was filed as reviewed", patched?.status === "reviewed",
    JSON.stringify(patched)?.slice(0, 140) ?? "no PATCH");
  check("the session note rode in the same PATCH", patched?.note === TYPED,
    JSON.stringify(patched?.note));

  check(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  console.log(`\n!! ${e?.stack ?? e}`);
  results.push(["ran to the end", false, String(e?.message ?? e)]);
} finally {
  console.log(`\n${results.filter((r) => r[1]).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.every((r) => r[1]) ? 0 : 1);
}
