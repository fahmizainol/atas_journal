// Lab -> Recall: spaced repetition over your own reviewed trades.
//
// What this asks:
//
//   1. the deck loads, the stats row is present, and a card draws — a *sized*
//      canvas with ink on it, scoped to `[data-recall-chart]` (the page can end
//      up with more than one canvas; the document-wide helpers would grab
//      whichever is biggest);
//   2. **the front is blind, in the DOM and in the payload.** No contract month
//      code and no ISO date in the card's text — the root ("NQ") is the only
//      symbol the front may show, and a visible date is a lookup key into the
//      trader's own memory. And the deck *response* carries no direction, no
//      price and no PnL: masking a field that arrived anyway is theatre, so the
//      network is checked, not just the screen. The entry stamp is the one thing
//      the front may carry, because the window is pinned to it — and it is checked
//      against the back's own copy: a front and a back built in different clocks
//      each draw perfectly well on their own, and the card is still asking about
//      one hour and answering about another;
//   3. **the front's window is the fill ±30s, plays itself, and cannot be driven
//      past it.** The card starts running at the window's near side with nothing
//      clicked; ⟲ puts it back there mid-play; the ceiling — driven to by hand,
//      since the scrubber is relative and the clock is the only absolute stamp on
//      the front — reads exactly half a minute past the entry, which is the one
//      assertion that catches a window that slid or collapsed back onto the fill;
//      and ▶ *there* rewinds the whole minute rather than doing nothing;
//   4. flipping fetches the answer, draws the trade, plays the reveal at the
//      speed you were already watching at, and unmasks the date and the full
//      contract — and says how the position was opened and what it was
//      bracketed with, checked against the payload's own numbers;
//   5. rating advances the deck;
//   6. **undo brings the rating back, and the read that went with it.** The
//      button appears only once the deck says there is something to take back,
//      it posts, and the guess typed before the misclick is put back in the box
//      — losing it is the thing that makes an undo not worth clicking. Then the
//      button goes away again: only the last rating is revertible.
//
// The rating and undo POSTs are stubbed (method checked before path — a route
// handler that matches on path alone swallows the deck GET too), and the deck's
// `undo` slot is injected into the real response the way the server would have.
// The real write path is pytest's (tests/test_recall.py); a smoke that wrote
// would push a real card out to tomorrow and quietly shrink the deck.
//
// Run: node tools/browser/recallcheck.mjs [--headed]
import { launch, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** The card's own canvas, read back — size, ink count, coarse fingerprint. */
async function probeCard(page) {
  return page.evaluate(() => {
    const root = document.querySelector("[data-recall-chart]");
    if (!root) return { error: "no card chart" };
    const c = [...root.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return { error: "no canvas" };
    if (!c.width || !c.height) return { error: "zero-sized canvas" };
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const tally = new Map();
    for (let i = 0; i < d.length; i += 16 * 4) {
      const k = `${d[i]},${d[i + 1]},${d[i + 2]}`;
      tally.set(k, (tally.get(k) ?? 0) + 1);
    }
    const bg = [...tally.entries()].sort((a, b) => b[1] - a[1])[0][0].split(",").map(Number);
    let ink = 0;
    let sig = 0;
    for (let i = 0; i < d.length; i += 16 * 4) {
      const off =
        Math.abs(d[i] - bg[0]) >= 12 ||
        Math.abs(d[i + 1] - bg[1]) >= 12 ||
        Math.abs(d[i + 2] - bg[2]) >= 12;
      if (off) {
        ink++;
        sig = (sig * 31 + i + d[i] + d[i + 1] * 3) >>> 0;
      }
    }
    return { w: c.width, h: c.height, ink, sig };
  });
}

async function waitForInk(page, timeout = 60000) {
  const t0 = Date.now();
  for (;;) {
    const p = await probeCard(page);
    if (!p.error && p.ink > 200) return p;
    if (Date.now() - t0 > timeout) return p;
    await page.waitForTimeout(500);
  }
}

const deck = await (await fetch("http://localhost:8000/api/recall/deck")).json();
if (!deck.cards?.length) {
  console.log("SKIP — nothing due on this machine; nothing to drive.");
  process.exit(0);
}

// 2b — the payload half of the blindness, checked before a browser is involved.
// `entry_ts_local` is deliberately absent from this list: the window is centred
// on the fill, so that instant is a fixed offset from the right edge and `cut_ms`
// is it. Asserting the name is missing while the value is the card's whole
// geometry would be theatre.
const ANSWER_FIELDS = [
  "direction", "avg_entry", "avg_exit", "net_pnl", "max_contracts",
  "exit_ts_local", "grade", "tags", "note", "watched_levels",
  // The order behind the fill: an order type says whether the entry was waited
  // for and a stop says how much room the trade was given.
  "order",
];
const leaked = ANSWER_FIELDS.filter((f) => f in deck.cards[0]);
ok("the deck payload carries no answer", leaked.length === 0, leaked.join(", "));

// 2c — the front's window is centred on ITS OWN fill, in the tape's own clock.
// The back's stamps are read by the browser as wall-time-as-UTC; two ends built
// in different zones still each look sane on their own — the front draws, the
// back draws — and only this comparison shows the card asking about one hour and
// answering about another. Millisecond equality, because `cut_ms` *is* the entry
// stamp; the ±30s is the client's, and section 3 checks that end of it.
const wallMs = (s) => Date.parse(String(s).slice(0, 19) + "Z");
const backs = new Map();
for (const c of deck.cards) {
  const b = await (await fetch(`http://localhost:8000/api/recall/back/${c.trade_key}`)).json();
  backs.set(c.trade_key, b);
  const d = c.cut_ms - wallMs(b.entry_ts_local);
  ok(
    `the front is centred on its own fill — ${c.trade_key.slice(0, 8)}`,
    Math.abs(d) < 1000,
    `${d} ms from the entry`,
  );
}

const { browser, page, errors } = await launch({ headed });

// The stub: rating, canned. Method first — path alone would swallow the deck GET.
let rated = null;
//: What the server's `recall_undo` slot would hold after that rating. Set by the
//  rating stub and cleared by the undo stub, so the deck below reports what a
//  real one would at each point: nothing, then the rating, then nothing again.
let staged = null;
await page.route("**/api/recall/rate", async (route) => {
  if (route.request().method() !== "POST") return route.fallback();
  rated = route.request().postDataJSON();
  staged = { trade_key: rated.trade_key, rating: rated.rating, guess: rated.guess };
  await route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      ok: true,
      card: { due: "2099-01-01", interval_d: 1, reps: 1 },
    }),
  });
});

