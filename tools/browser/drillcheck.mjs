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
    if (url.pathname.endsWith("/journal")) return route.fulfill({ json: { trades: [] } });
    return route.fulfill({
      json: { id: openId, status: "active", repeat_index: 0, note: "", model_id: 1,
              trades: [], log: { orders: [], closes: [], brackets: [] }, discarded: [] },
    });
  }
  if (method !== "GET") return route.fulfill({ json: { ok: true } });
  return route.fallback();
});

let openId = null;

let lastCreate = null;

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
    localStorage.removeItem("sim.review");
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

  // --- forward only ---------------------------------------------------------
  const back = page.locator('.sim-transport button[title*="drill"]');
  check("the rewind button is refused", await back.isDisabled(),
    (await back.getAttribute("title"))?.slice(0, 60));
  // And the key too, which is the path a button state cannot speak for.
  const before = await titleClock();
  await page.locator('.sim-pane[data-pane="0"]').hover();
  await page.keyboard.press(",");
  await page.waitForTimeout(600);
  check("the , key is refused as well", (await titleClock()) === before,
    `${before} → ${await titleClock()}`);

  // --- ending a rep ---------------------------------------------------------
  await page.locator('button:has-text("End rep")').click();
  await page.waitForTimeout(1200);
  const revealed = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  check("ending the rep reveals the day", !revealed.includes("▨"), revealed.trim());
  const review = page.locator(".sim-sec-t:has-text('Rep over')");
  check("the review is offered", (await review.count()) > 0);
  // Never forced: the next draw is live with nothing answered. This is the
  // assertion the whole "offered, never forced" decision rests on.
  const next = page.locator('button:has-text("Next rep")');
  check("and 🎲 is live with nothing answered", !(await next.isDisabled()));

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
  }).catch(() => {});
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
