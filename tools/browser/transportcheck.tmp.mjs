// Throwaway: the transport with a position on.
//
// It used to take itself away the moment you were holding something. It stays
// now, and the one dishonest move — a rewind back through your own fill, which
// truncates the log and un-happens the trade you are in — is refused at
// `seekTo` and fenced off by the scrubber's `min`. Four claims:
//
//   1. the row is still in flow with size on
//   2. the scrubber's floor has moved up to the fill
//   3. a backward step (`,`) is refused, and the position is still there after
//   4. flat again, the floor drops back to the session start
//
//   node transportcheck.tmp.mjs [--headed]
//
// **It writes nothing.** Same stubs as panecheck: the first fill would open a
// real sitting in data/replays otherwise, and the account gate would then 409
// the next run for an hour.
import { launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

await page.route("**/api/replays", (route) =>
  route.request().method() === "POST"
    ? route.fulfill({
        json: {
          id: "2026-01-01_NQH5_20260101T000000Z",
          status: "active",
          repeat_index: 0,
          note: "",
          model_id: null,
        },
      })
    : route.fallback(),
);
await page.route("**/api/replays/*", (route) =>
  route.request().method() === "PUT" ? route.fulfill({ json: { ok: true } }) : route.fallback(),
);
await page.route("**/api/replays/account", (route) =>
  route.fulfill({
    json: {
      now: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      equity: 50000, floor: 48000, peak_close: 50000, status: "live",
      day_net: 0, day_loss_remaining: 1200, target_remaining: 3000,
      next_sitting_at: null, cooldown_until: null, can_reset: false,
      review_block: null,
      epoch: { index: 0, started_at: "2026-01-01T00:00:00Z", sittings: 0, net: 0 },
      last_death: null, caps: { minis: 4, micros: 40 },
      // `liveEquity` reads this one; without it the page throws the moment a
      // position opens (which is how the first draft of this file "failed").
      counted_ids: [],
    },
  }),
);
// This machine's guard levels refuse every NQ entry (100-tick floor against a
// $250 ceiling), which is a real thing and not what this file is about.
await page.route("**/live/routing", async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  body.guards = {
    ...body.guards,
    stop_ticks_min: 20, stop_ticks_max: 400, min_target_ticks: 20, max_risk_usd: 100_000,
  };
  await route.fulfill({ response: res, json: body });
});

const scrub = page.locator(".sim-scrub");
const floorMs = () => scrub.getAttribute("min").then(Number);
const clockMs = () => scrub.inputValue().then(Number);
const rowClass = () => page.locator(".sim-transport").getAttribute("class").then((c) => c ?? "");
const held = () => page.locator(".sim-quick-pos").count();

try {
  await openChart(page, "/charts/replay");
  // 1×, so the second of tape below is a print or two rather than a minute.
  await page.selectOption(".sim-transport select", "1");
  await page.evaluate(() => document.activeElement?.blur?.());
  // Paused, so nothing moves under the assertions.
  if ((await page.locator(".sim-transport button").first().textContent())?.includes("Pause")) {
    await page.keyboard.press("k");
    await page.waitForTimeout(400);
  }

  const flatFloor = await floorMs();
  check("the transport is in flow, flat", !(await rowClass()).includes("away"), await rowClass());

  // Settle before the gesture. `placeMarket` returns silently while the page is
  // not `ready` — no refusal, no order — and pressing w the instant the canvas
  // has ink is early enough to land in that window about half the time.
  await page.waitForTimeout(1500);
  await page.keyboard.press("w");
  // Read the refusal *now* if there is one: the Simulator clears it on a timer,
  // so the poll below would report "no refusal shown" for an order that was
  // very much refused.
  await page.waitForTimeout(250);
  const said = (await page.locator("[data-refusal]").first().textContent().catch(() => "")) ?? "";
  // A market order fills on the next print, and a paused tape has no next
  // print — so the poll runs with the tape *running*. **Not `.`**: one 1m step
  // is sixty seconds of tape at once, and the first draft of this file kept
  // opening and stopping out the position inside a single step, which reads
  // exactly like "w placed nothing". At 1× a fill is a second or two, and a
  // fixed sleep was one flaky run in three (the day is drawn fresh each run and
  // some of them are thin).
  await page.keyboard.press("k");
  for (let i = 0; i < 10 && !(await held()); i++) await page.waitForTimeout(400);
  await page.keyboard.press("k");
  await page.waitForTimeout(300);
  check("w fills on the replay", (await held()) > 0,
    said || (await page.evaluate(() =>
      [...document.querySelectorAll(".chart-legend-item")]
        .map((e) => e.textContent.trim())
        .find((t) => /^Trades ·/.test(t)) ?? "no trades layer")));

  check("and the transport stays with a position on",
    !(await rowClass()).includes("away"), await rowClass());

  const heldFloor = await floorMs();
  const at = await clockMs();
  check(`the scrubber's floor rises to the fill (${flatFloor} → ${heldFloor})`,
    heldFloor > flatFloor && heldFloor <= at, `clock ${at}`);

  // The keys reach the same choke point the handle does.
  await page.keyboard.press(",");
  await page.waitForTimeout(600);
  const refusal = await page.locator("[data-refusal]").first().textContent().catch(() => null);
  check("a step back through the entry is refused", /rewind past your entry/.test(refusal ?? ""),
    refusal?.trim() ?? "no refusal shown");
  check("and the position survived it", (await held()) > 0);

  // Play still works while holding — the whole point of keeping the row.
  const before = await clockMs();
  await page.keyboard.press("k");
  await page.waitForTimeout(1200);
  await page.keyboard.press("k");
  await page.waitForTimeout(300);
  check(`Play still runs the tape with size on (${before} → ${await clockMs()})`,
    (await clockMs()) > before);

  // Out. The exit is a market order too, so it also wants a print — same
  // running poll as the entry rather than a sleep against a stopped tape.
  await page.keyboard.press("q");
  await page.keyboard.press("k");
  for (let i = 0; i < 10 && (await held()); i++) await page.waitForTimeout(400);
  await page.keyboard.press("k");
  await page.waitForTimeout(300);
  check("flat again", (await held()) === 0);
  check(`the floor drops back to the session start (${await floorMs()})`,
    (await floorMs()) === flatFloor, `was ${flatFloor}`);

  check(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  console.log(`\n!! ${e?.stack ?? e}`);
  console.log(await page.evaluate(() => document.body.innerText.slice(0, 1200)).catch(() => "?"));
  await page.screenshot({ path: "shots/transportcheck-fail.png" }).catch(() => {});
  results.push(["ran to the end", false, String(e?.message ?? e)]);
} finally {
  console.log(`\n${results.filter((r) => r[1]).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.every((r) => r[1]) ? 0 : 1);
}