let undone = 0;
await page.route("**/api/recall/undo", async (route) => {
  if (route.request().method() !== "POST") return route.fallback();
  undone += 1;
  const body = { ok: true, ...staged };
  staged = null;   // only the last rating is revertible
  await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
});

// The deck stays real — the cards, the stats and the blindness assertions above
// all read it — with only the slot the stubbed rating would have created folded
// in. The `guess` is deliberately not included: the server keeps it off this
// payload, and a smoke that shipped it would pass against a page that leaked it.
await page.route("**/api/recall/deck", async (route) => {
  // The page refetches the deck on every rating and on teardown, so this can be
  // holding a request that the browser abandons underneath it. A throw inside a
  // route handler is an unhandled rejection, which kills the run with a wall of
  // request headers and no failing assertion — so losing the race has to be
  // survivable rather than fatal.
  try {
    const res = await route.fetch();
    const json = await res.json();
    await route.fulfill({
      response: res,
      contentType: "application/json",
      body: JSON.stringify({
        ...json,
        undo: staged ? { trade_key: staged.trade_key, rating: staged.rating,
                         staged_at: "2026-08-31 12:00:00" } : null,
      }),
    });
  } catch {
    await route.abort().catch(() => {});
  }
});

await page.goto(`${BASE}/recall`, { waitUntil: "networkidle", timeout: 60000 });

// 1 — deck + a drawn card.
await page.waitForSelector("[data-recall-card]", { timeout: 30000 });
ok("stats row", (await page.locator("[data-recall-stat=due]").innerText()).includes("due"));
const key = await page
  .locator("[data-recall-card]")
  .first()
  .getAttribute("data-recall-card");
const card = deck.cards.find((c) => c.trade_key === key);
ok("shown card is in the deck", !!card, key);
const front = await waitForInk(page);
ok("front canvas has ink", !front.error && front.ink > 200, JSON.stringify(front));
await shot(page, "recall-front");

// 2 — blind on screen. Everything shown about the card: no month-coded
// contract, no date.
//
// Two selectors rather than one because Recall became its own chrome-less
// workspace and the card's identity moved up into the page's 36px bar. The
// deck's own line is deliberately NOT included — "next due 2026-08-29" is a
// schedule date, not this card's, and it would trip the date rule below while
// telling you nothing about the trade.
const panelSel = ".recall-ident, .recall-body";
const cardText = async () =>
  (await page.locator(panelSel).allInnerTexts()).join("\n");
