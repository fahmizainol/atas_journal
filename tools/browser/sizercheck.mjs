// The ticket's risk sizer, in a real browser.
//
// The arithmetic is already pinned by tests/test_risk_sizer.py against a fixture
// generated from the real lib, so none of that is re-checked here. What no
// pytest can see is the half this file is for: that the size grid reaches the
// floating ticket at all, that it carries the presets' ruler reading through
// from a pane it does not depend on being open, that the card opens without
// falling off screen, and that the three rows render their two routes.
//
// It sizes off the **same ruler the A–D brackets do** — `PresetRuler` at the
// bucketing the toggle is on — which is why the tape has to be run forward
// before anything below is graded, exactly as presetcheck.mjs does it. That
// ruler reads from the 09:30 open with nothing behind it, so the empty state is
// a real answer and not a bug; it is just not the answer this file is about.
//
// **There is no Σ chip any more.** The sizer and the brackets are one card
// behind the one A–D chip (`TicketCard`) — they were two popovers each drawing
// their own copy of the bucketing toggle over the same reading — so this file
// opens that chip and checks the half below the rule. Which is also the check
// that the merge held: if the size grid is not in there, it is nowhere.
//
//   node sizercheck.mjs
//   node sizercheck.mjs --headed
//
// **It writes nothing, and it clicks no preset.** Applying a cell moves the real
// ticket — size, stop, and possibly the mini/micro switch — and those persist to
// localStorage as the user's own prefs. A check that quietly re-sized his ticket
// would be the same class of mistake as one that trades. The apply path is
// covered by types and by the pure-lib fixture; what is verified here is
// everything up to the click.
import { launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

try {
  await openChart(page, "/charts/replay");

  // Run the tape up to a reading before anything is checked. `]` walks the speed
  // ladder (it tops out at 300x, so pressing past the end is safe) and `k`
  // plays; the second `k` pauses, so the rest runs against a chart holding
  // still. Waiting on the chip's own text rather than a fixed sleep — three
  // 30-second bars is ninety seconds of tape and how long that takes in
  // wall-clock is the speed ladder's business, not ours.
  for (let i = 0; i < 8; i++) await page.keyboard.press("]");
  await page.keyboard.press("k");
  const chipReads = page.locator("button.sim-knob-rr.preset", { hasText: /\d+t/ });
  await chipReads.waitFor({ timeout: 60000 }).catch(() => {});
  await page.keyboard.press("k");
  await page.waitForTimeout(500);

  // --- the one chip on the floating ticket ---------------------------------
  const chip = page.locator("button.sim-knob-rr.preset");
  const hasChip = (await chip.count()) > 0;
  check("the card's chip on the ticket", hasChip);
  if (!hasChip) throw new Error("no ticket card chip — nothing further to check");

  // And no second one beside it. The Σ is gone rather than hidden: a leftover
  // chip opening a second panel with a second copy of the bucketing toggle is
  // precisely the state this merge removed, and it would look fine.
  check("no Σ chip left over", (await page.locator("button.sim-knob-rr.sizer").count()) === 0);

  const chipText = (await chip.first().innerText()).trim();
  const chipT = Number(chipText.match(/(\d+)t/)?.[1]);

  // --- the card -------------------------------------------------------------
  await chip.first().click();
  await page.waitForTimeout(400);
  const pop = page.locator(".sim-sizer-pop");
  check("card opens", (await pop.count()) > 0);

  // The two halves are one measurement, and this is where that is now visible:
  // the head quotes the stop the presets would *place*, which is the reading
  // plus `LEG_ROOM`, and the grid below sizes the reading itself. So the gap
  // between the chip's number and the head's `57+5` is fixed at 5, and any
  // other gap means the halves have drifted onto different rulers.
  const readT = Number(
    (await pop.locator(".sim-sizer-head").innerText().catch(() => "")).match(/(\d+)\+5/)?.[1],
  );
  check(
    "the card's two halves read one ruler",
    Number.isFinite(chipT) && Number.isFinite(readT) && chipT - readT === 5,
    `chip ${chipT}t · sizes ${readT}t`,
  );

  // It hangs off a box capped at 300px and is wider than it on purpose, so the
  // thing worth checking is that it is still on screen.
  const box = await pop.first().boundingBox();
  const vp = page.viewportSize();
  check(
    "panel is on screen",
    !!box && box.x >= 0 && box.y >= 0 && box.x + box.width <= vp.width + 1,
    box ? `x=${Math.round(box.x)} w=${Math.round(box.width)} h=${Math.round(box.height)}` : "no box",
  );

  const head = (await pop.locator(".sim-sizer-head").innerText()).replace(/\s+/g, " ").trim();
  console.log(`    head: ${head}`);

  // The size half is under its own rule, below the preset rows. Its presence is
  // the merge: this locator finding nothing means the card opened with brackets
  // and no sizer.
  check("the size half is in the card", (await pop.locator(".sim-card-sep").count()) === 1);
  check("and the brackets are above it", (await pop.locator(".sim-preset-opt").count()) === 4);

  const empty = await pop.locator(".sim-sizer-empty").count();
  if (empty) {
    // A legitimate state, not a failure — but it means the rows below cannot be
    // checked, and saying so beats a green tick over nothing.
    check("ruler had a reading", false, "panel says the ruler is not ready yet");
  } else {
    const labels = await pop.locator(".sim-sizer-lbl").allInnerTexts();
    check(
      "three appetite rows",
      labels.length === 3,
      labels.map((s) => s.replace(/\s+/g, "")).join(" "),
    );

    // Six cells: three rows x mini and micro. A cell is either a route button
    // or the dashed "—" that says nothing fits.
    const cells = pop.locator(".sim-sizer-cell");
    const nCells = await cells.count();
    check("six route cells", nCells === 6, `${nCells}`);

    const texts = await cells.allInnerTexts();
    const rows = [];
    for (let i = 0; i < texts.length; i += 2) {
      rows.push([texts[i], texts[i + 1]].map((s) => s.replace(/\s+/g, " ").trim()));
    }
    for (const [i, [mini, micro]] of rows.entries()) {
      console.log(`    ${(labels[i] ?? "").replace(/\s+/g, " ")}: mini "${mini}" | micro "${micro}"`);
    }

    // Every live cell quotes contracts and what being wrong costs. The dashes
    // are the honest empty, and are allowed.
    const priced = texts.filter((t) => /\$\d/.test(t));
    check("live cells quote money", priced.length > 0, `${priced.length}/6 priced`);

    // The invariant the panel exists to make visible: as the ruler widens the
    // budget stops reaching a whole mini while micros still fit, so a row with a
    // mini must always have a micro too. The reverse is the size-down signal.
    const bad = rows.filter(([mini, micro]) => /\$\d/.test(mini) && !/\$\d/.test(micro));
    check("no row offers a mini but no micro", bad.length === 0, `${bad.length} bad`);
  }

  await shot(page, "sizer-open");

  // Closing puts it away rather than leaving a panel over the tape.
  await pop.locator("button[aria-label='Close']").first().click();
  await page.waitForTimeout(300);
  check("card closes", (await page.locator(".sim-sizer-pop").count()) === 0);

  // --- the copy in the ticket panel ----------------------------------------
  // The same card, open rather than behind a chip, under the fields it fills.
  // Its own check because it is the copy that has no ✕ and no popover to escape
  // — if it renders at all it renders in flow — and because it is the narrow
  // one: the popover sets its own 320px, this takes whatever column the panel
  // gives it, which is where a money grid gets cramped if anywhere.
  //
  // It was on the *blotter* for a day, above the rows. That is gone: the card
  // belongs next to the fields it fills, not next to the record of what the
  // fields already did — so this also checks the blotter is a record again.
  // The dock is closed by default — it covers the tape — so summon it first.
  // `▤▎` on the top bar is the only switch it has.
  await page.locator("button[title='Show the ticket and blotter']").first().click();
  await page.waitForTimeout(500);
  check("nothing left on the blotter", (await page.locator(".sim-blotter-sizer").count()) === 0);

  const block = page.locator(".sim-ticket .sim-preset-block");
  const inPanel = (await block.count()) > 0;
  check("the card is in the ticket panel", inPanel);
  if (inPanel) {
    // The bucketing toggle comes with it, and exactly one of it: a card that
    // rendered its two halves as two panels would put two toggles on one setting
    // here, which is what the merge was for.
    check(
      "the ruler toggle came with it, once",
      (await block.locator(".sim-preset-bucket").count()) === 3,
      `${await block.locator(".sim-preset-buckets").count()} toggle(s)`,
    );
    // Both halves on this surface — the panel ticket used to get the brackets
    // alone, and the size that would carry them was a popover away.
    check("brackets in the panel", (await block.locator(".sim-preset-opt").count()) === 4);
    check("and the size grid", (await block.locator(".sim-sizer-grid").count()) === 1);
    // The narrow column is the risk here. Every cell has to stay inside it —
    // a money grid that overflows its panel is how "6 MNQ $258" becomes "6 MN".
    const pbox = await page.locator(".sim-ticket").first().boundingBox();
    const cells = page.locator(".sim-ticket .sim-sizer-cell");
    const boxes = [];
    for (let i = 0; i < (await cells.count()); i++) boxes.push(await cells.nth(i).boundingBox());
    const out = boxes.filter(
      (b) => b && pbox && (b.x < pbox.x - 1 || b.x + b.width > pbox.x + pbox.width + 1),
    );
    check(
      "the grid fits the panel's column",
      !!pbox && out.length === 0,
      pbox ? `${boxes.length} cells in ${Math.round(pbox.width)}px, ${out.length} over` : "no box",
    );
    // The ticket panel scrolls, and this card is under the fields — so a shot
    // taken where the panel happens to be sitting is a picture of the fields.
    await block.first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot(page, "sizer-panel");
  }

  check("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("ran to completion", false, String(e.message ?? e));
} finally {
  const bad = results.filter(([, ok]) => !ok);
  console.log(`\n${results.length - bad.length}/${results.length} ok`);
  await browser.close();
  process.exit(bad.length ? 1 : 0);
}
