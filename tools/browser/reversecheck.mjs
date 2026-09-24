// The reverse-entry knob, in a real browser.
//
// The rule is one line in `placeMarket` — flip the side unless there is already
// a position — and it is exactly the kind of line no pytest can reach: it lives
// inside a five-thousand-line component, between a ref read and a callback, and
// the thing worth knowing about it is not arithmetic but *agreement*. Two
// separate expressions decide what happens when you press a market button: the
// one in `placeMarket` that picks the side going out, and the one in the render
// that picks the word on the button. A check that only read the labels would
// pass on a ticket whose green BUY sends shorts.
//
// So this presses the button and reads the position back. What it asks:
//
//   1. off, the dock is the dock — SELL on the left, BUY on the right;
//   2. on and flat, the pair swaps, so the label is never a claim the order
//      contradicts;
//   3. pressing the *left* button — the one that has always meant sell, and now
//      reads BUY — opens a LONG. This is the whole feature;
//   4. with size on, the knob stands down: the chip strikes through and the
//      buttons go back to meaning what they say, because a flip that applied
//      here would turn the click that gets you out into the one that doubles
//      you up;
//   5. flat again, it applies again.
//
//   node reversecheck.mjs
//   node reversecheck.mjs --headed
//
// **It writes nothing.** A replay opens its attempt on the first fill, and this
// file takes a trade — so every non-GET to /api/replays is answered here rather
// than by the API, for the reason drillcheck spells out. Left to the real
// server this check would leave a sitting in data/replays and a trade in the
// journal on every run.
import { healthyAccount, launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });

// Anything that would write, swallowed. The account is stubbed healthy for
// presetcheck's reason: a session that owes a review opens *in* the review, and
// there is no ticket there to press.
let wrote = 0;
await page.route("**/api/replays**", async (route) => {
  const url = new URL(route.request().url());
  const method = route.request().method();
  if (url.pathname.endsWith("/replays/account")) {
    return route.fulfill({ json: healthyAccount() });
  }
  if (method === "GET" && url.pathname.endsWith("/api/replays")) {
    return route.fulfill({ json: { attempts: [] } });
  }
  if (method !== "GET") {
    wrote += 1;
    return route.fulfill({ json: { ok: true } });
  }
  return route.fallback();
});

const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

/** The two market buttons in DOM order, as [label, class-side] pairs. Filtered
 *  to the sides: `Close` is a `.sim-quick-btn` too and appears ahead of them the
 *  moment a position is on, so an index into the family would start reading the
 *  wrong button exactly when this check gets interesting. */
const pair = () =>
  page.$$eval(".sim-quick-btn.buy, .sim-quick-btn.sell", (els) =>
    els.map((e) => [e.querySelector("span")?.textContent?.trim(), e.classList.contains("buy") ? "buy" : "sell"]),
  );

const chipClass = () =>
  page.$eval("button.sim-knob-rr.rev", (e) => e.className).catch(() => null);

const position = () =>
  page.$eval(".sim-quick-pos", (e) => e.textContent.trim()).catch(() => null);

try {
  await openChart(page, "/charts/replay");

  // The chip is on the floating ticket from the first frame — it needs no ruler
  // reading, unlike the preset chip beside it — so it is waited on directly
  // rather than by running the tape first.
  await page.waitForSelector("button.sim-knob-rr.rev", { timeout: 60000 });
  await page.waitForSelector(".sim-quick-btn.buy:not([disabled])", { timeout: 60000 });

  const off = await pair();
  check(
    "off — SELL then BUY, each its own colour",
    JSON.stringify(off) === JSON.stringify([["SELL", "sell"], ["BUY", "buy"]]),
    JSON.stringify(off),
  );
  check("off — chip is not lit", !(await chipClass()).includes(" on"), await chipClass());

  await page.locator("button.sim-knob-rr.rev").click();
  const on = await pair();
  check(
    "on and flat — the pair swaps, label and colour together",
    JSON.stringify(on) === JSON.stringify([["BUY", "buy"], ["SELL", "sell"]]),
    JSON.stringify(on),
  );
  const lit = await chipClass();
  check("on and flat — chip lit, not struck through", lit.includes(" on") && !lit.includes("idle"), lit);

  // Run the tape to take the trade. It has to be *playing*: a market order is
  // filled by the next print, and on a paused replay there is no next print —
  // the order would sit there and the position chip would never appear.
  for (let i = 0; i < 8; i++) await page.keyboard.press("]");
  await page.keyboard.press("k");

  // The left button. It is the one that has always meant sell — and now reads
  // BUY, so a long is what has to come back.
  await page.locator(".sim-quick-btn.buy, .sim-quick-btn.sell").first().click();
  await page.waitForSelector(".sim-quick-pos", { timeout: 60000 });
  const pos = await position();
  await page.keyboard.press("k");
  check("the left button opened a LONG", /LONG/.test(pos ?? ""), pos ?? "no position");

  const held = await pair();
  check(
    "size on — the knob stands down, buttons mean what they say",
    JSON.stringify(held) === JSON.stringify([["SELL", "sell"], ["BUY", "buy"]]),
    JSON.stringify(held),
  );
  const idle = await chipClass();
  check("size on — chip struck through", idle.includes(" on") && idle.includes("idle"), idle);

  await shot(page, "reverse-held");

  // Flat again, and it applies again.
  await page.keyboard.press("k");
  await page.locator(".sim-quick-btn.flat").click();
  await page.waitForSelector(".sim-quick-pos", { state: "detached", timeout: 60000 });
  await page.keyboard.press("k");
  const flat = await pair();
  check(
    "flat again — the pair swaps back",
    JSON.stringify(flat) === JSON.stringify([["BUY", "buy"], ["SELL", "sell"]]),
    JSON.stringify(flat),
  );

  check("nothing reached the API that would have written", wrote > 0, `${wrote} write(s) intercepted`);
  check("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} finally {
  const bad = results.filter(([, ok]) => !ok);
  console.log(`\n${results.length - bad.length}/${results.length} passed`);
  if (errors.length) console.log(`errors:\n  ${errors.slice(0, 8).join("\n  ")}`);
  await browser.close();
  process.exit(bad.length ? 1 : 0);
}
