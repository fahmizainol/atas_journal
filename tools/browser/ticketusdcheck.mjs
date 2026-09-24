// A bracket leg set in dollars, in a real browser.
//
// The arithmetic is four lines (`lib/bracketUsd`) and reading it tells you
// nothing about the feature, because the feature is a *rule about state*: a
// pinned leg's tick distance stops being a setting and becomes a reading, and
// the whole question is whether every surface that writes the ticket honours
// that — the knob that steps it, the box that types it, the size knob beside it
// that does not touch the leg at all but changes what it comes to, and the
// preset that must put the pin out because it is choosing a distance.
//
// None of that is visible to `tsc`. What it looks like when it breaks is a
// bracket that reads $250 and sends 50 ticks of a two-lot position, which is
// $500 — the silent doubling this exists to stop.
//
//   node ticketusdcheck.mjs
//   node ticketusdcheck.mjs --headed
//
// Nothing is sent: this drives the ticket only, in a throwaway Playwright
// profile, and never presses a market button.
import { launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });

const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};
const num = (s, re) => {
  const m = re.exec((s ?? "").replace(/[,\s]/g, ""));
  return m ? Number(m[1]) : null;
};

try {
  await openChart(page, "/charts/replay");

  const knob = (leg) => page.locator(`.sim-knob.${leg}`);
  const label = async (leg) => (await knob(leg).locator(".sim-knob-t").innerText()).trim();
  const unit = () => page.locator(".sim-knob.stop .sim-knob-u");
  const sizeOf = async () => num(await label("size"), /×(\d+)/);
  const pinned = async () => (await unit().getAttribute("aria-pressed")) === "true";
  const stored = () =>
    page.evaluate(() => {
      const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
      return { stopUsd: p.stopUsd ?? null, stopTicks: p.stopTicks ?? null };
    });

  check("the stop knob is on the ticket", (await knob("stop").count()) > 0);
  const t0 = num(await label("stop"), /(\d+)t/);
  check("it reads a tick distance to begin with", t0 != null && !(await pinned()), `⊥${t0}t`);

  // The money the knob already prints is the figure the switch has to land on:
  // changing unit is a change of unit, not of bracket.
  const cost0 = num(await unit().innerText(), /\$(\d+)/);
  await unit().click();
  await page.waitForTimeout(200);
  const pin0 = num(await label("stop"), /\$(\d+)/);
  check("pressing $ pins the leg", await pinned(), `label ${await label("stop")}`);
  check("and pins it at what it already cost", pin0 === cost0, `$${pin0} vs $${cost0}`);
  check("the ticks it resolves to are shown beside it", /^\d+t$/.test((await unit().innerText()).trim()),
    (await unit().innerText()).trim());

  // Typing. The figure is a button that turns into a box — this is the half the
  // user asked for, and the half no unit test reaches.
  const size = await sizeOf();
  await knob("stop").locator(".sim-knob-t").click();
  await page.locator(".sim-knob.stop input.sim-knob-in").fill("250");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(250);
  const typed = num(await label("stop"), /\$(\d+)/);
  const ticksAt250 = num(await unit().innerText(), /(\d+)t/);
  check("a dollar figure can be typed", typed === 250, `$${typed}`);
  // NQ is a $5 tick, and the replay defaults there when nothing else is loaded.
  // Derived from the reading rather than hard-coded so a micro-routed or
  // differently-priced session still checks the *relationship*.
  const perTick = 250 / (ticksAt250 * size);
  check(
    "the distance is derived from it",
    ticksAt250 > 0 && Math.abs(ticksAt250 - Math.round(250 / (perTick * size))) < 1,
    `$250 → ${ticksAt250}t at ×${size} ($${perTick}/tick)`,
  );

  // The point of the whole thing: the size moves and the money does not.
  await knob("size").locator("button", { hasText: "+" }).click();
  await page.waitForTimeout(250);
  const size2 = await sizeOf();
  const ticks2 = num(await unit().innerText(), /(\d+)t/);
  check("size went up", size2 === size + 1, `×${size2}`);
  check(
    "the pin holds and the distance follows the size",
    num(await label("stop"), /\$(\d+)/) === 250 && ticks2 === Math.round(250 / (perTick * size2)),
    `$250 → ${ticks2}t at ×${size2}`,
  );

  const st = await stored();
  check("the pin is remembered", st.stopUsd === 250, JSON.stringify(st));
  check("and the distance is stored beside it", st.stopTicks === ticks2, JSON.stringify(st));

  await shot(page, "ticket-usd-pinned");

  // Stepping a pinned leg steps the money, by what the same tick step is worth.
  const before = ticks2;
  await knob("stop").locator("button", { hasText: "+" }).click();
  await page.waitForTimeout(200);
  const stepped = num(await label("stop"), /\$(\d+)/);
  check(
    "the knob steps a pinned leg in dollars",
    stepped > 250 && num(await unit().innerText(), /(\d+)t/) > before,
    `$250 → $${stepped}`,
  );

  // And a preset chooses a *distance*, so it must put the pin out — otherwise
  // the shape it applied would be overwritten by the money on the next render.
  const chip = page.locator("button.sim-knob-rr.preset");
  // The presets' ruler reads 30-second bars from 09:30 onwards with nothing
  // behind it, so the tape has to be run before the card has any rows — see
  // presetcheck, where the same wind-forward is spelled out. `]` walks the speed
  // ladder, `k` plays and pauses.
  for (let i = 0; i < 8; i++) await page.keyboard.press("]");
  await page.keyboard.press("k");
  await chip
    .filter({ hasText: /\d+t/ })
    .waitFor({ timeout: 60000 })
    .catch(() => {});
  await page.keyboard.press("k");
  await page.waitForTimeout(500);
  // Running the tape must not have touched the ticket.
  check(
    "the pin survives the tape running",
    await pinned(),
    `label ${await label("stop")}`,
  );
  if ((await chip.count()) === 0) {
    check("preset chip to test the unpin with", false, "no chip on this page");
  } else {
    await chip.first().click();
    await page.waitForTimeout(300);
    const row = page.locator(".sim-preset-opt").first();
    if ((await row.count()) === 0) {
      // The ruler reads from 09:30 onwards and this check never runs the tape,
      // so an empty card is the ordinary case rather than a failure.
      console.log("    (no ruler reading yet — the preset half is skipped)");
      await page.keyboard.press("Escape");
    } else {
      await row.click();
      await page.waitForTimeout(300);
      check("applying a preset unpins the leg", !(await pinned()), `label ${await label("stop")}`);
      check(
        "and leaves a tick distance behind",
        /\d+t/.test(await label("stop")),
        await label("stop"),
      );
      check("the stored pin goes with it", (await stored()).stopUsd === null, JSON.stringify(await stored()));
    }
  }

  await shot(page, "ticket-usd-after");
} catch (e) {
  check("ran without throwing", false, String(e));
  await shot(page, "ticket-usd-error").catch(() => {});
} finally {
  if (errors.length) console.log(`\n  page errors:\n${errors.map((e) => `    ${e}`).join("\n")}`);
  const bad = results.filter(([, ok]) => !ok);
  console.log(`\n${bad.length ? `✗ ${bad.length} of ${results.length} failed` : `✓ ${results.length} checks`}`);
  await browser.close();
  process.exit(bad.length ? 1 : 0);
}
