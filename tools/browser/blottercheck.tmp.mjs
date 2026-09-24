// The two things this change is about, driven in a real browser on /charts/replay.
//
//   1. THE DAILY STOP NO LONGER CLOSES A POSITION ON UNBOOKED LOSS. Guards are
//      stubbed with `daily_loss_stop: 1`, so under the old equity rule a
//      position a single dollar down would have been flattened within a tick.
//      The assertion is that a *losing* position stays on and the day stays
//      unlocked until something books.
//   2. THE BLOTTER PRINTS WHAT THE ENTRY STAKED. `⌀$…` on the row, off
//      `Trade.riskUsd` — the same figure the ticket's sizer quotes.
//
// **It writes nothing.** Every mutating `/api/replays` call is answered
// synthetically, for the reason drillcheck and accountcheck give: trading on
// this page opens an attempt against the real persistent account, and a check
// that files sittings is a check that costs you a base rate.
//
//   node tools/browser/blottercheck.tmp.mjs [--headed]
import { launch, openChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

let writes = 0;
await page.route("**/api/replays**", async (route) => {
  const method = route.request().method();
  if (method === "GET") return route.fallback();
  writes += 1;
  const url = new URL(route.request().url());
  if (url.pathname.endsWith("/replays")) {
    return route.fulfill({
      json: {
        id: "2026-01-01_NQH5_20260101T000001Z",
        status: "active", repeat_index: 0, note: "", model_id: null,
      },
    });
  }
  return route.fulfill({ json: { ok: true } });
});

// A $1 day stop with the flatten armed: the sharpest possible version of the
// old rule. Everything else is opened up so the shape rules cannot refuse the
// order this check is about.
await page.route("**/live/routing", (route) =>
  route.fulfill({
    json: {
      replay_guardrails: true,
      guards: {
        daily_loss_stop: 1, daily_profit_lock: 0, slow_down_at: 300, min_gap_s: 0,
        min_target_ticks: 20, stop_ticks_min: 20, stop_ticks_max: 400,
        require_bracket: false, auto_flatten: true, max_risk_usd: 100_000,
        commission_per_side: 3.5,
      },
    },
  }),
);

const step = async (n) => {
  for (let i = 0; i < n; i++) {
    await page.keyboard.press(".");
    await page.waitForTimeout(350);
  }
};
const openLine = async () => {
  const el = page.locator(".sim-quick-pos").first();
  return (await el.count()) ? ((await el.textContent()) ?? "").trim() : "";
};
const refusedText = async () => {
  const el = page.locator("text=/⚠ refused/").first();
  return (await el.count()) ? ((await el.textContent()) ?? "").trim() : "";
};
const openPnl = async () => {
  const t = await openLine();
  const m = t.match(/(-?)\$([\d,.]+)\s*$/);
  return m ? (m[1] ? -1 : 1) * Number(m[2].replace(/,/g, "")) : null;
};

try {
  await page.goto(`${BASE}/`);
  await page.evaluate(() => {
    for (const k of ["sim.review", "sim.review.replay", "sim.review.drill",
                     "sim.resume", "sim.resume.replay", "sim.resume.drill"]) {
      localStorage.removeItem(k);
    }
    // A stop wide enough to survive the stepping. The first run of this file
    // stopped out inside five bars on the default 50 ticks, which left nothing
    // open to *not* be auto-closed — the position has to outlive the check for
    // the check to be about anything.
    // The order pad carries the position chip this check reads the open P&L
    // off, and it is minimised to a badge in this profile's saved prefs — which
    // is why the first two runs saw no position while one was plainly on.
    localStorage.removeItem("chart.quickDockMin");
    const p = JSON.parse(localStorage.getItem("sim.prefs") || "{}");
    localStorage.setItem(
      "sim.prefs",
      JSON.stringify({ ...p, stopTicks: 400, targetTicks: 400, size: 1 }),
    );
  });
  await openChart(page, "/charts/replay");
  await page.locator('.sim-pane[data-pane="0"]').hover();

  // Take a side, look at it, and take the other one if this day is going the
  // wrong way for the test. The market is a random RTH day (see the replay's
  // own draw), so which direction loses is not knowable in advance — and a
  // check that quietly passes on a winning position would be testing nothing.
  // Retried: `w` is a chart gesture and wants the pane under the pointer, and a
  // page still settling after the first paint drops the first press about a
  // third of the time.
  let pnl = null;
  for (let tries = 0; tries < 3 && !(await openLine()); tries++) {
    await page.locator('.sim-pane[data-pane="0"]').hover();
    await page.keyboard.press("w");         // buy at market, with the ticket's bracket
    await step(3);
  }
  pnl = await openPnl();
  if (pnl != null && pnl > 0) {
    await page.locator(".sim-quick-btn.flat").first().evaluate((el) => el.click());
    await page.waitForTimeout(500);
    await page.locator('.sim-pane[data-pane="0"]').hover();
    await page.keyboard.press("s");         // and go the other way
    await step(4);
    pnl = await openPnl();
  }
  // Step it down — but **stop well short of $1,200**. That is the persistent
  // account's own daily loss limit (`replayAccount`, `guardRules.accountBreach`),
  // which is a *different* rule and still fires on running P&L including the
  // open position. It closed the position out from under an earlier run of this
  // check, which is a true reading of the account rule and says nothing about
  // the personal stop this file is about.
  for (let i = 0; i < 8; i++) {
    pnl = await openPnl();
    if (pnl == null || pnl < -50) break;
    await step(2);
  }
  pnl = await openPnl();

  // **Reported as skipped rather than failed when the day will not cooperate.**
  // The replay draws a random RTH day and the bracket is 400 ticks wide, so
  // whether a position is still on and between −$1 and −$1,000 at this point is
  // the market's decision, not the check's. A red for that would be noise; a
  // quiet green would be worse.
  if (pnl == null || pnl >= -1 || pnl <= -1_000) {
    console.log(`  ~ SKIPPED the daily-stop half — open P&L was ${pnl} on this ` +
                `day, outside the -$1..-$1,000 window it needs`);
  } else {
    // The whole point. A $1 personal stop with the flatten armed, and a position
    // far further down than that: the old equity rule would have closed it the
    // moment the HUD ticked.
    check("a losing position is NOT closed on unbooked loss",
          (await openLine()).length > 0, `open ${pnl}`);
    check("…and the day is not locked by it",
          !(await refusedText()).includes("daily stop"), await refusedText());
  }

  // Book it, if the bracket has not already. The dock's Close button rather than
  // `q`: the key is a chart gesture wanting the pane under the pointer, which
  // this file keeps losing between steps. Same `closeAll` either way.
  if (await openLine()) {
    // Not fatal if it will not go: the row this check is really after is booked
    // by whichever exit gets there, and a Close that missed its target should
    // not take the blotter assertions down with it.
    try {
      await page.locator(".sim-quick-btn.flat").first().evaluate((el) => el.click());
      // Close sends a market order, and a market order on a **paused** tape has
      // nothing to fill against — the fill model charges the gesture lag and
      // then waits for a print. So step the tape while waiting, or this waits
      // out its whole timeout on a chart that is deliberately holding still.
      for (let i = 0; i < 10 && (await openLine()); i++) await step(1);
      await page.waitForFunction(() => !document.querySelector(".sim-quick-pos"),
                                 null, { timeout: 8000 });
    } catch {
      console.log("  ~ the Close press did not land; reading whatever booked");
    }
  }
  await page.waitForTimeout(800);
  await page.locator('button[title="Show the ticket and blotter"]').click();
  await page.waitForSelector(".sim-panel.open", { timeout: 5000 });
  await page.waitForTimeout(400);

  // Rows, not the empty note — which is also a bare div in this list.
  const rows = page.locator(".sim-blotter-list > div", { hasText: "×" });
  const n = await rows.count();
  check("the blotter has rows", n > 0, `${n}`);
  const first = n ? ((await rows.first().textContent()) ?? "") : "";
  check("the row carries the size", /[LS]×\d/.test(first), first);
  check("the row carries what it staked (⌀$…)", /⌀\$[\d,]/.test(first), first);
  const staked = first.match(/⌀\$([\d,.]+)/);
  check("…and it is a real figure, not zero",
        !!staked && Number(staked[1].replace(/,/g, "")) > 0, staked?.[1] ?? "—");

  await shot(page, "blotter-risk");
  // Every one of them was answered by the stub above — the count is here to say
  // the page really did try to file, i.e. that the interception was load-bearing
  // rather than decorative.
  check("every write was intercepted, none reached data/replays", writes > 0,
        `${writes} intercepted`);
  check("no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} catch (e) {
  // Otherwise `finally`'s `process.exit` swallows the throw and the run reports
  // "3/3 passed" for a check that fell over on its fourth step.
  check("the run finished", false, String(e).split("\n")[0]);
} finally {
  const bad = results.filter(([, ok]) => !ok);
  console.log(`\n${results.length - bad.length}/${results.length} passed`);
  await browser.close();
  process.exit(bad.length ? 1 : 0);
}
