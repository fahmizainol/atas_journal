// Armed levels (lib/levelArm): does a level asked to trade itself actually place
// an order, and does it place the *right* one?
//
// The geometry is pure and could be unit-tested if the frontend had a runner; it
// does not, so this is the proof. What can only be seen in a browser anyway:
// that the arm reaches the page's own `placeOrder` funnel (and so inherits its
// gates), that a `through` arm is spent by the crossing rather than left to fire
// again, and that a rewind un-places what an arm placed — which is the one
// behaviour the log's truncation gives for free and would silently lose if the
// arm ever started building orders of its own.
//
// Three more things live only here. That an `exit` arm reaches the page's
// *flatten* rather than its order funnel — the geometry can only say "close",
// and which code that word reaches is the whole risk. That the race cancels
// within a purpose and not across it. And that the rows read nearest-first,
// which is a CSS `order` written straight at the DOM by the distance painter:
// the source order React renders is deliberately different, so nothing but a
// browser can tell whether the ranking is applied at all.
//
// Run: node tools/browser/armcheck.mjs [--headed]
import { launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page } = await launch({ headed });

// Deliberately **not** stubbing `/replays/account` the way presetcheck does.
// That stub's payload (`healthyAccount()` in lib.mjs) no longer matches what
// `AccountChip` reads — it throws on `fmtRecord` and takes the whole route down
// with it — so here the real account answers and the one state it can put the
// page into that would break this check, a sitting that owes a review, is
// asserted against instead. A review refuses every order by design; failing on
// that with a sentence beats timing out on a missing panel.

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`  ${cond ? "✓" : "✗"} ${name}${detail ? `  — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** Walk the speed ladder to one end. It is [1,3,5,15,30,120,300] and pressing
 *  past either end is a no-op, so eight presses always arrives. */
async function speed(fast) {
  for (let i = 0; i < 8; i++) await page.keyboard.press(fast ? "]" : "[");
}

/** Play for a while, then pause.
 *
 *  Speed is the whole difficulty of testing this. Warming up needs 300x — the
 *  replay drops at the bell and the classification window wants closed bars —
 *  but every order check below needs **1x**, because at 300x a frame carries
 *  minutes of tape and a resting order placed a tick from the market is filled
 *  before the next assertion runs. "0 working" then means "it filled", which
 *  looks exactly like "it was never placed". */
async function runFor(ms) {
  await page.keyboard.press("k");
  await page.waitForTimeout(ms);
  await page.keyboard.press("k");
  await page.waitForTimeout(400);
}

/** Play in slices until `done()`, then stop. Polling rather than one long run
 *  because what a fired `through` arm leaves behind is a *resting* stop, and a
 *  stop four ticks from the market does not rest for long — run past it and the
 *  only honest reading is "something happened", which is not what this is
 *  checking. Returns whether it got there. */
async function playUntil(done, maxMs = 60_000, fast = 0) {
  const slice = 1_200;
  if (fast) for (let i = 0; i < fast; i++) await page.keyboard.press("]");
  // The tape can simply run out: the warm-up spends hours of session at 300x,
  // and `play()` refuses once the clock is at the end. A dead tape makes every
  // wait below time out, which reads as "the arm never fired" — a failure of the
  // feature — when it is a failure to have any market left. The distances repaint
  // with every print, so a frozen set of them is the signal.
  let stalled = 0;
  const dists = () => page.$$eval(".chart-levels-dist", (e) => e.map((x) => x.textContent).join());
  let last = await dists();
  try {
    for (let spent = 0; spent < maxMs; spent += slice) {
      await runFor(slice);
      if (await done()) return true;
      const now = await dists();
      stalled = now === last ? stalled + 1 : 0;
      last = now;
      if (stalled >= 3) return "stalled";
    }
    return false;
  } finally {
    await speed(false);
  }
}

/** The working-orders card, as text. Nothing in it carries a class of its own,
 *  so it is found by its section title. */
const workingRows = () =>
  page.$$eval(".sim-card", (cards) => {
    const c = cards.find((x) =>
      (x.querySelector(".sim-sec-t")?.textContent ?? "").trim().startsWith("Working"),
    );
    if (!c) return [];
    return [...c.children]
      .slice(1)
      .map((d) => d.textContent.replace(/\s+/g, " ").trim())
      .filter(Boolean);
  });

/** How many orders are resting, off the rail badge — which is there whether or
 *  not the panel is open. */
const workingCount = async () => {
  const b = page.locator(".sim-rail-badge");
  return (await b.count()) ? Number((await b.first().innerText()).trim()) || 0 : 0;
};