const panelText = await cardText();
ok(
  "front shows no contract month code",
  !/\b[A-Z]{1,3}[FGHJKMNQUVXZ]\d{1,2}\b/.test(panelText),
  panelText.slice(0, 120),
);
ok("front shows no date", !/\d{4}-\d{2}-\d{2}/.test(panelText) && !panelText.includes(card.date));
ok(
  "front marks no direction",
  !/\b(Long|Short)\b/.test(panelText),
  panelText.slice(0, 120),
);

const scrub = page.locator("[data-recall-transport] input[type=range]");
const maxAttr = Number(await scrub.getAttribute("max"));
ok("the scrubber is relative, so no epoch reaches the DOM", maxAttr < 86_400_000, `max=${maxAttr}`);

// 3 — the window: played without being asked, restartable, and walled.
const WINDOW_PRE_MS = 30_000; // RecallCard.WINDOW_PRE_MS
const WINDOW_POST_MS = 30_000; // RecallCard.WINDOW_POST_MS
const WINDOW_MS = WINDOW_PRE_MS + WINDOW_POST_MS;
const playBtn = page.locator("[data-recall-play]");
const restartBtn = page.locator("[data-recall-restart]");
const clockText = () => page.locator("[data-recall-transport] .sim-clock").innerText();
const hhmmss = (ms) => new Date(ms).toISOString().slice(11, 19);
/** The transport clock as a within-day ms, for comparing against a tape stamp
 *  without caring which day either of them thinks it is. */
const clockMsOf = (txt) => {
  const [h, m, s] = txt.trim().split(":").map(Number);
  return ((h * 60 + m) * 60 + s) * 1000;
};
const entryMs = wallMs(backs.get(key).entry_ts_local);
const step = Number(await scrub.getAttribute("step")) || 1;

// 3a — the card plays itself. Nothing has been clicked: the rep is the minute,
// not the button that starts it. The clock has to be *inside* the window, which
// is what catches an autoplay that fired at the ceiling and stopped dead.
ok("the card plays itself", (await playBtn.innerText()).includes("Pause"));
const opened = Number(await scrub.inputValue());
ok(
  "autoplay starts at the window's near side",
  maxAttr - opened >= WINDOW_MS - 5000 && maxAttr - opened <= WINDOW_MS + 2000,
  `${opened} of ${maxAttr} (${maxAttr - opened} ms back)`,
);
await page.waitForTimeout(1500);
const running = Number(await scrub.inputValue());
ok("the replay advances the clock", running > opened, `${opened} -> ${running}`);
ok("the replay stays inside the window", running <= maxAttr, `${running} vs ${maxAttr}`);

// 3b — ⟲ mid-play: back to the same near side, still running. The gesture is
// "this window again", not "wherever a rewind lands", so it is checked against
// the position autoplay opened on rather than merely against "earlier".
await restartBtn.click();
await page.waitForTimeout(400);
const restarted = Number(await scrub.inputValue());
ok(
  "⟲ restarts the window",
  Math.abs(restarted - opened) <= 1500 + step,
  `${running} -> ${restarted} (opened at ${opened})`,
);
ok("⟲ leaves the card playing", (await playBtn.innerText()).includes("Pause"));
await playBtn.click();
ok("▶ pauses", !(await playBtn.innerText()).includes("Pause"));

// 3c — the window's far side is a wall, and it is half a minute past the fill.
// The clock is the only absolute stamp on the front (the scrubber is relative by
// design and cannot say), so the ceiling is read there: a window that slid, that
// was built in the wrong clock, or that quietly collapsed back onto the fill all
// show up here and nowhere else. A fill inside the last half-minute of a session
// clamps to the session end and would read short; nothing in the deck is that late.
//
// Aligned down to the step, because `max` is a tape length and needn't be a
// round number of seconds — and `fill` refuses an off-step value. This is also
// what dragging to the far right actually gives you: the rightmost position of
// a range input is the largest step multiple inside the range, not `max`. That
// alignment is the whole tolerance below.
await scrub.fill(String(Math.floor(maxAttr / step) * step));
await page.waitForTimeout(1200);
const ceilOff = clockMsOf(hhmmss(entryMs + WINDOW_POST_MS)) - clockMsOf(await clockText());
ok(
  "the window ends half a minute past the fill",
  ceilOff >= 0 && ceilOff <= step,
  `${(await clockText()).trim()} vs ${hhmmss(entryMs + WINDOW_POST_MS)} (entry ${hhmmss(entryMs)})`,
);
const atCut = await probeCard(page);
ok("the front draws at the window's far side", !atCut.error && atCut.ink > 200, JSON.stringify(atCut));

