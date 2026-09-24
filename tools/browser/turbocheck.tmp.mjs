// Throwaway: hold Ctrl, the tape runs ten times faster.
//
// The claim is arithmetic on a clock, which is one of the few things on this
// page a DOM read can settle: `.sim-scrub`'s value IS the replay clock, so the
// tape's rate is a difference over a wall-clock window. Three windows, one
// continuous play, no pause in between (a pause/play pair would put the rAF
// loop's first frame inside the measurement):
//
//   A  Ctrl up      — the set speed
//   B  Ctrl held    — should be ~10× A
//   C  Ctrl up      — back to A, i.e. the hold left nothing switched on
//
// Plus the chip, which is the only thing on screen that says the tape is not
// running at the number in the <select>.
//
//   node turbocheck.tmp.mjs [--headed]
//
// **It writes nothing** — it never places an order, so no sitting is opened.
import { launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const scrub = page.locator(".sim-scrub");
const clockMs = () => scrub.inputValue().then(Number);
const chip = () => page.locator(".sim-transport [title^='Ctrl held']");

/** Tape-ms per wall-second over a window. The clock publishes at 10 Hz, so the
 *  window has to be seconds rather than the ~200ms one frame would need. */
const WINDOW = 3000;
async function rate() {
  const a = await clockMs();
  await page.waitForTimeout(WINDOW);
  const b = await clockMs();
  return (b - a) / (WINDOW / 1000);
}

try {
  await openChart(page, "/charts/replay");
  // 3×, not 1×: fast enough that a 3s window is a real number, slow enough that
  // 10× of it doesn't run a short tape off its end mid-measurement.
  await page.selectOption(".sim-transport select", "3");
  await page.evaluate(() => document.activeElement?.blur?.());
  await page.waitForTimeout(500);

  // Play, and stay playing for all three windows.
  await page.keyboard.press("k");
  await page.waitForTimeout(800);

  const a = await rate();
  check(`plays at the set speed (${Math.round(a)} tape-ms/s)`, a > 0);

  await page.keyboard.down("Control");
  await page.waitForTimeout(300);
  check("the chip says what it is now running at",
    (await chip().count()) > 0 && /30×/.test((await chip().first().textContent()) ?? ""),
    (await chip().first().textContent().catch(() => null))?.trim() ?? "no chip");

  const b = await rate();
  const mult = a > 0 ? b / a : 0;
  // 6–14 rather than 10±ε: both windows are 10 Hz samples of a rAF loop, and
  // the ratio of two of them is noisy at the ends.
  check(`Ctrl runs it ~10× (${mult.toFixed(1)}×, ${Math.round(b)} tape-ms/s)`,
    mult > 6 && mult < 14);

  await page.keyboard.up("Control");
  await page.waitForTimeout(300);
  check("and the chip goes when it does", (await chip().count()) === 0);

  const c = await rate();
  check(`releasing gives the set speed back (${Math.round(c)} tape-ms/s)`,
    c > 0 && Math.abs(c - a) / a < 0.5);

  // The stuck-at-10× case: the keyup lands in another window and never arrives.
  await page.keyboard.down("Control");
  await page.waitForTimeout(300);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await page.waitForTimeout(300);
  check("losing focus mid-hold drops it", (await chip().count()) === 0);
  const d = await rate();
  check(`and the tape is back at the set speed (${Math.round(d)} tape-ms/s)`,
    d > 0 && Math.abs(d - a) / a < 0.5);
  await page.keyboard.up("Control");

  await page.keyboard.press("k");
} finally {
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} checks passed`);
  if (errors.length) console.log("page errors:\n  " + errors.join("\n  "));
  await browser.close();
  process.exit(bad || errors.length ? 1 : 0);
}
