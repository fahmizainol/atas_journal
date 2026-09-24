// The bracket presets, in a real browser.
//
// The arithmetic and the ruler under it are pinned by tests/test_order_presets.py
// against a fixture generated from the real libs, so none of that is re-checked
// here. What no pytest can see is the wiring: that the chip appears on the
// floating ticket at all, that it carries a reading through from a ruler pane
// nobody opened, that the card lists every shape with the distances they
// resolve to — and that clicking one lands all five of them on the ticket,
// including the three trail fields that live on the page rather than on the
// ticket component.
//
// And that the lit row is *derived*, not remembered. The card marks the preset
// whose six numbers equal the ticket's, so applying one lights it and nudging a
// knob by hand puts it out. That distinction only exists at runtime: a stored
// letter and a derived one look identical until the moment they disagree, which
// is the moment the highlight would start lying about what is on the ticket.
//
//   node presetcheck.mjs
//   node presetcheck.mjs --headed
//
// **It clicks a preset, unlike sizercheck.** That file's rule is about the risk
// sizer specifically: applying a cell can throw the mini/micro switch, which is
// an account decision. A preset moves five distances and nothing else, the
// context is a throwaway Playwright profile (its own localStorage, so the user's
// stored prefs are untouched), and no order is sent — and the trail half of the
// apply path is exactly the half that is worth seeing land.
import { healthyAccount, launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });

// A live, healthy account with nothing owed — accountcheck's `healthy()`, which
// is where the shape is documented. Stubbed for one reason: a session that owes
// a review opens *in* the review, and the ticket panel is deliberately not
// rendered there, so the panel copy of the list would be untestable on any
// machine that happens to owe one. Nothing is written; the account's own rules
// are covered by tests/test_replay_account.py.
await page.route("**/api/replays**", async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname.endsWith("/replays/account")) {
    return route.fulfill({
      json: {
        ...healthyAccount(),
        account: url.searchParams.get("mode") === "paper" ? "paper" : "funded",
      },
    });
  }
  return route.fallback();
});
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};
const num = (s, re) => {
  const m = re.exec(s ?? "");
  return m ? Number(m[1]) : null;
};