// 3d — ▶ at the ceiling still means "replay the minute": there is no tape ahead
// of the clock there, so a button that merely resumed would be dead.
await playBtn.click();
await page.waitForTimeout(400);
const rewound = Number(await scrub.inputValue());
const jump = Math.floor(maxAttr / step) * step - rewound;
ok(
  "▶ at the ceiling rewinds the whole window",
  jump >= WINDOW_MS - 5000 && jump <= WINDOW_MS + 1000,
  `${maxAttr} -> ${rewound} (${jump} ms)`,
);
await playBtn.click();

// 3e — the speed is the trader's, and it is kept. Set here, checked again after
// the flip: the reveal used to force 1× back on, which on a deck is a setting
// re-picked forever.
await page.locator("[data-recall-transport] select").selectOption("3");
ok("the speed picker takes 3×", (await page.locator("[data-recall-transport] select").inputValue()) === "3");

// 4 — flip.
await page.locator("[data-recall-guess]").fill("fades back into value");
await page.locator("[data-recall-flip]").click();
await page.waitForSelector("[data-recall-back]", { timeout: 20000 });
await page.waitForSelector("[data-recall-direction]", { timeout: 20000 });
ok("the back names the direction", (await page.locator("[data-recall-direction]").count()) === 1);
ok("the back keeps the guess", (await cardText()).includes("fades back into value"));

// The reveal runs the tape from before the entry, so the canvas moves.
await page.waitForTimeout(2500);
const back = await probeCard(page);
ok("the reveal redraws the canvas", !back.error && back.sig !== atCut.sig, `${atCut.sig} -> ${back.sig}`);
await shot(page, "recall-back");

// The order behind the fill, on the panel. Checked against the payload's own
// numbers rather than against a shape, because the line is where a reconstructed
// bracket would be wrong quietly: the levels are struck at the fill, and the
// panel is the only place they are legible enough to disagree with.
const ordered = backs.get(key)?.order ?? null;
if (ordered) {
  const line = (await page.locator("[data-recall-order]").innerText()).trim();
  ok("the back names how the entry was placed",
     line.startsWith(ordered.open_type === "market" ? "market in" : ordered.open_type),
     line);
  ok("the back names the bracket the position opened with",
     ordered.stop == null
       ? line.includes("no stop")
       : line.includes(ordered.stop.toFixed(2)),
     `${ordered.stop} in "${line}"`);
  ok("the back says what took the trade out",
     /out on the (stop|target|trail)|closed by hand|scaled out/.test(line), line);
} else {
  ok("no order line for a trade that was never a sitting",
     (await page.locator("[data-recall-order]").count()) === 0);
}

ok(
  "the reveal keeps the speed you were watching at",
  (await page.locator("[data-recall-transport] select").inputValue()) === "3",
);
ok("the reveal plays itself", (await playBtn.innerText()).includes("Pause"));

const backText = await cardText();
ok("the back shows the date", backText.includes(card.date));
ok("the back shows the contract", backText.includes(card.symbol));
ok("the back reaches past the window", Number(await scrub.getAttribute("max")) >= maxAttr,
   `${maxAttr} -> ${await scrub.getAttribute("max")}`);

// 5 — rate it.
await page.locator("[data-recall-rate='3']").click();
await page.waitForTimeout(800);
ok("rating posts the card, the rating and the guess",
   rated?.trade_key === key && rated?.rating === 3 && rated?.guess === "fades back into value",
   JSON.stringify(rated));

// 6 — take it back.
const undoBtn = page.locator("[data-recall-undo]");
await undoBtn.waitFor({ timeout: 10000 });
ok("undo names the rating it would revert",
   (await undoBtn.innerText()).toLowerCase().includes("good"),
   await undoBtn.innerText());

await undoBtn.click();
await page.waitForTimeout(800);
ok("undo posts", undone === 1, `${undone} posts`);
ok("undo hands the guess back",
   (await page.locator("[data-recall-guess]").inputValue()) === "fades back into value",
   await page.locator("[data-recall-guess]").inputValue());
ok("undo lands back on the card it restored",
   (await page.locator("[data-recall-card]").getAttribute("data-recall-card")) === key);
ok("only the last rating is revertible",
   (await page.locator("[data-recall-undo]").count()) === 0);

ok("no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));

await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} FAILED: ${fails.join(", ")}`);
  process.exit(1);
}
console.log("\nall good");