/** Trades booked so far. The empty state is one div saying so, which is why the
 *  text is checked rather than the child count. */
const blotterCount = () =>
  page.$$eval(".sim-blotter-list", (els) =>
    !els.length || /No trades yet/.test(els[0].textContent ?? "")
      ? 0
      : [...els[0].children].length,
  );

const rowInfo = () =>
  page.$$eval(".chart-levels-row", (els) =>
    els.map((e) => ({
      price: Number(e.querySelector(".chart-levels-dist")?.getAttribute("data-price")),
      dist: e.querySelector(".chart-levels-dist")?.textContent ?? "",
      name: e.querySelector(".chart-levels-name")?.textContent ?? "",
      arms: [...e.querySelectorAll(".chart-levels-armbtn")].map((b) => b.textContent.trim()),
      // Where the row actually *sits*, not where React put it. The rows are
      // ranked nearest-first by flex `order`, written straight at the DOM by the
      // chart's own distance painter, and source order stays price-descending —
      // so reading `els` in order would read the fallback and never see the
      // ranking at all.
      top: e.getBoundingClientRect().top,
    })),
  );

/** Ticks off the mark, unsigned, as the row reports it. */
const away = (r) => Math.abs(Number(/-?\d+/.exec(r.dist)?.[0] ?? NaN));

/** Is there a position on? The quick strip only renders this when there is. */
const inPosition = async () => (await page.locator(".sim-quick-pos").count()) > 0;

/** How many arms are standing. */
const standingCount = () => page.locator(".chart-levels-armedrow").count();

/** Put ⤢ into a known state. Named rather than clicked, because the sections
 *  below want it in opposite states and a sequence of toggles only lands where
 *  it is meant to if every branch above it took the path that was expected —
 *  which is exactly what a guard that only fires on a thin session breaks. */
async function setReach(on) {
  const lit = (await page.locator(".chart-levels-reach.on").count()) > 0;
  if (lit === on) return;
  await page.locator(".chart-levels-reach").click();
  await page.waitForTimeout(600);
}

/** Disarm everything still standing, so the next section starts from nothing. */
async function clearArms() {
  for (const b of await page.locator(".chart-levels-armedrow button").all()) {
    await b.click().catch(() => {});
  }
  await page.waitForTimeout(200);
}

/** Index of the row nearest the last price — the one a crossing will reach
 *  soonest, which is what makes the firing check finish in bounded time. */
const nearestRow = (rows) => {
  let best = -1;
  let d = Infinity;
  rows.forEach((r, i) => {
    const t = Math.abs(Number(/-?\d+/.exec(r.dist)?.[0] ?? NaN));
    if (Number.isFinite(t) && t < d) {
      d = t;
      best = i;
    }
  });
  return { i: best, ticks: d };
};

const armButton = (i, text) =>
  page.locator(".chart-levels-row").nth(i).locator(".chart-levels-armbtn", { hasText: text });

