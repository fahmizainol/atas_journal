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
import { BASE, healthyAccount, iso, launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

/** A live, healthy account — the shared stub in lib.mjs, so this file and every
 *  other check that needs a session with nothing owed serve the same payload.
 *  Every state below is it with two fields moved. */
const healthy = healthyAccount;

/** The registry the switcher lists. Two built-ins, as a fresh install has. */
const accountList = () => ({
  accounts: [
    { id: "funded", label: "LucidPro 50K", template: "lucid_pro", trailing: "eod",
      needs_cause: true, archived: false, epochs: 1, numbers: healthy().rules },
    { id: "paper", label: "Paper", template: "paper", trailing: "eod",
      needs_cause: false, archived: false, epochs: 1, numbers: healthy().rules },
  ],
  templates: [
    { key: "lucid_pro", label: "LucidPro", trailing: "eod", needs_cause: true,
      note: "End-of-day trailing drawdown.", defaults: healthy().rules },
    { key: "lucid_daily", label: "LucidDaily", trailing: "intraday", needs_cause: true,
      note: "Intraday trailing drawdown.", defaults: healthy().rules },
    { key: "paper", label: "Paper", trailing: "eod", needs_cause: false,
      note: "Nothing at stake but the account.", defaults: healthy().rules },
  ],
});

// Mutated between reloads; the route handler below always serves the current one.
let account = healthy();
// The other account, served when the page asks with `?mode=paper`. Deliberately
// a different equity from `healthy()`: it is the only way to tell "the paper
// page fetched the paper account" from "the query answered whatever it had".
let paperAccount = { ...healthy(), account: "paper", label: "Paper", equity: 48_940 };
// The attempt the review stub answers for. Filled in once a real session is on
// screen — review mode only holds while the sitting it names is the session
// being looked at, which is the point of that rule and also what makes it
// impossible to fake with a hardcoded id.
let reviewDetail = null;
let reviewJournal = [];

// One handler for the whole family, dispatching on the path. Two `page.route`
// calls would collide: `**/replays/*` matches `/replays/account` as well, and
// which one wins is a fact about registration order rather than about intent.
await page.route("**/api/replays**", async (route) => {
  const url = new URL(route.request().url());
  // The registry, which the account switcher lists. Above the single-account
  // route: `/replays/accounts` also ends in something the check below would
  // not match, but keeping the more specific path first is the rule that stops
  // this from depending on `endsWith` never gaining a prefix.
  if (url.pathname.endsWith("/replays/accounts")) {
    return route.fulfill({ json: accountList() });
  }
  if (url.pathname.endsWith("/replays/account")) {
    // Asked for by id since accounts became a registry; the mode is still
    // accepted by the server, so both are honoured here.
    const id = url.searchParams.get("account") ?? url.searchParams.get("mode");
    return route.fulfill({ json: id === "paper" ? paperAccount : account });
  }
  if (reviewDetail && url.pathname.endsWith(`/replays/${reviewDetail.id}/journal`)) {
    // The review's per-trade half: one row already answered, one still owing.
    // The gate is what this section checks, so it needs both states on screen.
    return route.fulfill({ json: { trades: reviewJournal } });
  }
  if (reviewDetail && url.pathname.endsWith(`/replays/${reviewDetail.id}`)) {
    return route.fulfill({ json: reviewDetail });
  }
  // The detail of the sitting the floor section opens — a resume bookmark it
  // leaves behind would otherwise reach the real API with an id no attempt has.
  // GET only, or this branch would swallow the recorder's PUT to the same path
  // and the floor section would read `lastPut` as never written.
  if (route.request().method() === "GET" &&
      url.pathname.endsWith("/replays/2026-01-01_NQH5_20260101T000000Z")) {
    return route.fulfill({
      json: { id: "2026-01-01_NQH5_20260101T000000Z", status: "active", repeat_index: 0,
              note: "", model_id: null, rewinds: [], discarded_trades: 0, summary: {},
              trades: [], log: { orders: [], closes: [], brackets: [] }, discarded: [] },
    });
  }
  // Never write. Apart from the floor section — whose fill and flatten are the
  // point — every gesture in here is meant to be refused, so nothing *should*
  // reach the recorder; but a check that can put practice nobody sat into the
  // real track record is one bad assertion away from doing it.
  if (route.request().method() !== "GET") {
    if (route.request().method() === "PUT") lastPut = route.request().postDataJSON() ?? null;
    return route.fulfill({ json: { id: "2026-01-01_NQH5_20260101T000000Z", status: "active", repeat_index: 0, note: "", model_id: null } });
  }
  // The attempts list. Empty until a section that needs the page to *find* a
  // row (the auto-review) fills it — and answered outright either way, so a
  // fake id can never fall through to the real API's records.
  if (route.request().method() === "GET" && url.pathname.endsWith("/api/replays"))
    return route.fulfill({ json: { attempts: attemptsList } });
  return route.fallback();
});

// The body of the recorder's last PUT — how the floor section knows the death
// actually settled the sitting rather than just clearing the screen.
let lastPut = null;
// What GET /api/replays answers — see the dispatcher above.
let attemptsList = [];

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
    localStorage.removeItem("sim.review.replay");
    localStorage.removeItem("sim.review.drill");
    localStorage.removeItem("sim.review.paper");
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
    localStorage.removeItem("sim.resume.paper");
  });
  await openChart(page, "/charts/replay");

  check("the chip is on the bar", (await chip().count()) === 1, `${await chip().count()} chip(s)`);
  check("a healthy account says nothing else", (await blockText()) === "", `said "${await blockText()}"`);
  check("no band above the tape", (await page.locator(".sim-account-note").count()) === 0);

  // Which session is actually loaded — the review stub has to name it.
  const title = (await page.locator(".chart-topbar-title").first().textContent()) ?? "";
  const [symbol, date] = title.split("·").map((s) => s.trim());
  check("a session is loaded", /^[A-Z0-9]+$/.test(symbol) && /^\d{4}-\d{2}-\d{2}$/.test(date), title.trim());

  // --- the other account ----------------------------------------------------
  // The switch is the `<select>` in the ticket now, not the chip's name — the
  // chip could only ever toggle between two, and there can be any number. It is
  // only offered while this account is clean, which is why this section is here
  // rather than lower down: everything below spends the file making it anything
  // but. The dock has to be open for it to exist at all.
  const swtch = () => page.locator("select[data-account-switch]");
  const name = () => chip().locator("[data-account-name]");
  // The ticket lives in the right-hand dock, so the switch does not exist until
  // it is open. Same gesture presetcheck uses.
  const openDock = async () => {
    if (await page.locator(".sim-panel.open").count()) return;
    await page.locator("button.chart-topbar-btn", { hasText: "▤▎" }).first().click();
    await page.waitForSelector(".sim-panel.open", { timeout: 5000 });
    await page.waitForTimeout(300);
  };
  await openDock();
  check("the chip names the funded account",
    (await name().textContent())?.trim() === "LucidPro 50K",
    (await name().textContent())?.trim());
  check("the switch is offered", !(await swtch().isDisabled()));
  check("and lists both accounts",
    (await swtch().locator("option").count()) === 2,
    `${await swtch().locator("option").count()} option(s)`);

  await swtch().selectOption("paper");
  await page.waitForURL("**/charts/replay/paper");
  await page.waitForSelector(".chart-legend");
  await page.waitForTimeout(800);
  await openDock();
  check("picking paper lands on the paper page",
    new URL(page.url()).pathname === "/charts/replay/paper", page.url());
  check("the chip is the paper one",
    (await chip().getAttribute("data-account")) === "paper" &&
      (await name().textContent())?.trim() === "Paper",
    `${await chip().getAttribute("data-account")} / ${(await name().textContent())?.trim()}`);
  // The equity is the paper stub's, which is the whole point of keying the
  // query by mode: one account's number must never be drawn under the other's
  // name, not even for the frame before a refetch lands.
  const paperEq = ((await chip().locator("b").first().textContent()) ?? "").trim();
  check("showing the paper account's own equity", paperEq.includes("48940"), paperEq);
  check("and still no band above the tape",
    (await page.locator(".sim-account-note").count()) === 0);

  await swtch().selectOption("funded");
  await page.waitForURL((u) => new URL(u).pathname === "/charts/replay");
  await page.waitForSelector(".chart-legend");
  await page.waitForTimeout(800);
  const backEq = ((await chip().locator("b").first().textContent()) ?? "").trim();
  check("picking it back comes back to the funded one",
    (await chip().getAttribute("data-account")) === "funded" && backEq.includes("50000"),
    `${await chip().getAttribute("data-account")} / ${backEq}`);

  // --- the flagged sittings -------------------------------------------------
  // This section used to drive the *review gate*: an owed review refused the
  // next entry, withdrew the account switch, and put "review owed" on the chip.
  // All three went on 2026-08-25 — reviewing is a choice now. What is left to
  // check is that the reminder replacing it is only ever a reminder, which is
  // exactly the property that would rot quietly if nothing asserted it.
  account = {
    ...healthy(),
    review_flagged: {
      attempt_id: "2026-02-02_NQH5_20260202T140000Z",
      flags: [],
      count: 2,
    },
  };
  await openChart(page, "/charts/replay");
  const flagged = await blockText();
  check("the chip counts the flagged sittings", flagged === "2 flagged", `chip says "${flagged}"`);
  check("the switch to paper stays open", !(await swtch().isDisabled()));

  await spaceClick();
  const refused = await refusedText();
  check(
    "a flagged sitting refuses no entry",
    !refused.includes("has not been reviewed"),
    refused.slice(0, 80) || "(nothing refused)",
  );

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

  // --- resettable -----------------------------------------------------------
  // The state a paper death lands on within the second, and the funded one a day
  // later. Neither gate refuses it — the next sitting *is* the reset, minted by
  // the create — so the page must still offer a ticket to take it with. It did
  // not until 2026-08-19: the autopsy hid the ticket for as long as the account
  // was not `live`, which on paper is forever, and the account wedged on the card
  // saying "the next sitting opens a fresh $50,000" with no way to open one.
  paperAccount = {
    ...paperAccount,
    status: "can_reset",
    equity: 47_967,
    floor: 48_000,
    can_reset: true,
    cooldown_until: iso(Date.now() - 60_000),
    last_death: {
      at: iso(Date.now() - 60_000),
      attempt_id: "2026-02-03_NQH5_20260203T140000Z",
      equity: 47_967,
      floor: 48_000,
      epoch: 0,
      cause_of_death: null,
    },
  };
  await openChart(page, "/charts/replay/paper");
  check("a resettable account still shows the death",
    (await page.locator("[data-autopsy]").count()) === 1 &&
      (await page.locator(".sim-account-note.dead").count()) === 1);
  check(
    "…and gives the ticket back to take the reset with",
    (await page.locator(".sim-ticket").count()) === 1,
    `${await page.locator(".sim-ticket").count()} ticket(s)`,
  );
  check("no cause is asked for on paper", (await page.locator("[data-autopsy-cause]").count()) === 0);

  // --- the review, entered on purpose ---------------------------------------
  // It was "the forced review" until 2026-08-25 and the page let itself into it.
  // Now there is one way in and it is the marks — written by the history page's
  // review button and by Review now on the recap card, and seeded directly here
  // so this drives the same build both of them do.
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
  reviewJournal = [
    {
      trade_key: "k-answered", direction: "Long", entry_ts_local: `${date} 10:02:00`,
      net_pnl: 220, model_id: null, rules_met: [], reviewed: true,
      note: "", tags: ["patient entry"], setups: [], confluences: [],
      grade: "B", setup: "faded_rally", discipline: "clean",
      watched_levels: ["devVP_val"],
      levels: [{ id: "devVP_val", label: "NY VAL", family: "value_low", dist_ticks: 2, rank: 0.04 }],
      context_chips: [],
    },
    {
      trade_key: "k-owing", direction: "Short", entry_ts_local: `${date} 11:15:00`,
      net_pnl: -400, model_id: null, rules_met: [], reviewed: false,
      note: "", tags: [], setups: [], confluences: [],
      grade: null, setup: null, discipline: null, watched_levels: [],
      context_chips: [],
      // As the server sends them: distance order. The far level is the
      // *better-ranked* one, so this payload is only in the right order if the
      // client leaves it alone — which is the thing being checked.
      levels: [
        { id: "vwap", label: "NY VWAP", family: "session_mean", dist_ticks: 3, rank: 0.6 },
        { id: "gxVP_vah", label: "GX VAH", family: "value_high", dist_ticks: -40, rank: 0.01 },
      ],
    },
  ];
  account = {
    ...healthy(),
    review_flagged: { attempt_id: attemptId, flags: reviewDetail.flags, count: 1 },
  };
  attemptsList = [reviewDetail];
  // The marks the two doors into a review both write (lib/replayReview beside
  // lib/replayResume), seeded here. A flagged sitting on the account is *not*
  // one of those doors and must not act like one — the assertion below that the
  // panel is open would pass on auto-entry too, so the marks are written after
  // a load that proves the flag alone opened nothing.
  await page.evaluate(() => {
    for (const k of ["sim.review", "sim.review.replay", "sim.resume", "sim.resume.replay"])
      localStorage.removeItem(k);
  });
  await openChart(page, "/charts/replay");
  check(
    "a flagged sitting does not open itself",
    (await page.locator("[data-review-panel]").count()) === 0,
  );

  // Off the sim page first. Leaving it runs `writeResume`, which rewrites
  // `sim.resume.replay` for whatever session was on screen — marks seeded from
  // the sim page are clobbered by its own unmount on the way out.
  await page.goto(`${BASE}/`);
  await page.evaluate(
    ([id, sym, d]) => {
      localStorage.setItem("sim.resume.replay", JSON.stringify({
        symbol: sym, date: d, clockMs: 0, attemptId: id, contextTicks: 0,
      }));
      localStorage.setItem("sim.review.replay", JSON.stringify({ attemptId: id }));
    },
    [attemptId, symbol, date],
  );
  await openChart(page, "/charts/replay");

  const panel = page.locator("[data-review-panel]");
  check("the marks open the page in review mode", (await panel.count()) === 1);
  check("every trade gets a card", (await page.locator("[data-review-trade]").count()) === 2,
    `${await page.locator("[data-review-trade]").count()} card(s)`);
  check(
    "the ticket is gone",
    (await page.locator(".sim-ticket").count()) === 0,
    `${await page.locator(".sim-ticket").count()} ticket(s)`,
  );
  // What the review stopped asking for. Both were removed rather than moved:
  // the context numbers live on the trade detail, and leak/justified asks
  // whether an account rule tripped, which is not how good the trade was.
  check("no flag verdicts in the review", (await page.locator("[data-verdict]").count()) === 0);
  // Asserted on the strip's own words rather than a marker attribute: the
  // component has no test hook, and "after, best" is text only it renders.
  const panelText = await panel.innerText();
  check("no context strip in the review",
    !/after, best|bar printed/i.test(panelText), panelText.slice(0, 90));

  const file = page.locator("[data-review-file]");
  check("filing is refused while a trade is unanswered", await file.isDisabled(),
    (await page.locator("[data-review-progress]").textContent())?.trim());

  // The owing card, answered one field at a time — the gate is all three.
  const owing = page.locator('[data-review-trade="1"]');
  await owing.locator('[data-review-setup-pick="faded_rally"]').click();
  await page.waitForTimeout(150);
  check("a setup alone is not a review", await file.isDisabled(),
    (await page.locator("[data-review-progress]").textContent())?.trim());

  // The server orders candidates by distance (pytest owns that rule). What the
  // client owes is to leave the order alone: `value_high` has the tighter rank,
  // so a picker that re-sorted on its own would lead with the machine's opinion
  // instead of the chart's fact.
  const firstLevel = await owing.locator("[data-review-level-pick]").first()
    .getAttribute("data-review-level-pick");
  check("the picker keeps the server's order, and does not re-sort by rank",
    firstLevel === "vwap", firstLevel);
  await owing.locator('[data-review-level-pick="vwap"]').click();
  await page.waitForTimeout(150);
  check("a setup and a level still owe a discipline call", await file.isDisabled(),
    (await page.locator("[data-review-progress]").textContent())?.trim());

  await owing.locator('[data-review-discipline-pick="clean"]').click();
  await page.waitForTimeout(150);
  check("level + setup + discipline completes it", !(await file.isDisabled()),
    (await page.locator("[data-review-progress]").textContent())?.trim());

  // Placing must still be refused. The refusal *sentence* is not visible here
  // — the strip that would show it lives in the ticket, and review mode takes
  // the whole ticket away ("the refusal on the order paths is a backstop
  // rather than the explanation") — so what is asserted is the backstop's
  // effect: the gesture leaves nothing resting and nothing on.
  await spaceClick();
  check("an order in review mode is refused — nothing rests", (await page.evaluate(() =>
    Number(document.querySelector(".sim-rail-badge")?.textContent?.trim()) || 0)) === 0);
  check("…and nothing is on", (await page.locator(".sim-quick-pos").count()) === 0);

  // --- the floor ends the sitting, and it stays ended -----------------------
  // Last, because it is the one section that actually trades: the sitting it
  // opens leaves recorder state and a resume bookmark behind that the stubbed
  // sections above must not inherit. Equity opens already under the floor, so
  // the first booked trade is a breach whichever way the tape ticks — which is
  // the shape of the sitting that found this bug: a stop-out can realize its
  // way through the floor and be flat again before any position-gated check
  // runs. What is being checked is the whole death: the flatten, the sitting
  // settling as finished, and the *next* entry being refused off the page's
  // own memory — the stubbed account here never flips to "blown", which is
  // exactly the beat the server needs to learn of the death in production.
  reviewDetail = null;
  reviewJournal = [];
  // A full $1,000 under, not a few dollars: the first trade breaches whichever
  // way it resolves, so the section does not flake on the tape's direction —
  // the death fires the moment a position exists.
  account = { ...healthy(), equity: 47_000, floor: 48_000 };
  // Off the sim first: leaving it is what writes the resume bookmark, so the
  // removal has to happen after the review page has already unloaded.
  await page.goto(`${BASE}/`);
  await page.evaluate(() => {
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.review.replay");
    localStorage.removeItem("sim.review.drill");
    localStorage.removeItem("sim.review.paper");
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
    localStorage.removeItem("sim.resume.paper");
  });
  await openChart(page, "/charts/replay");
  await page.locator('.sim-pane[data-pane="0"]').hover();
  await page.keyboard.press("w");
  // The order fills on the next print — a paused tape has no next print.
  for (let i = 0; i < 4; i++) {
    await page.keyboard.press(".");
    await page.waitForTimeout(400);
  }
  const died = await refusedText();
  check("the floor closes the position and ends the sitting",
    died.includes("trailing floor"), died.slice(0, 90));
  check("nothing is left on", (await page.locator(".sim-quick-pos").count()) === 0,
    `${await page.locator(".sim-quick-pos").count()} position row(s)`);
  check("the death settled the sitting",
    lastPut?.status === "finished" && (lastPut?.trades?.length ?? 0) >= 1,
    `last PUT status=${lastPut?.status} trades=${lastPut?.trades?.length}`);
  await page.keyboard.press("w");
  await page.keyboard.press(".");
  await page.waitForTimeout(700);
  const back = await refusedText();
  check("trading the account back is refused", back.includes("died this sitting"),
    back.slice(0, 90));
  check("and no position came back", (await page.locator(".sim-quick-pos").count()) === 0);
  // The bookmark this sitting leaves has to be cleared from *outside* the sim,
  // because leaving the page is precisely what writes it (`writeResume` spends
  // itself on unmount) — the `finally` below runs its removals off `BASE`.
  await page.goto(`${BASE}/`);

  check("no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  // Leave no marks behind: a review marker in localStorage would make every
  // later visit to the page a review.
  await page.evaluate(() => {
    localStorage.removeItem("sim.review");
    localStorage.removeItem("sim.review.replay");
    localStorage.removeItem("sim.review.drill");
    localStorage.removeItem("sim.review.paper");
    localStorage.removeItem("sim.resume");
    localStorage.removeItem("sim.resume.replay");
    localStorage.removeItem("sim.resume.drill");
    localStorage.removeItem("sim.resume.paper");
  }).catch(() => {});
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