try {
  await openChart(page, "/charts/replay");

  // Run the tape up to a reading before anything is checked.
  //
  // The replay opens at the bell and the presets' ruler reads 30-second bars
  // from **09:30 onwards**, with nothing behind it — so the first ninety seconds
  // of tape have to happen before there is a stop at all, and every check below
  // would otherwise be grading the empty state. `]` walks the speed ladder (it
  // tops out at 300x, so pressing it past the end is safe) and `k` plays. Then
  // pause, so the rest of this runs against a chart that is holding still.
  for (let i = 0; i < 8; i++) await page.keyboard.press("]");
  await page.keyboard.press("k");
  const chipReads = page.locator("button.sim-knob-rr.preset", { hasText: /\d+t/ });
  await chipReads.waitFor({ timeout: 60000 }).catch(() => {});
  await page.keyboard.press("k");
  await page.waitForTimeout(500);
  check("ruler reads once three bars have closed", (await chipReads.count()) > 0);

  // --- the chip on the floating ticket --------------------------------------
  const chip = page.locator("button.sim-knob-rr.preset");
  const hasChip = (await chip.count()) > 0;
  check("A–D chip on the ticket", hasChip);
  if (!hasChip) throw new Error("no preset chip — nothing further to check");

  const chipText = (await chip.first().innerText()).replace(/\s+/g, " ").trim();
  // It quotes the stop it would build from, so the ticket says what it is
  // working off without being opened. No reading yet is a legitimate state.
  check("chip quotes the stop", /^A–D( \d+t)?$/.test(chipText), JSON.stringify(chipText));

  // --- the panel ------------------------------------------------------------
  await chip.first().click();
  await page.waitForTimeout(400);
  const pop = page.locator(".sim-preset-pop");
  check("panel opens", (await pop.count()) > 0);

  const box = await pop.first().boundingBox();
  const vp = page.viewportSize();
  check(
    "panel is on screen",
    !!box && box.x >= 0 && box.y >= 0 && box.x + box.width <= vp.width + 1,
    box ? `x=${Math.round(box.x)} w=${Math.round(box.width)}` : "no box",
  );

  // Two numbers, and the panel has to show both: the stop it will place, and the
  // ruler reading plus the room it is made of (`LEG_ROOM`). Everything below is
  // derived from `reading`, because that is what the shapes are written in —
  // deriving from the placed stop would re-check the offset against itself.
  const readHead = async () => {
    const h = (await pop.locator(".sim-sizer-head").innerText()).replace(/\s+/g, " ").trim();
    return { head: h, stop: num(h, /⊥\s*(\d+)t/), reading: num(h, /(\d+)\+\d+\b/), room: num(h, /\d+\+(\d+)\b/) };
  };
  let { head, stop, reading, room } = await readHead();
  console.log(`    head: ${head}`);
  check(
    "panel shows the reading and the room behind the stop",
    stop != null && reading != null && room != null && stop === reading + room,
    `⊥${stop} = ${reading}+${room}`,
  );

  // --- the bucketing toggle -------------------------------------------------
  // Three bars the stop can be read at (lib/volRuler `PRESET_BUCKETS`), and the
  // arithmetic under them is pinned by pytest — what only a browser can say is
  // that the control is wired to the header at all, that each option carries its
  // own reading rather than only a name, and that picking one is remembered.
  const bucketBtns = pop.locator(".sim-preset-bucket");
  const nBuckets = await bucketBtns.count();
  check("three bucketings offered", nBuckets === 3, `${nBuckets}`);
  const bucketText = (await bucketBtns.allInnerTexts()).map((x) => x.replace(/\s+/g, " ").trim());
  console.log(`    buckets: ${bucketText.join(" | ")}`);
  check(
    "every bucketing quotes its own reading",
    bucketText.length === 3 && bucketText.every((x) => /^(500t|30s|15s) (\d+t|—)$/.test(x)),
    JSON.stringify(bucketText),
  );
  // Exactly one selected, and it is the one the header is reading. `aria-pressed`
  // rather than the class, because that is the half a screen reader gets.
  const pressed = await bucketBtns.evaluateAll((els) =>
    els.filter((e) => e.getAttribute("aria-pressed") === "true").map((e) => e.textContent),
  );
  check("exactly one bucketing is selected", pressed.length === 1, JSON.stringify(pressed));
  // The reading is the *last* number in the cell: "500t 57t" has a label that is
  // itself a tick count, and a leading match would compare the bar to the stop.
  const bucketReading = (txt) => num((txt ?? "").trim(), /(\d+)t$/);
  const selReading = bucketReading(pressed[0]);
  check("the header reads the selected bucketing", selReading === reading, `${selReading} vs ${reading}`);

  // Switch to one whose reading differs, and the header has to follow. Picking
  // the *differing* one on purpose: a toggle that re-rendered nothing would pass
  // a same-value comparison, which is the failure worth catching.
  const others = [];
  for (let i = 0; i < nBuckets; i++) {
    if ((await bucketBtns.nth(i).getAttribute("aria-pressed")) === "true") continue;
    const r = bucketReading(bucketText[i]);
    if (r != null && r !== reading) others.push({ i, r, label: bucketText[i] });
  }
  if (!others.length) {
    check("a bucketing with a different reading to switch to", false, JSON.stringify(bucketText));
  } else {
    const pick = others[0];
    await bucketBtns.nth(pick.i).click();
    await page.waitForTimeout(300);
    const after = await readHead();
    check("switching the bucketing moves the header", after.reading === pick.r, `${after.reading} vs ${pick.r}`);
    check("and the stop follows it", after.stop === pick.r + room, `⊥${after.stop} vs ${pick.r}+${room}`);
    const rowsNow = (await pop.locator(".sim-preset-opt").allInnerTexts()).map((x) => x.replace(/\s+/g, " "));
    check(
      "the rows re-derive off the new stop",
      rowsNow.length === 4 && rowsNow.every((r) => new RegExp(`⊥${after.stop}\\b`).test(r)),
      `⊥${after.stop}`,
    );
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("sim.prefs") ?? "{}").presetBucket);
    check("the choice is stored", typeof stored === "string" && pick.label.startsWith(
      { t500: "500t", s30: "30s", s15: "15s" }[stored] ?? "\u0000",
    ), `${stored} for ${pick.label}`);
    // Back to where it started, so everything below applies the default bar and
    // this file leaves the profile as it found it.
    const back = pressed[0].trim().split(/\s+/)[0];
    await pop.locator(".sim-preset-bucket", { hasText: back }).first().click();
    await page.waitForTimeout(300);
    ({ head, stop, reading, room } = await readHead());
    check("switching back restores the reading", reading === selReading, `${reading} vs ${selReading}`);
  }

  if ((await pop.locator(".sim-sizer-empty").count()) > 0) {
    // Before three bars have closed this is the honest state and there is
    // nothing behind it to fall back to — but the tape was run on above
    // precisely so the rows could be checked, so reaching here means the ruler
    // never started, and saying so beats a green tick over nothing.
    check("ruler had a reading", false, "panel says the ruler is not ready yet");
    throw new Error("no reading, so nothing to apply");
  }

  const rows = pop.locator(".sim-preset-opt");
  const nRows = await rows.count();
  check("four preset rows", nRows === 4, `${nRows}`);
  const rowText = (await rows.allInnerTexts()).map((s) => s.replace(/\s+/g, " ").trim());
  for (const r of rowText) console.log(`    ${r}`);

  // Every row spells out the shape it would apply, off the one stop in the head.
  check(
    "every row quotes the head's stop",
    stop != null && rowText.every((r) => new RegExp(`⊥${stop}\\b`).test(r)),
    `stop ${stop}`,
  );
  check(
    "A is the one with no target",
    /^A\b/.test(rowText[0]) && /no ⊤/.test(rowText[0]),
    rowText[0],
  );
  // C and D are the same trade priced two ways and the difference between them
  // is a *missing* thing — no rung behind either target. The row draws the
  // ladder segment only when there is one, so the absence of the `·` is the
  // rendered form of that, and it is the failure worth catching here: a C that
  // kept B's breakeven would look right (same stop, a nearer target) while
  // quietly scratching the losers this shape exists to take in full.
  check(
    "C is 1.33R with nothing behind it",
    /^C\b/.test(rowText[2])
      && new RegExp(`⊤${Math.floor(reading * (4 / 3) + 0.5) + room}\\b`).test(rowText[2])
      && !rowText[2].includes("·"),
    rowText[2],
  );
  check(
    "D is 1R with nothing behind it",
    /^D\b/.test(rowText[3]) && new RegExp(`⊤${stop}\\b`).test(rowText[3]) && !rowText[3].includes("·"),
    rowText[3],
  );

  await shot(page, "preset-open");

  // --- applying one ---------------------------------------------------------
  // B: a 1.5R target and a breakeven-only ladder at one stop's distance. Chosen
  // because it sets all five legs to distinct values — A leaves the target at
  // zero, which a broken apply could also produce by doing nothing.
  await rows.nth(1).click();
  await page.waitForTimeout(400);
  check("panel closes on apply", (await page.locator(".sim-preset-pop").count()) === 0);

  const knob = async (cls) =>
    (await page.locator(`.sim-knob.${cls} .sim-knob-v`).first().innerText()).replace(/\s+/g, " ");
  const gotStop = num(await knob("stop"), /⊥\s*(\d+)t/);
  const gotTarget = num(await knob("target"), /⊤\s*(\d+)t/);
  const wantTarget = Math.floor(reading * 1.5 + 0.5) + room;
  check("stop on the ticket is the reading plus the room", gotStop === stop, `⊥ ${gotStop} vs ${stop}`);
  check("target is 1.5R plus the room", gotTarget === wantTarget, `⊤ ${gotTarget} vs ${wantTarget}`);

  // The three trail fields are the page's, not the ticket component's — this is
  // the half of the apply path only a browser can see. Read off the page's own
  // carried settings rather than off the panel's inputs, because the panel is
  // not always there to read: a session that owes a review opens straight into
  // review mode and the ticket is deliberately replaced (a BUY/SELL pad that
  // refuses every press is the shape of a broken page). The store is the same
  // state either way — `sim.prefs` is written from it on every change — and this
  // context's localStorage is Playwright's own, not the user's.
  const prefs = await page.evaluate(() => JSON.parse(localStorage.getItem("sim.prefs") ?? "{}"));
  console.log(
    `    ticket: ⊥${prefs.stopTicks} ⊤${prefs.targetTicks} ` +
      `trail ${prefs.trailTicks}/${prefs.trailStepTicks}/${prefs.trailBeTicks}` +
      `${prefs.trailBeOnly ? " BE-only" : ""}`,
  );
  check("the ladder is on", prefs.trailTicks > 0, `${prefs.trailTicks}`);
  check("back is one stop", prefs.trailTicks === stop, `${prefs.trailTicks} vs ${stop}`);
  check("step is 0 — one rung per trail distance", prefs.trailStepTicks === 0, `${prefs.trailStepTicks}`);
  check("BE is 3 ticks past the fill", prefs.trailBeTicks === 3, `${prefs.trailBeTicks}`);
  check("BE only — the first rung and no other", prefs.trailBeOnly === true);
  // The two legs again, from the store rather than from the dock: the knobs and
  // the page have been two sources of truth before (see TicketKnobs' header).
  check("the store agrees with the dock", prefs.stopTicks === gotStop && prefs.targetTicks === gotTarget);

  // --- the lit row ----------------------------------------------------------
  // B is on the ticket now, so B is the row that should be lit — and only B.
  await chip.first().click();
  await page.waitForTimeout(400);
  const lit = async () => {
    const on = pop.locator(".sim-preset-opt.on");
    const n = await on.count();
    return { n, id: n ? (await on.first().innerText()).replace(/\s+/g, " ").trim() : "" };
  };
  const litB = await lit();
  check("the applied preset is lit, and only it", litB.n === 1 && /^B\b/.test(litB.id), `${litB.n}: ${litB.id}`);
  await pop.locator("button[aria-label='Close']").first().click();
  await page.waitForTimeout(250);

  // Now move the target one step by hand. Nothing about the card changed — the
  // ticket did — and the highlight has to notice, because what it claims is
  // "this is what you are about to send" and that is no longer true of any row.
  // The `+` on the target knob. Last of the two, since the label carries the
  // knob's own value ("⊤ 91t up") and matching on that would re-derive it here.
  await page.locator(".sim-knob.target button").last().click();
  await page.waitForTimeout(300);
  await chip.first().click();
  await page.waitForTimeout(400);
  const litAfter = await lit();
  const nudged = num(await knob("target"), /⊤\s*(\d+)t/);
  check(
    "nudging a knob puts the highlight out",
    litAfter.n === 0 && nudged !== gotTarget,
    `${litAfter.n} lit at ⊤${nudged} (was ${gotTarget})`,
  );
  // Put it back, so the surfaces below are graded against B's real shape and the
  // profile is left as B left it.
  await pop.locator(".sim-preset-opt").nth(1).click();
  await page.waitForTimeout(400);

  // --- the same list in the panel beside the blotter ------------------------
  // The dock is one of two surfaces; the other is the ticket in the panel, where
  // the fields a preset fills are sitting. Same component, so what is worth
  // checking here is that it is actually rendered there, fits the column, and
  // applies from there too.
  await page.locator("button.chart-topbar-btn", { hasText: "▤▎" }).first().click();
  await page.waitForTimeout(500);
  const ticket = page.locator(".sim-ticket");
  const inPanel = (await ticket.count()) > 0;
  check("the ticket panel is up", inPanel);
  if (inPanel) {
    const block = ticket.locator(".sim-preset-block");
    check("preset list is in the panel ticket", (await block.count()) > 0);
    const panelBox = await ticket.first().boundingBox();
    const blockBox = await block.first().boundingBox();
    check(
      "it fits the panel's column",
      !!panelBox && !!blockBox && blockBox.x >= panelBox.x - 1
        && blockBox.x + blockBox.width <= panelBox.x + panelBox.width + 1,
      blockBox && panelBox
        ? `block ${Math.round(blockBox.width)}px in ${Math.round(panelBox.width)}px`
        : "no box",
    );
    const panelRows = block.locator(".sim-preset-opt");
    check("four rows in the panel too", (await panelRows.count()) === 4);
    await shot(page, "preset-panel");

    // A from the panel: no target, and a trail 5 ticks inside the stop. It is
    // the one preset whose shape cannot be reached by an apply that half-works.
    await panelRows.first().click();
    await page.waitForTimeout(400);
    const after = await page.evaluate(() => JSON.parse(localStorage.getItem("sim.prefs") ?? "{}"));
    // The target stays off through the offset — the case that would turn A into
    // a different preset if the room were added blind.
    check("A from the panel turns the target off", after.targetTicks === 0, `⊤${after.targetTicks}`);
    check("A from the panel trails inside the stop", after.trailTicks === stop - 5,
      `${after.trailTicks} vs ${stop - 5}`);
    check("A from the panel starts at the entry", after.trailBeTicks === 0 && after.trailBeOnly === false);
  }

  await shot(page, "preset-applied");
  // Reported the way every other check reports it, including the standing noise:
  // PostHog's optional bundles 404 against its CDN on this machine, so this line
  // is red here and in sizercheck alike, on unchanged code. Left rather than
  // filtered — a check that learns to ignore a class of error is a check that
  // ignores the real one when it arrives.
  check("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("ran to completion", false, String(e.message ?? e));
} finally {
  const bad = results.filter(([, ok]) => !ok);
  console.log(`\n${results.length - bad.length}/${results.length} ok`);
  await browser.close();
  process.exit(bad.length ? 1 : 0);
}