try {
  // Load once before touching storage — there is no origin to write to until a
  // page from it has been served.
  await openChart(page, "/charts/replay");
  await page.evaluate(() => localStorage.setItem("chart.levelPanel", "1"));
  await openChart(page, "/charts/replay");
  if (await page.locator(".sim-panel.reviewing").count()) {
    throw new Error("this sitting owes a review — nothing can be placed; file it and re-run");
  }
  await speed(true);
  await runFor(25_000);
  // Everything from here is an order check, so drop to 1x and stay there.
  await speed(false);

  // A random session can genuinely have nothing within the near window at the
  // moment the warm-up ends. Run on rather than fail: "no rows" is a fact about
  // the day drawn, not about the code under test.
  let rows = await rowInfo();
  for (let i = 0; i < 5 && !rows.length; i++) {
    await speed(true);
    await runFor(10_000);
    await speed(false);
    rows = await rowInfo();
  }
  ok("the panel has rows to arm", rows.length > 0, `${rows.length} rows`);
  if (!rows.length) throw new Error("no level rows on this session — re-run");
  ok(
    "every row offers all three shapes",
    rows.every(
      (r) =>
        r.arms.length === 3 &&
        r.arms.includes("bid") &&
        r.arms.includes("thru") &&
        r.arms.includes("exit"),
    ),
    JSON.stringify(rows[0].arms),
  );

  // --- nearest first --------------------------------------------------------
  // The ranking is CSS `order` written by the distance painter, so this reads
  // where the rows landed on screen rather than the order React rendered them
  // in — the two disagree by design, and asserting the DOM order would pass
  // against a panel that ranks nothing.
  const seen = [...(await rowInfo())].sort((a, b) => a.top - b.top).map(away);
  ok(
    "the rows read nearest first",
    seen.every((t, i) => i === 0 || !(t < seen[i - 1])),
    seen.join(" ≤ "),
  );

  // Reach on for the rest of the run. The two shapes want opposite levels to be
  // checkable at all, and only the wide list has both: a **far** level for the
  // bid, because a bid two ticks from the market is filled before the next
  // assertion and "0 working" then means "it filled" rather than "it never
  // placed"; and the **nearest** for the crossing, because a level tens of ticks
  // away is minutes of tape from being reached.
  await setReach(true);
  rows = await rowInfo();
  ok(
    "⤢ reaches past the near window",
    rows.some((r) => Math.abs(Number(/-?\d+/.exec(r.dist)?.[0] ?? 0)) > 40),
    `${rows.length} rows, furthest ${Math.max(
      ...rows.map((r) => Math.abs(Number(/-?\d+/.exec(r.dist)?.[0] ?? 0))),
    )}t`,
  );

  // --- bid: places now, stands nothing --------------------------------------
  const before = await workingCount();
  const far = rows
    .map((r, i) => ({ i, t: Math.abs(Number(/-?\d+/.exec(r.dist)?.[0] ?? 0)) }))
    .sort((a, b) => b.t - a.t)[0];
  await armButton(far.i, "bid").click();
  // The order is stamped with the clock and arrives `latencyMs` later, so a
  // paused tape never shows it. Run a moment for it to land.
  await runFor(2_500);
  const afterBid = await workingCount();
  ok("a bid places a resting order", afterBid > before, `${before} → ${afterBid} working (${far.t}t out)`);
  ok(
    "it rests as a limit",
    (await workingRows()).some((t) => t.includes("LMT")),
    (await workingRows()).join(" | ") || "(panel shut)",
  );
  ok(
    "a bid leaves nothing standing",
    (await page.locator(".chart-levels-armedrow").count()) === 0,
    "a limit has nothing left to wait for",
  );

  // Clear the decks: flatten and cancel everything working.
  await page.keyboard.press("q");
  await runFor(2_000);

  // --- through: stands, then fires on the crossing --------------------------
  rows = await rowInfo();
  const target = nearestRow(rows);
  const armedPrice = rows[target.i]?.price;
  await armButton(target.i, "thru").click();
  await page.waitForTimeout(300);
  ok(
    "a through arm lights its row",
    (await page.locator(".chart-levels-armbtn.on").count()) === 1,
    `${await page.locator(".chart-levels-armbtn.on").count()} lit`,
  );
  const standing = await page.$$eval(".chart-levels-armedrow", (els) =>
    els.map((e) => ({
      text: e.textContent.replace(/\s+/g, " ").trim(),
      px: Number(e.querySelector(".chart-levels-armedpx")?.getAttribute("data-price")),
    })),
  );
  ok("it appears as a standing arm", standing.length === 1, standing.map((x) => x.text).join(" | "));
  // The arm snaps its level to the tick grid on purpose: a VWAP does not sit on
  // the grid, and the price shown standing has to be the price the order will
  // actually rest at. So the claim is "within a tick of the level", not "equal".
  const shownPx = standing[0]?.px;
  ok(
    "the standing arm quotes the grid price it froze",
    Math.abs(shownPx - armedPrice) <= 0.25,
    `level ${armedPrice} → armed ${shownPx}`,
  );

  // Stop the moment it fires, so a stop that is still resting is caught resting.
  // 15x for the wait: a level tens of ticks away is minutes of tape from being
  // crossed, and at 1x that is a check whose result is decided by which random
  // session got drawn.
  const tradesBefore = await blotterCount();
  const spent = await playUntil(
    async () => (await page.locator(".chart-levels-armedrow").count()) === 0,
    90_000,
    4,
  );
  if (spent === "stalled") {
    throw new Error("the session ran out of tape before the arm was reached — re-run");
  }
  ok("the crossing spends the arm", spent, `armed ${target.ticks}t away`);

  // That an order exists, not what shape it is. The shape is exact and belongs
  // to tests/test_level_arm.py, which runs the geometry under node; here the
  // tape is a random session and the stop it rests sits four ticks from the
  // market, so by the next poll it may be resting, filled, or filled and closed.
  // Flattening collapses all three into one observable — a booked trade — and
  // the failure this catches is the arm being spent with nothing placed at all.
  const fired = await workingRows();
  await page.keyboard.press("q");
  await runFor(2_000);
  const tradesAfter = await blotterCount();
  ok(
    "firing leaves an order behind",
    fired.some((t) => t.includes("STP")) || tradesAfter > tradesBefore,
    fired.length
      ? fired.join(" | ")
      : `nothing resting, trades ${tradesBefore} → ${tradesAfter}`,
  );

  await shot(page, "armcheck");

  // --- a rewind un-places what an arm placed --------------------------------
  // On a far level again, for the same reason as the bid above and one more: an
  // order that has not filled leaves no position, and a rewind through a fill is
  // refused outright (it would un-happen the trade you are in).
  await page.keyboard.press("q");
  await runFor(2_000);
  // Any arm still standing would keep the last check honest by accident.
  for (const b of await page.locator(".chart-levels-armedrow button").all()) {
    await b.click().catch(() => {});
  }
  await page.waitForTimeout(200);
  rows = await rowInfo();
  const far2 = rows
    .map((r, i) => ({ i, t: Math.abs(Number(/-?\d+/.exec(r.dist)?.[0] ?? 0)) }))
    .sort((a, b) => b.t - a.t)[0];
  await armButton(far2.i, "bid").click();
  await runFor(2_500);
  const restingFar = await workingCount();
  ok("the far bid rests", restingFar > 0, `${restingFar} working (${far2.t}t out)`);
  for (let i = 0; i < 12; i++) await page.keyboard.press(",");
  await page.waitForTimeout(1200);
  ok(
    "a rewind un-places it",
    (await workingCount()) < restingFar,
    `${restingFar} → ${await workingCount()} after 12 bars back`,
  );

  // --- disarming places nothing --------------------------------------------
  rows = await rowInfo();
  const d = nearestRow(rows);
  const w = await workingCount();
  await armButton(d.i, "thru").click();
  await page.waitForTimeout(250);
  await armButton(d.i, "thru").click();
  await page.waitForTimeout(250);
  ok(
    "arming and disarming places nothing",
    (await page.locator(".chart-levels-armedrow").count()) === 0 &&
      (await workingCount()) === w,
    `${w} → ${await workingCount()} working`,
  );

  // --- exit: takes a position off rather than putting an order on -----------
  await page.keyboard.press("q");
  await runFor(2_000);
  await clearArms();

  // Reach back **off** for this section, and it is not cosmetic. Both exit
  // checks wait for price to actually reach the armed level, so the level has to
  // be one a bounded amount of tape can get to — and with ⤢ on, "nearest" is
  // nearest *of the whole session's range*: it came out at 55 ticks on the third
  // random session tried and 90 seconds of tape at 15x never got there, which
  // this file then reported as the arm failing to fire. Off, `NEAR_TICKS` caps
  // the list at 40 and the nearest row is usually single digits.
  await setReach(false);
  // ...unless the near window is empty, which a random session is entitled to
  // be. Reach back on rather than arm nothing: `nearestRow` answers -1 on an
  // empty list and `armButton(-1)` is Playwright's *last* row, so the failure
  // this guard prevents is a thirty-second timeout blamed on the wrong thing.
  if (!(await rowInfo()).length) {
    await setReach(true);
    console.log("  – nothing inside the near window; the exit checks run on the wide list");
  }

  // Armed while flat first, because that is the half with nothing to race it: a
  // bracket resting on an open position can reach its stop or target before
  // price reaches the level, and then the crossing's effect is unobservable. Flat,
  // the whole effect is observable and it is *nothing* — the arm is still spent,
  // the same rule every other shape follows. An exit that stayed standing waiting
  // for a position to protect would be an instruction the panel had stopped
  // showing you the price of.
  rows = await rowInfo();
  const flatTarget = nearestRow(rows);
  if (flatTarget.i < 0) throw new Error("no level rows to arm an exit on — re-run");
  const restBefore = await workingCount();
  const bookedBefore = await blotterCount();
  await armButton(flatTarget.i, "exit").click();
  await page.waitForTimeout(300);
  ok("an exit arm stands", (await standingCount()) === 1, `${await standingCount()} standing`);
  const flatSpent = await playUntil(async () => (await standingCount()) === 0, 90_000, 4);
  if (flatSpent === "stalled") {
    throw new Error("the session ran out of tape before the exit was reached — re-run");
  }
  ok("the crossing spends an exit arm", flatSpent, `armed ${flatTarget.ticks}t away`);
  ok(
    "an exit fired while flat places nothing",
    (await workingCount()) === restBefore && (await blotterCount()) === bookedBefore,
    `working ${restBefore} → ${await workingCount()}, trades ${bookedBefore} → ${await blotterCount()}`,
  );

  // ...and now with something to take off.
  //
  // Retried, because this one has a genuine competitor: the entry rests a stop
  // and a target, and either can be reached before price gets back to the level.
  // That is the sim working correctly and it leaves the arm untested, so the
  // answer is another position rather than a red line — but only twice, or a
  // feature that never closes anything would retry its way to a clean run.
  let closedByArm = null;
  let booked2 = 0;
  for (let attempt = 0; attempt < 3 && closedByArm === null; attempt++) {
    await page.keyboard.press("q");
    await runFor(1_500);
    await clearArms();
    booked2 = await blotterCount();
    await page.keyboard.press("w");
    await runFor(2_500);
    if (!(await inPosition())) continue;
    rows = await rowInfo();
    const exitAt = nearestRow(rows);
    if (exitAt.i < 0) continue;
    await armButton(exitAt.i, "exit").click();
    await page.waitForTimeout(300);
    const exitSpent = await playUntil(
      async () => (await standingCount()) === 0 || !(await inPosition()),
      60_000,
      4,
    );
    if (exitSpent === "stalled") {
      throw new Error("the session ran out of tape before the exit fired — re-run");
    }
    // Still standing means something else took the position off first.
    if ((await standingCount()) === 0) closedByArm = exitSpent;
  }
  if (closedByArm === null) {
    // Not `ok(..., true)`: this check did not run, and a green line for a check
    // that did not run is the one thing this file is not allowed to print.
    console.log("  – an exit arm closes the position  — the bracket won all three tries; untested this run");
    await clearArms();
  } else {
    ok("an exit arm closes the position", !(await inPosition()), "flat");
    ok(
      "and the trade is booked",
      (await blotterCount()) > booked2,
      `${booked2} → ${await blotterCount()}`,
    );
  }

  // --- the race: first touched wins, within a purpose -----------------------
  await page.keyboard.press("q");
  await runFor(2_000);
  await clearArms();
  await page.locator(".chart-levels-race").click();
  await page.waitForTimeout(200);
  ok("the race lights", (await page.locator(".chart-levels-race.on").count()) === 1);

  // Reach back on: the race wants three levels to arm and the near window does
  // not reliably hold that many. Only the *nearest* has to be reached here —
  // whichever arm fires first is the one under test — so the far two being far
  // is free, and is in fact the point.
  await setReach(true);

  rows = await rowInfo();
  const ranked = rows.map((r, i) => ({ i, t: away(r) })).sort((a, b) => a.t - b.t);
  if (ranked.length < 3) {
    console.log("  – the race needs three rows to be worth checking; this session has fewer");
  } else {
    // Two exits and one entry. Whichever fires first, the survivors must be the
    // ones of the *other* purpose and all of them — which is one assertion that
    // holds either way round, and so does not depend on which level a random
    // session reaches first.
    await armButton(ranked[0].i, "exit").click();
    await armButton(ranked[ranked.length - 1].i, "exit").click();
    await armButton(ranked[ranked.length - 2].i, "thru").click();
    await page.waitForTimeout(300);
    ok("three arms stand", (await standingCount()) === 3, `${await standingCount()} standing`);
    const raced = await playUntil(async () => (await standingCount()) < 3, 90_000, 4);
    if (raced === "stalled") {
      throw new Error("the session ran out of tape before any arm fired — re-run");
    }
    ok("one of them fires", raced, `${await standingCount()} left`);
    const left = await page.$$eval(".chart-levels-armedrow .chart-levels-cls", (els) =>
      els.map((e) => e.textContent.trim()),
    );
    ok(
      "the survivors are all of the other kind",
      left.length > 0 &&
        left.every((s) => s === left[0]) &&
        left.length === (left[0] === "thru" ? 1 : 2),
      left.join(",") || "(nothing left standing)",
    );
  }
} catch (e) {
  // Playwright puts the message on line 1 and the *selector* on the lines after
  // it. Line 1 alone says "locator.click: Timeout 30000ms exceeded" and leaves
  // you guessing which of a dozen clicks it was — which cost a debugging round
  // once already. Four lines is the call log's first entry, which names it.
  ok("ran to the end", false, String(e).split("\n").slice(0, 4).join(" · "));
} finally {
  console.log(fails.length ? `\n${fails.length} failed: ${fails.join(", ")}` : "\nall ok");
  await browser.close();
  process.exit(fails.length ? 1 : 0);
}
