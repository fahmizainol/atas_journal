// Backtest mode, driven end to end in a real browser.
//
// Everything the server decides about a drill is already covered by pytest —
// that a drill is invisible to the account, that the sweep deletes an empty one,
// that the campaign counts sat-out reps. What no pytest can see is the half this
// file is for: that the third tab exists and lands on a page with a model
// picker, that the drop is somewhere other than the open and different on the
// next draw, that the day really is hidden, that ⏪ is refused, and that ending a
// rep offers the review rather than forcing it.
//
//   node drillcheck.mjs
//   node drillcheck.mjs --headed
//   APP_URL=http://localhost:4300 node drillcheck.mjs
//
// **It writes nothing.** `POST /replays` is answered synthetically, for the
// reason written into panecheck and accountcheck: backtest mode opens its
// attempt at the *drop* rather than on the first fill, so merely loading this
// page would put a rep into data/replays and into the journal — and this check
// draws several. It would leave a campaign nobody sat.
import { launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");

/** The browser copy of `DRILL_REVIEW_REQUIRED` in api/routers/replays.py — the
 *  third of the three mirrors that flip together (the other is the constant in
 *  pages/Simulator.tsx). With the gate off the 🎲 lock does not engage, so the
 *  lock assertions are *skipped and said so* rather than left to fail: a check
 *  that disagrees with a deliberate setting is noise, and one that quietly
 *  passes anyway is worse. What still runs either way is the panel itself. */
const DRILL_REVIEW_REQUIRED = false;
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

let created = 0;

// One handler for the family, dispatching on path — two `page.route` calls
// would collide, since `**/replays/*` matches `/replays/account` too.
await page.route("**/api/replays**", async (route) => {
  const url = new URL(route.request().url());
  const method = route.request().method();
  if (url.pathname.endsWith("/replays/drills")) {
    return route.fulfill({
      json: {
        model_id: 1, reps: 0, traded_reps: 0, sat_out: 0, base_rate: null,
        trades: 0, net_usd: 0, expectancy: null,
        drawn_by_hour: [], traded_by_hour: [],
      },
    });
  }
  if (method === "POST" && url.pathname.endsWith("/replays")) {
    created += 1;
    // What the page asked for, echoed back. Asserting on the *request* below is
    // the only way to know the mode and the drop left the browser at all.
    const body = route.request().postDataJSON() ?? {};
    lastCreate = body;
    openId = `2026-01-01_NQH5_2026010100000${created}Z`.replace(/(\d{8})(\d{6}Z)$/, "$1T$2");
    return route.fulfill({
      json: {
        id: openId,
        status: "active", repeat_index: 0, note: "", model_id: body.model_id ?? null,
      },
    });
  }
  // The rep's own record and its journal rows. Both have to be answered here or
  // they fall through to the real API with an id no attempt has — which returns
  // 400 rather than 404, because `attempt_dir` rejects a malformed id before it
  // ever looks for the folder. That is how the first run of this file found its
  // own id template was missing the `T`.
  if (method === "GET" && openId && url.pathname.includes(openId)) {
    // One journal row for the rep's one fill, echoing whatever the review has
    // saved so far — the page re-fetches after every save and decides the 🎲
    // lock off what comes back, exactly as the real mirror would. Unanswered to
    // start: that is the state the lock is about.
    if (url.pathname.endsWith("/journal"))
      return route.fulfill({ json: { trades: [{
        trade_key: "k1", direction: "long", entry_ts_local: "2025-06-18 13:05:00",
        net_pnl: 120, model_id: 1, rules_met: [], reviewed: false,
        note: "", tags: saved.tags, setups: [], confluences: [],
        grade: null, setup: saved.setup, discipline: saved.discipline,
        watched_levels: saved.watched_levels,
        levels: [
          { id: "vwap", label: "NY VWAP", family: "session_mean", dist_ticks: 3, rank: 0.6 },
        ],
        context_chips: [
          { key: "approach", label: "choppy approach · 0.21", title: "stubbed" },
        ],
      }] } });
    return route.fulfill({
      json: { id: openId, status: "active", repeat_index: 0, note: "", model_id: 1,
              trades: [], log: { orders: [], closes: [], brackets: [] }, discarded: [] },
    });
  }
  // The attempts list, for the rep counter on the bar. Empty: this check makes
  // its own reps, and a real one leaking in would move the count. (It also fed
  // the auto-review gate until 2026-08-25 — nothing opens itself now.)
  if (method === "GET" && url.pathname.endsWith("/api/replays"))
    return route.fulfill({ json: { attempts: [] } });
  if (method !== "GET") return route.fulfill({ json: { ok: true } });
  return route.fallback();
});

let openId = null;

let lastCreate = null;

// What the review has saved for the rep's one trade. All empty — a trade owing
// a level, a setup and a discipline call is the state the 🎲 lock is about.
let saved = { setup: null, discipline: null, watched_levels: [], tags: [] };

// The review is one write now (`PUT /notes`), the grade and watched level being
// partial fields on it. Stubbed so a check that reviews a trade writes no real
// journal row. Method before path (trap 9): the PUT and the tag-vocabulary GET
// share a prefix.
await page.route("**/api/notes/**", (route) => {
  const method = route.request().method();
  if (method === "PUT") {
    const body = JSON.parse(route.request().postData() ?? "{}");
    // Partial, exactly as the server treats them: null means unchanged, which
    // is what lets a note save land without blanking a grade. The level answer
    // is a list that REPLACES — deselecting one of several has to persist — so
    // only an omitted field falls through to what was already stored.
    saved = {
      setup: body.setup ?? saved.setup,
      discipline: body.discipline ?? saved.discipline,
      watched_levels: body.watched_levels ?? saved.watched_levels,
      tags: body.tags ?? saved.tags,
    };
    return route.fulfill({ json: { ok: true } });
  }
  if (method === "GET" && new URL(route.request().url()).pathname.endsWith("/notes/tags"))
    return route.fulfill({ json: { tags: ["oversized", "patient entry"] } });
  return route.fallback();
});
await page.route("**/api/review/vocab", (route) =>
  route.fulfill({ json: {
    grades: [
      { id: "A", says: "everything lined up" },
      { id: "B", says: "bread and butter" },
      { id: "C", says: "a feeler" },
      { id: "D", says: "should not have been taken" },
    ],
    setups: [
      { id: "faded_rally", says: "against the run" },
      { id: "joined_breakout", says: "with a break" },
      { id: "test", says: "a feeler" },
    ],
    disciplines: [
      { id: "clean", says: "to plan" },
      { id: "fomo", says: "chased" },
    ],
    no_level: "none",
  } }));

// A model with rules, so the review has something to draw. Real models on this
// instance would do, but a check that depends on the journal's contents fails
// for reasons that have nothing to do with the code under it.
await page.route("**/api/models/list**", (route) =>
  route.fulfill({
    json: {
      models: [
        {
          id: 1, name: "Drill target", description: "", archived: false,
          folder: null, target_sample: null,
          rules: [
            { id: 11, model_id: 1, label: "afternoon session", sort_order: 0 },
            { id: 12, model_id: 1, label: "touch of developing VAH", sort_order: 1 },
          ],
        },
      ],
    },
  }),
);

// Same reason accountcheck answers this outright: with no Rithmic session up,
// `route.fetch()` on /live/routing reaches the broker and hangs until
// Playwright's 30s route timeout kills the run.
await page.route("**/live/routing", (route) =>
  route.fulfill({
    json: {
      replay_guardrails: true,
      guards: {
        daily_loss_stop: 500, daily_profit_lock: 0, slow_down_at: 300,
        min_gap_s: 120, min_target_ticks: 20, stop_ticks_min: 20,
        stop_ticks_max: 400, require_bracket: true, auto_flatten: true,
        max_risk_usd: 100_000, commission_per_side: 3.5,
      },
    },
  }),
);

/** The clock the title is showing, as "HH:MM", or "". The title is the setup
 *  panel's trigger and carries the drop — which is the only place the drop is
 *  legible without revealing the day. */
const titleClock = async () => {
  const t = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  const m = t.match(/(\d{2}):(\d{2})/);
  return m ? m[0] : "";
};

/** Where the tape actually *is*, as "HH:MM:SS", or "". Not the title: in a
 *  drill the title's clock is the drop, a fixed property of the rep, so it is
 *  the right read for "where was I dropped" and the wrong one for anything
 *  that moves. The transport's own clock is the running one. Sliced off the
 *  front because the same element can carry a bar countdown behind it. */
const runningClock = async () => {
  const t = (await page.locator(".sim-clock").first().innerText()) ?? "";
  const m = t.match(/\d{2}:\d{2}:\d{2}/);
  return m ? m[0] : "";
};

try {
  // Bind the model before the first load: the page refuses to draw without one,
  // which is itself asserted below by clearing it again.
  await page.goto(`${process.env.APP_URL ?? "http://localhost:5173"}/charts/replay`);
  await page.evaluate(() => {
    localStorage.setItem("sim.drill", JSON.stringify({
      modelId: 1, dropFrom: "11:00", dropTo: "14:00",
    }));
    // A bookmark left by a real replay session would be fetched on load and
    // fail against the stub. A drill ignores it for trading, but the detail
    // query does not know that.
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.review.replay");
    localStorage.removeItem("sim.review.drill");
  });

  await openChart(page, "/charts/backtest");

  // --- the tab and the binding ---------------------------------------------
  const tabs = await page.locator(".chart-topbar-tabs a").allTextContents();
  check("Charts has three tabs", tabs.join("|") === "Replay|Live|Backtest", tabs.join("|"));
  const title = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  check("the bar names the bound model", title.includes("Drill target"), title.trim());
  check("the day is hidden", title.includes("▨"), title.trim());

  // --- the drop -------------------------------------------------------------
  const first = await titleClock();
  const inWindow = (t) => t >= "11:00" && t <= "14:00";
  check("the rep starts inside the drawn window, not at the open", inWindow(first), first);
  check("the create carried the mode and the drop", lastCreate?.mode === "drill" && lastCreate?.drop_ms != null,
    `mode=${lastCreate?.mode} model=${lastCreate?.model_id} drop=${lastCreate?.drop_ms != null}`);
  check("the attempt opened at the drop, with no fill", created === 1, `${created} created`);

  // Draw again until the clock differs. Three tries, because two draws from a
  // 181-minute window landing on the same minute is a 1-in-181 event and a
  // check that fails that often is a check nobody trusts.
  await page.locator(".chart-topbar-title").first().click();
  let second = first;
  for (let i = 0; i < 3 && second === first; i++) {
    await page.locator('.sim-setup button[title*="Draw"]').click();
    await page.waitForTimeout(1500);
    second = await titleClock();
  }
  check("the next rep lands somewhere else", second !== first, `${first} → ${second}`);
  check("and inside the window too", inWindow(second), second);
  await page.keyboard.press("Escape");

  // --- the rep runs backwards too ------------------------------------------
  // It did not, until D8 was superseded: a drill refused every backward move,
  // and this asserted the refusal. The rewind is now allowed and *recorded*
  // instead, so what there is to check is that the clock actually moves —
  // through the button and through the key, which is the path a button state
  // cannot speak for.
  //
  // The *running* clock, not the title's: the title carries the drop, which a
  // rewind is not supposed to move. Asserting on it read as "the rewind does
  // nothing" for as long as it was there, which is the failure a check that
  // watches the wrong element always produces — loud, and about nothing.
  const back = page.locator('.sim-transport button[title^="One bar back"]');
  check("the rewind button is live", !(await back.isDisabled()));
  const before = await runningClock();
  await back.click();
  await page.waitForTimeout(600);
  const stepped = await runningClock();
  check("⏮ walks the rep back a bar", !!stepped && stepped < before, `${before} → ${stepped}`);
  await page.locator('.sim-pane[data-pane="0"]').hover();
  await page.keyboard.press(",");
  await page.waitForTimeout(600);
  const keyed = await runningClock();
  check("the , key walks it back as well", !!keyed && keyed < stepped, `${stepped} → ${keyed}`);

  // --- the transport survives a fill ---------------------------------------
  // The row stays with size on — on both tabs now, since the Replay stopped
  // hiding it and started refusing only the rewind past the entry. That rule is
  // a drill's too since it gained a rewind at all, so Play and the speed stay
  // reachable through the one stretch of a rep where they matter most, and the
  // one thing refused there is the drag through your own fill.
  const transport = page.locator(".sim-transport");
  // 1×, so the tape run below is a print or two rather than a chunk of session.
  await page.selectOption(".sim-transport select", "1");
  await page.evaluate(() => document.activeElement?.blur?.());

  // --- the ticket asks nothing before the order -----------------------------
  // It briefly carried a thesis chip. Grading moved to the review by decision
  // G3 (docs/trade-grading-plan.md) — a scale on the fast path is one that gets
  // answered carelessly — so the fast path is bare again, and this asserts it
  // stayed that way rather than growing a new question.
  check("the ticket carries no claim chip",
    (await page.locator("button.sim-knob-rr.thesis").count()) === 0);

  await page.keyboard.press("w");
  // The order is placed on the key and fills on the next print — a paused tape
  // has no next print, so the position does not exist until the clock moves.
  //
  // **Playing, not `.`.** A step looks more deterministic and is worse: one 1m
  // step is sixty seconds of tape at once, and the position can open *and* stop
  // out inside it — which reads exactly like "w placed nothing" and flaked this
  // check about one run in five. Polling with the tape running catches the fill
  // whenever it lands.
  const held = () => page.locator('.sim-quick-pos:has-text("LONG")').count();
  // Toggling `k` blind is what made this section flaky in both directions: a
  // fill that lands on the first poll leaves the pair of presses one apart, and
  // the tape is then still running when the rep is ended below. Ask the button
  // what state it is in instead — it says Pause while playing — and press only
  // when the answer is wrong.
  const isPlaying = async () =>
    ((await page.locator(".sim-transport button").first().textContent()) ?? "").includes("Pause");
  const setPlaying = async (want) => {
    if ((await isPlaying()) !== want) {
      await page.keyboard.press("k");
      await page.waitForTimeout(250);
    }
  };
  const runUntil = async (done) => {
    await setPlaying(true);
    for (let i = 0; i < 12 && !(await done()); i++) await page.waitForTimeout(400);
    await setPlaying(false);
    await page.waitForTimeout(250);
  };
  await runUntil(async () => (await held()) > 0);
  // One retry, and only when the first `w` left *nothing* behind. `placeMarket`
  // returns silently while the page is not `ready` — no order, no refusal — and
  // the redraw two checks up puts this press near that window. Safe because the
  // run above is five seconds of tape: a market order that had been placed would
  // have filled inside it, so "still flat" means there is no order to double.
  if (!(await held())) {
    await page.keyboard.press("w");
    await runUntil(async () => (await held()) > 0);
  }
  check("w fills in a drill", (await held()) > 0);
  check("and the transport stays with a position on",
    !((await transport.getAttribute("class")) ?? "").includes("away"),
    (await transport.getAttribute("class")) ?? "");
  // The exit is a market order too, so it also wants a print.
  await page.keyboard.press("q");
  await runUntil(async () => (await held()) === 0);

  // --- ending a rep ---------------------------------------------------------
  await page.locator('button:has-text("End rep")').click();
  await page.waitForTimeout(1200);
  const revealed = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  check("ending the rep reveals the day", !revealed.includes("▨"), revealed.trim());
  const review = page.locator(".sim-sec-t:has-text('Rep over')");
  check("the review is offered", (await review.count()) > 0);
  // Forced: the next draw waits until every trade of this rep is answered for.
  // The server refuses the create the same way, so this lock is the honest copy
  // rather than the gate. What "answered" means became a watched level, a setup
  // and a discipline call on 2026-08-31 (the grade moved to the recall front).
  const next = page.locator('button:has-text("Next rep")');
  const drill = page.locator("[data-drill-trade]");

  if (DRILL_REVIEW_REQUIRED) {
    check("🎲 is locked while the rep's trade is unreviewed", await next.isDisabled());
    // Answered one field at a time, because a check that sent all three at once
    // would pass against a server gating on any one of them.
    await drill.locator('[data-review-level-pick="vwap"]').click();
    await drill.locator('button:has-text("Save")').click();
    await page.waitForTimeout(600);
    check("a level alone leaves 🎲 locked", await next.isDisabled());

    await drill.locator('[data-review-setup-pick="faded_rally"]').click();
    await drill.locator('button:has-text("Save")').click();
    await page.waitForTimeout(600);
    check("a level and a setup leave 🎲 locked", await next.isDisabled());

    await drill.locator('[data-review-discipline-pick="clean"]').click();
    await drill.locator('button:has-text("Save")').click();
    // Two round trips — the PUT, then the journal re-fetch that flips the lock.
    for (let i = 0; i < 10 && (await next.isDisabled()); i++) await page.waitForTimeout(300);
    check("all three on every trade unlocks 🎲", !(await next.isDisabled()));
  } else {
    console.log("  --  🎲 lock assertions skipped — DRILL_REVIEW_REQUIRED is off");
    // The gate standing down does not excuse the panel: the answers still have
    // to reach the server, which is what the rest of the review is built on.
    await drill.locator('[data-review-level-pick="vwap"]').click();
    await drill.locator('[data-review-setup-pick="faded_rally"]').click();
    await drill.locator('[data-review-discipline-pick="clean"]').click();
    const tagBox = drill.locator("[data-tag-input] input");
    await tagBox.fill("chased it");
    await tagBox.press("Enter");
    await page.waitForTimeout(200);
    check("the card offers the axes, the chips and no grade",
      (await drill.locator("[data-review-setup-pick]").count()) === 3 &&
        (await drill.locator("[data-review-discipline-pick]").count()) === 2 &&
        (await drill.locator("[data-review-context-chip]").count()) === 1 &&
        (await drill.locator("[data-review-grade-pick]").count()) === 0 &&
        (await drill.locator('[data-tag="chased it"]').count()) === 1);
    await drill.locator('button:has-text("Save")').click();
    for (let i = 0; i < 10; i++) {
      if ((await drill.locator('button:has-text("Saved")').count()) === 1) break;
      await page.waitForTimeout(300);
    }
    check("saving the review lands and the card goes clean",
      (await drill.locator('button:has-text("Saved")').count()) === 1,
      JSON.stringify(saved));
    check("the save carried all three", saved.setup === "faded_rally" &&
      saved.discipline === "clean" && saved.watched_levels.includes("vwap") &&
      saved.tags.includes("chased it"),
      JSON.stringify(saved));
  }

  // --- the guard on an unbound drill ---------------------------------------
  await page.evaluate(() =>
    localStorage.setItem("sim.drill", JSON.stringify({
      modelId: null, dropFrom: "11:00", dropTo: "14:00",
    })),
  );
  await openChart(page, "/charts/backtest");
  await page.locator(".chart-topbar-title").first().click();
  const die = page.locator('.sim-setup button[title*="Bind a model"]');
  check("an unbound drill cannot draw", (await die.count()) > 0 && (await die.isDisabled()));

  check("no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  await page.evaluate(() => {
    localStorage.removeItem("sim.drill");
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
  }).catch(() => {});
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
