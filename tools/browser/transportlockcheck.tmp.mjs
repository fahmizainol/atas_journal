// Throwaway: the funded replay has one lever, and it is Pause.
//
// `/charts/replay` runs the tape the way the day ran it — forward, at real
// time, or stopped. Every control that exists to *not sit through* the tape is
// gone: no step, no rewind, no speed ladder, no scrubber, and a held Ctrl buys
// nothing. Every other sitting keeps the lot, so the paper page is the control
// group that says this is a rule and not a regression.
//
// Seven claims:
//
//   1. the transport is Play/Pause and the clock, and nothing else
//   2. Play runs the tape and k stops it
//   3. `.` `,` `]` `[` move the clock by nothing
//   4. holding Ctrl does not multiply the rate
//   5. "Go to start" is gone, but the start time is still offered
//   6. `/charts/replay/paper` still has all of it — including a Ctrl worth 10×,
//      which is what proves claim 4 measured something
//   7. no console errors
//
//   node transportlockcheck.tmp.mjs [--headed]
//
// **It writes nothing.** No order is ever placed, so no sitting is opened; the
// account stub is only so a real cooldown can't put a notice over the page.
import { healthyAccount, launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

// The shared factory, not a fourth hand-written copy — a stub that drifts from
// the real payload crashes the page before it renders and the failure points at
// `.chart-legend` instead of at the stub.
await page.route("**/api/replays/account", (route) => route.fulfill({ json: healthyAccount() }));
// The attempts list, empty. A real finished-and-owing sitting leaking in would
// open the page in *review*, and a review is exactly the state that lifts the
// lock — so without this the check could fail by finding the transport it is
// asserting the absence of.
await page.route("**/api/replays", (route) =>
  route.request().method() === "GET"
    ? route.fulfill({ json: { attempts: [] } })
    : route.fallback(),
);

/** The replay clock in seconds, off the transport's own readout — which is the
 *  only place it is left on a locked page, the scrubber being gone. The
 *  countdown rides in the same element, so match the HH:MM:SS rather than
 *  parsing the whole string. */
const clockS = async () => {
  const t = (await page.locator(".sim-clock").first().innerText()) ?? "";
  const m = /(\d\d):(\d\d):(\d\d)/.exec(t);
  if (!m) return NaN;
  return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
};
const paused = async () =>
  ((await page.locator(".sim-transport button").first().textContent()) ?? "").includes("Play");
const ensurePaused = async () => {
  if (!(await paused())) {
    await page.keyboard.press("k");
    await page.waitForTimeout(400);
  }
};
/** Run the tape for `ms` of wall clock and report the tape-seconds it covered.
 *
 *  `ctrl` holds Ctrl for the run — **after** the k that starts it and released
 *  before the k that stops it. The page's key bindings all stand down while a
 *  chord is up (`isTypingTarget`), so a Ctrl-held `k` is not a play at all: the
 *  first draft of this file held Ctrl across both presses, never started the
 *  tape, and read the resulting 0s as "turbo is inert". */
const runFor = async (ms, ctrl = false) => {
  const before = await clockS();
  await page.keyboard.press("k");
  if (ctrl) await page.keyboard.down("Control");
  await page.waitForTimeout(ms);
  if (ctrl) await page.keyboard.up("Control");
  await page.keyboard.press("k");
  await page.waitForTimeout(300);
  return (await clockS()) - before;
};

try {
  await openChart(page, "/charts/replay");
  await page.evaluate(() => document.activeElement?.blur?.());
  await ensurePaused();

  const btns = await page.locator(".sim-transport button").allTextContents();
  check("the transport is one button", btns.length === 1, btns.join(" | ") || "none");
  check("and it is Play/Pause", /Play|Pause/.test(btns[0] ?? ""), btns[0] ?? "");
  check("no scrubber", (await page.locator(".sim-scrub").count()) === 0);
  check("no speed ladder", (await page.locator(".sim-transport select").count()) === 0);
  check("the clock is still read out", Number.isFinite(await clockS()), String(await clockS()));

  // 2. Play runs it, k stops it. ~2.5s of wall clock at a pinned 1× is ~2.5s of
  // tape; the window is wide because the first frame after a press costs a
  // rAF and the readout is whole seconds.
  const ran = await runFor(2500);
  check(`Play runs the tape (+${ran}s of tape in 2.5s)`, ran >= 1 && ran <= 6, `${ran}s`);
  check("and k left it paused", await paused());

  // 3. The transport keys reach callbacks that stand down, so a paused clock
  // stays exactly where it was. Whole seconds, so this is an equality.
  const still = await clockS();
  for (const k of [".", ",", "]", "["]) {
    await page.keyboard.press(k);
    await page.waitForTimeout(250);
  }
  check(`. , ] [ move nothing (${still}s)`, (await clockS()) === still, `${await clockS()}s`);
  check("and none of them started it", await paused());

  // 4. Turbo is worth 10×. If it leaked, the same 2.5s window would cover ~25s
  // of tape instead of ~2.5 — so this separates cleanly from claim 2's window.
  const turbo = await runFor(2500, true);
  check(`held Ctrl buys nothing (+${turbo}s, not ~25s)`, turbo >= 1 && turbo <= 6, `${turbo}s`);

  // The last way forward: "Go to start" re-seeks the *running* session to the
  // chosen start time, which on a locked rep is a scrubber with an extra step.
  // The start time input itself stays — it still picks where a new sitting
  // begins — so this is specifically about the button.
  // A CSS locator, not getByRole: the setup panel is `display: none` until you
  // summon it, and getByRole filters hidden nodes out — so it answers 0 on both
  // pages and the assertion would pass without measuring anything.
  const gotoStart = () => page.locator(".sim-setup button", { hasText: "Go to start" }).count();
  check("no Go-to-start button", (await gotoStart()) === 0);
  check("but the start time is still offered",
    (await page.locator('.sim-setup input[type="time"], .sim-setup select').count()) > 0);

  // 5. The control group. Same page, different account, full transport.
  await openChart(page, "/charts/replay/paper");
  await page.evaluate(() => document.activeElement?.blur?.());
  await ensurePaused();
  const paperBtns = await page.locator(".sim-transport button").allTextContents();
  check("paper still steps", paperBtns.length >= 3, paperBtns.join(" | "));
  check("paper still has the speed ladder",
    (await page.locator(".sim-transport select").count()) === 1);
  check("paper still has the scrubber", (await page.locator(".sim-scrub").count()) === 1);
  check("paper still has Go to start", (await gotoStart()) === 1);

  // And the same Ctrl-hold that bought nothing above is worth ~10× here, which
  // is what makes claim 4 an assertion rather than a probe that measures
  // nothing. Same 1× base so the two numbers are directly comparable.
  await page.selectOption(".sim-transport select", "1");
  await page.evaluate(() => document.activeElement?.blur?.());
  const paperTurbo = await runFor(2500, true);
  check(`paper's Ctrl still fast-forwards (+${paperTurbo}s)`, paperTurbo >= 10, `${paperTurbo}s`);

  check(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  console.log(`\n!! ${e?.stack ?? e}`);
  console.log(await page.evaluate(() => document.body.innerText.slice(0, 1200)).catch(() => "?"));
  await page.screenshot({ path: "shots/transportlockcheck-fail.png" }).catch(() => {});
  results.push(["ran to the end", false, String(e?.message ?? e)]);
} finally {
  console.log(`\n${results.filter((r) => r[1]).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.every((r) => r[1]) ? 0 : 1);
}
