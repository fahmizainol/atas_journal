// Prior days, which now follow the bar.
//
// The rule itself (lib/contextDays) is arithmetic and needs no browser. What
// does need one is the wiring, and specifically the part that is easy to get
// wrong and impossible to see from tsc: the count is derived from state that is
// declared *before* the fetch that uses it, on a page whose hooks run in a fixed
// order. Moving those declarations is exactly the kind of change that compiles
// and then throws on mount.
//
// So this asks:
//
//   1. the page still mounts and draws — the hook move did not break it;
//   2. pressing a slower bar raises "Prior days" on its own, and pressing the
//      fast one back puts it down again. It is a setting per bar, not a
//      high-water mark: 1m means one day whichever bar you arrived from. The
//      days themselves are cached by date, so going back up is not a refetch;
//   3. the control names the bar it belongs to, since the number now means
//      something different depending on which button is lit;
//   4. an override sticks to *that* bar and not to the page: set 15m to
//      something, go to 1h, come back, and 15m is where you left it;
//   5. ↺ appears only on an overridden bar and puts it back on the rule.
//
// Only /charts/replay. The Live page carries the same control, but loading it
// auto-connects a routed session — see docs; it is manual-test-only and this
// harness must never script it.
//
//   node tools/browser/priordayscheck.mjs
//   node tools/browser/priordayscheck.mjs --headed
import { healthyAccount, launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });

// Same reason presetcheck stubs it: a session that owes a review opens *in* the
// review, where the setup panel this control lives in is not rendered at all.
// Nothing is written.
await page.route("**/api/replays**", async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname.endsWith("/replays/account")) {
    return route.fulfill({ json: { ...healthyAccount(), account: "funded" } });
  }
  return route.fallback();
});

const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

/** The "Prior days" label, and the three things it now carries. Anchored on the
 *  label's own text rather than a class: the control is a plain <label> in the
 *  setup panel and has never needed a hook for anything else. */
const priorDays = () =>
  page.evaluate(() => {
    const label = [...document.querySelectorAll("label")].find((l) =>
      /^\s*Prior days/.test(l.textContent ?? ""),
    );
    if (!label) return null;
    const select = label.querySelector("select");
    return {
      days: select ? Number(select.value) : null,
      // The caption under the select: the bar's name, and "· default N" when the
      // bar is on a number you chose.
      caption: label.querySelector("span:last-of-type")?.textContent?.trim() ?? "",
      reset: !!label.querySelector("button"),
      options: select ? [...select.options].map((o) => Number(o.value)) : [],
    };
  });

/** The setup panel this control lives in, open.
 *
 *  It is a drawer behind the top bar's title, and it starts shut. `priorDays()`
 *  above reads the DOM and so does not care, but anything that *drives* the
 *  control does: a select inside a closed drawer is present and unclickable, and
 *  the failure reads as a missing element rather than a shut panel. */
async function openSetup() {
  const title = page.locator(".chart-topbar-title").first();
  if ((await title.getAttribute("aria-expanded")) !== "true") {
    await title.click();
    await page.waitForTimeout(350);
  }
}

/** Press a bar on the top bar's timeframe row. The buttons past the primary few
 *  live behind ⋯, so this opens it when the flat row does not have the one
 *  asked for. */
async function pressTf(label) {
  const flat = page.locator(`.radio-group button:text-is("${label}")`).first();
  if (await flat.isVisible().catch(() => false)) {
    await flat.click();
  } else {
    await page.locator("button.tf-more").first().click();
    await page.waitForTimeout(150);
    await page.locator(`.tf-pop button:text-is("${label}")`).first().click();
  }
  // The context refetch is what the press is for; give it a moment to settle.
  await page.waitForTimeout(900);
}

try {
  await openChart(page, "/charts/replay");
  check("the replay page mounts and draws", true);

  const opened = await priorDays();
  check("the Prior days control is on the page", opened !== null);
  if (opened === null) {
    await shot(page, "priordays-missing");
    throw new Error("no control to drive");
  }
  check(
    "20 is on offer, for the slow end",
    opened.options.includes(20),
    `options ${opened.options.join(",")}`,
  );

  // 2. the bar raises it, and going back does not lower it.
  await pressTf("1m");
  const at1m = await priorDays();
  check("1m opens on one day", at1m.days === 1, `${at1m.days} days`);
  check("the caption names the bar", /^1m/.test(at1m.caption), `"${at1m.caption}"`);

  await pressTf("15m");
  const at15m = await priorDays();
  check("15m raises it to three", at15m.days === 3, `${at15m.days} days`);

  await pressTf("1h");
  const at1h = await priorDays();
  check("1h raises it to ten", at1h.days === 10, `${at1h.days} days`);
  check("the caption follows the bar", /^1h/.test(at1h.caption), `"${at1h.caption}"`);

  await pressTf("4h");
  const at4h = await priorDays();
  check("4h is on the ladder and asks for twenty", at4h.days === 20, `${at4h.days} days`);

  await pressTf("1m");
  const back = await priorDays();
  check("back on 1m it is one again, not stuck at twenty", back.days === 1, `${back.days} days`);

  // 4 + 5. an override belongs to one bar.
  await pressTf("15m");
  await openSetup();
  await page.locator("label:has-text('Prior days') select").first().selectOption("5");
  await page.waitForTimeout(700);
  const set = await priorDays();
  check("15m takes the override", set.days === 5, `${set.days} days`);
  check("the caption admits it is off the default", /default 3/.test(set.caption), `"${set.caption}"`);
  check("↺ appears on an overridden bar", set.reset);

  await pressTf("1h");
  const other = await priorDays();
  check("the override did not follow to 1h", other.days === 10, `${other.days} days`);
  check("↺ is absent on a bar that is on the rule", !other.reset);

  await pressTf("15m");
  const remembered = await priorDays();
  check("15m is where it was left", remembered.days === 5, `${remembered.days} days`);

  // The override survives a reload — it is a preference, not page state.
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(1200);
  const reloaded = await priorDays();
  check("and survives a reload", reloaded?.days === 5, `${reloaded?.days} days`);

  await openSetup();
  await page.locator("label:has-text('Prior days') button").first().click();
  await page.waitForTimeout(700);
  const reset = await priorDays();
  check("↺ puts the bar back on the rule", reset.days === 3, `${reset.days} days`);
  check("and takes itself away", !reset.reset);

  await shot(page, "priordays");
} catch (e) {
  check("ran to the end", false, String(e).slice(0, 160));
} finally {
  const crashes = errors.filter((e) => !/favicon|ERR_/.test(e));
  check("nothing threw in the page", crashes.length === 0, crashes.slice(0, 2).join(" | "));
  await browser.close();
}

const failed = results.filter(([, ok]) => !ok);
console.log(failed.length ? `\n${failed.length} FAILED` : "\nall ok");
process.exit(failed.length ? 1 : 0);
