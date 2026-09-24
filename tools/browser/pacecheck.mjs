// The trade-pace guardrail, in a real browser.
//
// The rule itself is six lines in `lib/guardRules.paceRefusal`, and it is
// already pinned against the corpus it was measured on: bundling that file with
// esbuild and replaying `data/replays` through it reproduces the study's fire
// rates to the figure (drill 21.3%, replay 46.0%). So the arithmetic is not
// what is left for a browser to answer. The wiring is:
//
//   1. nothing is said before three entries have landed — the window wants
//      three, and a guard that fires on trade one is a guard you switch off;
//   2. three entries close together in TAPE time light the chip on the top bar;
//   3. the full sentence in `GuardMeters` agrees with it — both surfaces read
//      one `paceRefusal` call, and two panels disagreeing about whether you are
//      trading too fast would be worse than neither;
//   4. an unhurried fourth entry rolls the burst out of the window and it goes
//      quiet again. A guard that latches on is one you stop reading.
//
// WHY THE CHIP AND NOT THE PANEL. This check first watched the `GuardMeters`
// line and timed out waiting for it to be visible: the sim's side panel renders
// at 0x0 until it is opened, so the banner was invisible in ordinary use. That
// is why `PaceChip` exists on the top bar at all — the harness found it. The
// panel line is still asserted, but read while *attached* rather than visible.
//
// WHY DRILL CARRIES THE CLEAR CASE. A replay bound to a real-time account has
// its transport locked (`Template.real_time`), so `[`, `]` and `.` stand down
// and the tape only ever runs at 1x — three tape-minutes would be three real
// ones. A drill is unpriced and has no account, so the speed ladder is live
// there and the burst can be aged in seconds. Replay still gets the fire case,
// which needs no transport at all.
//
//   node pacecheck.mjs
//   node pacecheck.mjs --headed
//
// Only the sim surfaces. `/charts/live` auto-connects a routed session merely by
// being loaded, so the harness must never drive it; Live is verified by hand.
//
// **It writes nothing.** A sitting opens on the first fill (a drill, at the
// drop) and this file takes five trades, so every non-GET to /api/replays is
// answered here — drillcheck and reversecheck spell out the reason. Left to the
// real server each run would leave sittings in data/replays and trades in the
// journal.
import { healthyAccount, launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });

let wrote = 0;
await page.route("**/api/replays**", async (route) => {
  const url = new URL(route.request().url());
  const method = route.request().method();
  // The drop is a read — it picks a random RTH clock and creates nothing, so it
  // is left to the server. Everything that would write is swallowed below.
  if (url.pathname.endsWith("/replays/drills") && method === "GET") return route.fallback();
  if (url.pathname.endsWith("/replays/account")) return route.fulfill({ json: healthyAccount() });
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

/** The chip on the top bar — what a person actually sees. */
const chip = () => page.$eval("[data-pace-chip]", (e) => e.textContent.trim()).catch(() => null);
/** The sentence in the collapsed panel, read attached rather than visible. */
const line = () =>
  page.$eval("[data-pace]", (e) => e.textContent.replace(/\s+/g, " ").trim()).catch(() => null);
const tally = () => page.$eval("[data-guard-meters]", (e) => e.textContent).catch(() => "");

/** One round trip: market in, flatten out. The tape has to be *playing* — a
 *  market order is filled by the next print, and a paused replay has none. */
async function roundTrip() {
  await page.locator(".sim-quick-btn.buy").click();
  await page.waitForSelector(".sim-quick-pos", { timeout: 60000 });
  await page.locator(".sim-quick-btn.flat").click();
  await page.waitForSelector(".sim-quick-pos", { state: "detached", timeout: 60000 });
}

const nudge = async (key, n) => {
  for (let i = 0; i < n; i++) await page.keyboard.press(key);
  await page.waitForTimeout(150);
};

async function arrive(route) {
  await openChart(page, route);
  await page.waitForSelector(".sim-quick-btn.buy:not([disabled])", { timeout: 60000 });
  await page.waitForSelector("[data-guard-meters]", { state: "attached", timeout: 60000 });
}

try {
  // ---- drill: the whole arc, because the transport is available here -------
  await arrive("/charts/backtest");
  check("drill · flat and untraded — nothing said", (await chip()) === null);

  // Down to 1x. A drill opens near the top of the ladder, where three clicks
  // are minutes of tape apart and would be right not to fire.
  await nudge("[", 6);
  await page.keyboard.press("k");
  // Let the tape actually start. A market order placed into a stopped replay
  // rests until the next print, and flattening it books no trade — which was
  // silently costing this check its first entry and, with it, the burst.
  await page.waitForTimeout(1500);

  await roundTrip();
  check("drill · one trade — still nothing", (await chip()) === null);
  await roundTrip();
  check("drill · two trades — still nothing, the window wants three", (await chip()) === null);

  await roundTrip();
  const lit = await chip();
  check("drill · three entries inside three tape-minutes — chip up", lit != null, lit ?? "absent");
  const said = await line();
  check(
    "drill · the collapsed panel says the same thing, in full",
    !!said && /market time/.test(said) && /Nothing is refused/.test(said),
    said ?? "absent",
  );
  await shot(page, "pace-lit");

  // Age the burst: back up the ladder and let real tape pass, so the fourth
  // entry lands more than three tape-minutes after the second.
  await nudge("]", 4);
  await page.waitForTimeout(9000);
  await roundTrip();
  check("drill · an unhurried fourth entry rolls the burst out — chip clears", (await chip()) === null, (await chip()) ?? "absent");
  check("drill · and the panel line goes with it", (await line()) === null);
  check("drill · four trades actually happened", /4 trades/.test(await tally()), (await tally()).slice(0, 40));
  await shot(page, "pace-cleared");

  // ---- replay: the same chip is fed on the other surface -------------------
  // No transport here (a real-time account locks it), so only the fire case —
  // which is the one that needs no speed control.
  await arrive("/charts/replay");
  check("replay · flat and untraded — nothing said", (await chip()) === null);
  await page.keyboard.press("k");
  await page.waitForTimeout(1500);
  await roundTrip();
  await roundTrip();
  await roundTrip();
  check("replay · three trades landed", /3 trades/.test(await tally()), (await tally()).slice(0, 40));
  const litR = await chip();
  check("replay · three quick entries light the same chip", litR != null, litR ?? "absent");

  check("nothing reached the API that would have written", wrote > 0, `${wrote} write(s) intercepted`);
  check("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  // Without this the `finally` exits 0 on a thrown selector timeout and the run
  // prints "0/0 passed", which reads like a pass.
  check("the check itself ran to the end", false, String(e).split("\n")[0]);
} finally {
  const bad = results.filter(([, ok]) => !ok);
  console.log(`\n${results.length - bad.length}/${results.length} passed`);
  if (errors.length) console.log(`errors:\n  ${errors.slice(0, 8).join("\n  ")}`);
  await browser.close();
  process.exit(bad.length ? 1 : 0);
}
