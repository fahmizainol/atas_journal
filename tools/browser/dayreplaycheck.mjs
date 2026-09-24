// The Journal day's built-in tape replayer, which replaced the screen recording.
//
// What this asks:
//
//   1. the day opens with a replay toggle and no recording toggle;
//   2. opening it gives a *sized* canvas with ink on it. This is the one that
//      matters: the panel is mounted on first open precisely because a chart
//      under `display:none` measures 0x0, and a snapshot pushed into a
//      zero-width time scale frames against nothing. A regression here draws a
//      blank panel and throws nothing;
//   3. a row's ▶ opens the panel, seeks, and plays — the clock moves and the
//      canvas changes;
//   4. pause actually cancels the rAF loop: two samples half a second apart are
//      identical;
//   5. the scrub reaches both ends of the session, and the chart fills as the
//      clock advances;
//   6. collapsing and re-expanding recovers both the size and the ink.
//
// Every probe is scoped to `[data-day-replay]`. The page also carries
// DaySessionChart, and the shared helpers in lib.mjs take the biggest canvas in
// the *document* — which is whichever of the two happens to be larger.
//
// Run: node tools/browser/dayreplaycheck.mjs [--headed]
import { launch, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");

/** A day the journal has trades on *and* a tick tape exists for. Overridable:
 *  which days are on disk is a property of this machine. */
const DAY = process.env.JOURNAL_DAY ?? "2026-06-30";
const MODES = process.env.JOURNAL_MODES ?? "replay";

const ROOT = "[data-day-replay]";

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** The replayer's own canvas, read back: its size, and how much of it is not
 *  the background. `sig` is a coarse fingerprint — enough to say two samples
 *  differ without asserting exact pixels. */
async function probeReplay(page) {
  return page.evaluate((sel) => {
    const root = document.querySelector(sel);
    if (!root) return { error: "no panel" };
    const c = [...root.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return { error: "no canvas" };
    if (!c.width || !c.height) return { error: "zero-sized canvas", w: c.width, h: c.height };
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
    return { w: c.width, h: c.height, bg, ink, sig };
  }, ROOT);
}

// The one `.sim-clock` on the page — the Simulator, which owns the other, is a
// different route.
const clockText = (page) => page.locator(".sim-clock").first().innerText();

async function waitForInk(page, timeout = 60000) {
  await page.waitForFunction(
    (sel) => {
      const root = document.querySelector(sel);
      if (!root) return false;
      const c = [...root.querySelectorAll("canvas")].sort(
        (a, b) => b.width * b.height - a.width * a.height,
      )[0];
      if (!c || !c.width || !c.height) return false;
      const d = c.getContext("2d").getImageData(0, 0, c.width, Math.min(c.height, 200)).data;
      for (let i = 4; i < d.length; i += 4) {
        if (d[i] !== d[0] || d[i + 1] !== d[1] || d[i + 2] !== d[2]) return true;
      }
      return false;
    },
    ROOT,
    { timeout },
  );
  await page.waitForTimeout(500);
}

const { browser, page, errors } = await launch({ headed });
try {
  console.log(`journal day — ${BASE}/calendar/${DAY}?mode=${MODES}`);
  await page.goto(`${BASE}/calendar/${DAY}?mode=${MODES}`, { waitUntil: "networkidle" });

  const toggle = page.locator("button", { hasText: "Show replay" }).first();
  await toggle.waitFor({ timeout: 30000 });
  ok("day offers a replay toggle", await toggle.isVisible());
  ok(
    "the recording toggle is gone",
    (await page.locator("button", { hasText: "Show recording" }).count()) === 0,
  );
  ok("the Replay column is in the table", (await page.locator("th", { hasText: "Replay" }).count()) > 0);

  // 2 — a panel that opens sized, not 0x0.
  await toggle.click();
  await page.waitForSelector(ROOT, { timeout: 30000 });
  await waitForInk(page);
  await shot(page, "dayreplay-open");
  const opened = await probeReplay(page);
  console.log(`  canvas: ${opened.w}x${opened.h}, ink ${opened.ink}`);
  ok("panel canvas is sized", !opened.error && opened.w > 200 && opened.h > 100, opened.error ?? "");
  ok("panel canvas has ink", (opened.ink ?? 0) > 200, `${opened.ink} samples`);

  // 3 — a row's ▶ seeks and plays.
  const seek = page.locator("table tbody tr").first().locator("button", { hasText: "▶" }).first();
  ok("a trade row offers ▶", (await seek.count()) > 0);
  if ((await seek.count()) > 0) {
    const before = await clockText(page);
    await seek.click();
    await page.waitForTimeout(1200);
    const after = await clockText(page);
    ok("the seek moved the clock", after !== before, `${before} → ${after}`);
    const a = await probeReplay(page);
    await page.waitForTimeout(1000);
    const b = await probeReplay(page);
    ok("the tape is playing", a.sig !== b.sig, `${(await clockText(page))}`);
  }

  // 4 — pause cancels the loop.
  const pause = page.locator("button", { hasText: "Pause" }).first();
  if ((await pause.count()) > 0) {
    await pause.click();
    await page.waitForTimeout(300);
    const p1 = await probeReplay(page);
    await page.waitForTimeout(500);
    const p2 = await probeReplay(page);
    ok("pause stops the loop", p1.sig === p2.sig && p1.ink === p2.ink);
  } else {
    ok("pause stops the loop", false, "no Pause button — did the seek not play?");
  }

  // 5 — the scrub reaches both ends.
  const scrub = page.locator(".sim-scrub").first();
  // The session's last tick lands wherever it lands, so `max` is almost never a
  // whole number of `step`s above `min` — and a range input only accepts values
  // on that grid. Snap the top end down; it costs under a second of tape.
  const bounds = await scrub.evaluate((el) => {
    const min = Number(el.min);
    const step = Number(el.step) || 1;
    return { min: el.min, max: String(min + Math.floor((Number(el.max) - min) / step) * step) };
  });
  await scrub.fill(bounds.min);
  await page.waitForTimeout(800);
  const atStart = await probeReplay(page);
  const startClock = await clockText(page);
  await scrub.fill(bounds.max);
  await page.waitForTimeout(1500);
  const atEnd = await probeReplay(page);
  const endClock = await clockText(page);
  ok("the scrub reaches both ends", startClock !== endClock, `${startClock} → ${endClock}`);
  ok(
    "the chart fills as the clock advances",
    (atEnd.ink ?? 0) > (atStart.ink ?? 0),
    `${atStart.ink} → ${atEnd.ink} ink`,
  );
  await shot(page, "dayreplay-end");

  // 6 — collapse and re-expand.
  await page.locator("button", { hasText: "Hide replay" }).first().click();
  await page.waitForTimeout(400);
  await page.locator("button", { hasText: "Show replay" }).first().click();
  await page.waitForTimeout(1200);
  const back = await probeReplay(page);
  ok(
    "re-expanding recovers the canvas",
    !back.error && back.w === atEnd.w && back.h === atEnd.h,
    back.error ?? `${back.w}x${back.h}`,
  );
  ok("re-expanding recovers the ink", (back.ink ?? 0) > 200, `${back.ink} samples`);

  const noisy = errors.filter((e) => !/favicon/i.test(e));
  ok("nothing threw", noisy.length === 0, noisy.slice(0, 3).join(" | "));
} catch (e) {
  console.error(e);
  await shot(page, "dayreplay-error");
  fails.push(`threw: ${e.message}`);
} finally {
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} failed: ${fails.join(", ")}` : "\nall ok");
process.exit(fails.length ? 1 : 0);
