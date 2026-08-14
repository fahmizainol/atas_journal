// The split pane, driven the way a user drives it.
//
// Turning it on is a button in the top bar, so this presses that button rather
// than a query param — the thing being checked is that the pane appears, draws a
// *different* bucketing from the trading pane, resizes, survives a reload, and
// keeps its own indicator preferences instead of the trading pane's.
//
// RUN IT AGAINST BOTH SERVERS. The dev server is the one with StrictMode, whose
// mount/unmount/remount is what catches a chart holding refs to series that died
// with a previous chart — a crash a production run cannot see. The production
// build is the one whose *timings* mean anything. The first version of this file
// only ever ran against production and passed a page that crashed on click.
//
//   node panecheck.mjs                              # dev server, :5173
//   APP_URL=http://localhost:4300 node panecheck.mjs # a production build
//   node panecheck.mjs --headed
import { launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const paneCount = () => page.locator(".sim-pane").count();

/** How many orders the page says are working. The rail badge carries the count
 *  and is drawn whether the rail is open or shut, so reading it needs no UI
 *  fiddling — and it is the page's own state rather than a guess off pixels. */
const workingCount = () =>
  page.evaluate(() => Number(document.querySelector(".sim-rail-badge")?.textContent?.trim()) || 0);

/**
 * Space + click a price on one pane.
 *
 * Two things this has to get right, both learned the hard way. Space is only the
 * placing modifier **while the pointer is over the chart** — pressing it first
 * would just re-trigger whatever button had focus — so the pointer moves in
 * before the key goes down. And the click has to land in the **price area**: a
 * y in the volume or CVD sub-pane has no price behind it and the handler
 * correctly does nothing, which reads exactly like a broken gesture.
 *
 * Hence the default point: right of centre and above the sub-panes. The
 * indicator legend is a DOM overlay at the top-LEFT of each pane and it starts
 * open on pane 0, so on a narrow pane it covers the middle — a click there hits
 * the legend, not the chart, and pane 0 alone appears unable to place.
 */
const spaceClick = async (pane, fx = 0.78, fy = 0.45) => {
  const box = await page.locator(`.sim-pane[data-pane="${pane}"]`).boundingBox();
  const x = box.x + box.width * fx;
  const y = box.y + box.height * fy;
  // Two moves, not one. `mouse.move` to the coordinates the pointer is already
  // at generates no event at all, so a pane that happens to sit under wherever
  // the last click left the pointer would never see the pointerenter that makes
  // Space its modifier — which reads as "this pane cannot place orders".
  await page.mouse.move(x, y - 40);
  await page.mouse.move(x, y);
  await page.waitForTimeout(250);
  await page.keyboard.down("Space");
  await page.waitForTimeout(150);
  await page.mouse.click(x, y);
  await page.keyboard.up("Space");
  await page.waitForTimeout(900);
  return workingCount();
};
/** The x-axis labels a pane is showing, which is how two bucketings tell
 *  themselves apart: a 1m pane and a 5m pane over one tape never label the same
 *  span of clock in the same places. */
const axisOf = (i) =>
  page.evaluate((idx) => {
    const pane = document.querySelectorAll(".sim-pane")[idx];
    if (!pane) return null;
    const c = [...pane.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return null;
    const ctx = c.getContext("2d");
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let ink = 0;
    // A sampled ink count says "is anything drawn here". It is far too coarse to
    // say "did this repaint" — a 1x replay moves the forming bar by a tick, and
    // every 37th pixel can easily be identical either side of that. So carry a
    // hash over every pixel as well, which changes if any of them did.
    let hash = 0;
    for (let i = 4; i < d.length; i += 4 * 37) {
      if (d[i] !== d[0] || d[i + 1] !== d[1] || d[i + 2] !== d[2]) ink++;
    }
    for (let i = 0; i < d.length; i += 4) {
      hash = (hash * 31 + d[i] + d[i + 1] * 3 + d[i + 2] * 7) | 0;
    }
    return { ink, hash, w: Math.round(c.getBoundingClientRect().width) };
  }, i);

// The discipline layer is configured per instance, and this machine's levels
// refuse every entry on NQ — a 100-tick stop floor against a $250 risk ceiling
// at $5 a tick has no size that satisfies both. That is a real thing to look at
// (see the note in the run output), but it is not what this file is checking, so
// the levels are stubbed **in the browser only**. Nothing is written to the
// user's settings, and the refusal path itself is exercised by the live checks.
// The replay account (phase 10) refuses a *new sitting* within an hour of the
// last one — which is the feature working, and which would make every order
// assertion below fail for an hour after anybody, including another check,
// traded a replay. Answered with a clean live account so this file goes on
// testing panes rather than testing the clock. Browser-only, like the levels
// below; the account's own rules are covered by tests/test_replay_account.py
// and by accountcheck.mjs, which drives it through its four states on purpose.
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
    },
  }),
);

await page.route("**/live/routing", async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  body.guards = {
    ...body.guards,
    stop_ticks_min: 20,
    stop_ticks_max: 400,
    min_target_ticks: 20,
    max_risk_usd: 100_000,
  };
  await route.fulfill({ response: res, json: body });
});

try {
  // Start from a known state: no stored pane preference of any kind.
  await openChart(page, "/charts/replay");
  await page.evaluate(() => {
    localStorage.removeItem("sim.prefs");
    for (const i of [1, 2, 3]) {
      localStorage.removeItem(`chart.indicatorVisibility.p${i}`);
      localStorage.removeItem(`chart.legendOpen.p${i}`);
    }
  });
  await openChart(page, "/charts/replay");

  check("starts as one pane", (await paneCount()) === 1, `${await paneCount()} pane(s)`);

  /** Pick a layout by the title its button carries (see lib/paneLayout). */
  const pickLayout = async (title) => {
    await page.locator(".chart-layout-btn").click();
    await page.locator(`.chart-layout-menu button[title="${title}"]`).click();
    await page.waitForTimeout(2500);
  };

  await pickLayout("Two side by side");
  check("the picker adds a pane", (await paneCount()) === 2, `${await paneCount()} pane(s)`);

  const a = await axisOf(0);
  const b = await axisOf(1);
  check("both panes drew", !!a && !!b && a.ink > 300 && b.ink > 300, `ink ${a?.ink} / ${b?.ink}`);
  check(
    "the trading pane keeps the larger share",
    !!a && !!b && a.w > b.w,
    `${a?.w}px vs ${b?.w}px`,
  );

  // The top bar's timeframe picker, acting on whichever pane the pointer last
  // touched. There is no per-pane picker any more: the bar reaches every pane
  // now, and a second copy of the control on every canvas was chart pixels spent
  // on a second place for the same answer to live.
  const topTf = page.locator(".chart-topbar .radio-group");
  /** Press a pane. Focus is claimed by a **press**, not by the pointer arriving:
   *  chrome that re-aims itself at whatever the pointer brushed past on its way
   *  somewhere else is chrome you stop trusting. (Hovering still lets a pane
   *  answer keys — that is a separate election, see lib/chartFocus.)
   *
   *  Right of centre for the same reason spaceClick is: the legend is a DOM
   *  overlay at the top-LEFT and starts open on pane 0, so on a narrow pane a
   *  point at 0.5 lands on it — and the *crosshair* then never moves, which made
   *  the link look broken when it was the aim that was wrong. Two moves, again
   *  because a move to where the pointer already is fires nothing. */
  const focusPane = async (i, fx = 0.78, fy = 0.35) => {
    const box = await page.locator(`.sim-pane[data-pane="${i}"]`).boundingBox();
    const x = box.x + box.width * fx;
    const y = box.y + box.height * fy;
    await page.mouse.move(x, y - 30);
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.up();
    await page.waitForTimeout(300);
  };
  const focusNote = () => page.locator(".chart-focus-note").textContent();

  // Hovering must NOT focus — that is the behaviour that was taken out.
  const hoverBox = await page.locator('.sim-pane[data-pane="1"]').boundingBox();
  await page.mouse.move(hoverBox.x + hoverBox.width * 0.78, hoverBox.y + hoverBox.height * 0.3);
  await page.mouse.move(hoverBox.x + hoverBox.width * 0.78, hoverBox.y + hoverBox.height * 0.35);
  await page.waitForTimeout(400);
  check(
    "hovering a pane does not focus it",
    (await page.locator(".chart-focus-note").textContent())?.includes("1"),
    `bar says "${(await page.locator(".chart-focus-note").textContent())?.trim()}"`,
  );

  await focusPane(1);
  check("pressing a pane focuses it", (await focusNote())?.includes("2"), `bar says "${(await focusNote())?.trim()}"`);
  check(
    "the focus ring is on that pane and no other",
    (await page.locator(".sim-pane.focused").count()) === 1 &&
      (await page.locator('.sim-pane[data-pane="1"].focused').count()) === 1,
    `${await page.locator(".sim-pane.focused").count()} ring(s)`,
  );
  const pressed = await topTf.locator("button.active").textContent();
  check("the bar shows that pane's bucketing", pressed?.trim() === "5m", `showing ${pressed?.trim()}`);
  await topTf.locator('button:text-is("15m")').click();
  await page.waitForTimeout(1500);
  const after = await topTf.locator("button.active").textContent();
  check("it re-buckets that pane", after?.trim() === "15m", `showing ${after?.trim()}`);

  // …and pane 0 kept its own. The bar acting on the focused pane is only useful
  // if it did not quietly re-bucket the page's own chart on the way.
  await focusPane(0);
  const back = await topTf.locator("button.active").textContent();
  check(
    "pane 0 kept its own bucketing",
    back?.trim() === "1m" && (await focusNote())?.includes("1"),
    `showing ${back?.trim()}, bar says "${(await focusNote())?.trim()}"`,
  );

  // The legend's own bucketing picker: the label that says which bar this pane
  // draws, made into the control. It aims at its own pane by construction, which
  // is the point — re-bucketing a pane is one gesture, not focus-then-pick.
  const legendTf = page.locator(".chart-legend-tf");
  check("each pane's legend carries a bucketing picker", (await legendTf.count()) === 2,
    `${await legendTf.count()} pickers: ${(await legendTf.allTextContents()).join(", ")}`);
  await legendTf.nth(1).click();
  await page.waitForTimeout(400);
  await page.locator('.chart-legend-tfpop button:text-is("1h")').click();
  await page.waitForTimeout(1800);
  const tfLabels = await legendTf.allTextContents();
  check(
    "it re-buckets its own pane and no other",
    tfLabels[0].startsWith("1m") && tfLabels[1].startsWith("1h"),
    tfLabels.join(" / "),
  );
  // Put it back, so the reload check below still expects 15m.
  await legendTf.nth(1).click();
  await page.waitForTimeout(400);
  await page.locator('.chart-legend-tfpop button:text-is("15m")').click();
  await page.waitForTimeout(1800);

  // The divider.
  const div = await page.locator(".sim-pane-divider").boundingBox();
  const box = await page.locator(".sim-chart").boundingBox();
  await page.mouse.move(div.x + div.width / 2, div.y + div.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.35, div.y + div.height / 2, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const a2 = await axisOf(0);
  check("the divider resizes", a2.w < a.w, `${a.w}px → ${a2.w}px`);

  // Both choices are settings, so they survive a reload.
  await openChart(page, "/charts/replay");
  const a3 = await axisOf(0);
  await focusPane(1);
  const kept = await topTf.locator("button.active").textContent();
  check(
    "pane, size and bucketing all survive a reload",
    (await paneCount()) === 2 && Math.abs(a3.w - a2.w) < 30 && kept?.trim() === "15m",
    `${await paneCount()} panes, ${a3.w}px, ${kept?.trim()}`,
  );

  // Per-pane indicator preferences: hiding a layer on the context pane must not
  // hide it on the trading pane.
  const stores = await page.evaluate(() => ({
    shared: localStorage.getItem("chart.indicatorVisibility"),
    pane: localStorage.getItem("chart.indicatorVisibility.b"),
  }));
  check(
    "the context pane has not written over the shared indicator prefs",
    stores.pane === null || stores.shared !== stores.pane,
    stores.pane === null ? "no pane blob written yet" : "separate blobs",
  );

  // One order dock on the page, and it belongs to pane 0.
  const docks = await page.evaluate(
    () => document.querySelectorAll(".sim-pane .sim-quick-btn").length,
  );
  const elsewhere = await page.evaluate(
    () => document.querySelectorAll('.sim-pane:not([data-pane="0"]) .sim-quick-btn').length,
  );
  check(
    "the order dock is on pane 0 only",
    docks > 0 && elsewhere === 0,
    `${docks} buttons, ${elsewhere} outside pane 0`,
  );

  // The gate, at 1× — the case that matters and the one the first version of
  // this check missed by playing at 30×. A 15m pane closes a bar every fifteen
  // minutes of session time, so if the pane only repainted on bar close it would
  // be visibly frozen here for a quarter of an hour of wall clock. Two seconds
  // is far too short to close one and far longer than the 200ms repaint floor,
  // so this passes only if the floor is doing its job.
  await page.selectOption(".sim-transport select", "1");
  const before = await axisOf(1);
  await page.keyboard.press("k");
  await page.waitForTimeout(3000);
  await page.keyboard.press("k");
  const after2 = await axisOf(1);
  check(
    "the pane repaints at 1x without waiting for a bar close",
    before.hash !== after2.hash,
    `hash ${before.hash} → ${after2.hash}`,
  );

  await shot(page, "panecheck");

  // Every layout draws the panes it claims and the dividers that go with them —
  // the geometry itself is checked without a browser by tools/layoutcheck.mjs;
  // what this adds is that a real chart mounts in each one.
  for (const [title, panes, dividers] of [
    ["Two stacked", 2, 1],
    ["One left, two stacked right", 3, 2],
    ["One on top, two below", 3, 2],
    ["Two by two", 4, 2],
  ]) {
    await pickLayout(title);
    const n = await paneCount();
    const d = await page.locator(".sim-pane-divider").count();
    const drawn = await page.evaluate(
      () => [...document.querySelectorAll(".sim-pane")].every((p) => p.querySelector("canvas")),
    );
    check(`${title.toLowerCase()} draws ${panes} panes`, n === panes && d === dividers && drawn,
      `${n} panes, ${d} dividers, ${drawn ? "all drew" : "a pane is blank"}`);
  }

  // Every pane places. This is the property the old split deliberately did not
  // have — pane 1 was read-only by construction, handed no order callbacks at
  // all — so it is the one worth asserting rather than assuming: each pane in
  // turn must add exactly one to the working count.
  await pickLayout("Two by two");
  // Two bits of housekeeping before the gesture, both of them app behaviour
  // this check would otherwise trip over.
  //
  // Drop focus: the speed <select> above still has it, and the chart
  // deliberately ignores Space while a form control is focused — Space is that
  // control's own key.
  await page.evaluate(() => document.activeElement?.blur?.());
  // And make sure the replay is actually paused. The `k` that was meant to
  // pause it went to the focused <select> for the same reason, so the tape may
  // still be running — and a resting order placed a tick off a moving mark
  // fills immediately, which reads as "the pane placed nothing".
  const transport = () => page.locator(".sim-transport button").first().textContent();
  if ((await transport())?.includes("Pause")) {
    await page.keyboard.press("k");
    await page.waitForTimeout(400);
  }
  check("the replay is paused before the order checks", !(await transport())?.includes("Pause"),
    `transport says "${(await transport())?.trim()}"`);

  // --- the link (lib/paneLink) --------------------------------------------
  // Checked with the tape stopped, which is the only way any of it means
  // anything: a running replay repaints every pane on its own, so "pane 1
  // changed" would be true whatever pane 0 did.
  //
  // The crosshair first. Pane 1's OHLC readout is a DOM overlay, so a pane that
  // followed the crosshair prints a bar's numbers without a pointer on it.
  const readout = (i) =>
    page.evaluate((idx) => {
      const el = document.querySelector(`.sim-pane[data-pane="${idx}"] .chart-ohlc`);
      if (!el) return null;
      return getComputedStyle(el).display === "none" ? "" : el.textContent.trim();
    }, i);
  await focusPane(0);
  check(
    "the crosshair reaches the other panes",
    (await readout(1))?.startsWith("O") && (await readout(3))?.startsWith("O"),
    `pane 1: "${(await readout(1))?.slice(0, 22)}…"`,
  );

  /** Pan a pane by dragging its tape sideways. */
  const dragPane = async (pane, dx) => {
    const box = await page.locator(`.sim-pane[data-pane="${pane}"]`).boundingBox();
    const x = box.x + box.width * 0.6;
    const y = box.y + box.height * 0.35;
    await page.mouse.move(x - 6, y);
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y, { steps: 12 });
    await page.mouse.up();
    await page.waitForTimeout(700);
  };
  const before1 = await axisOf(1);
  await dragPane(0, 160);
  const after1 = await axisOf(1);
  check(
    "scrolling one pane moves the others",
    before1.hash !== after1.hash,
    `pane 1 hash ${before1.hash} → ${after1.hash}`,
  );

  // And the switch really is a switch. Off, a pan is one pane's business.
  await page.locator(".chart-topbar-btn.link").click();
  await page.waitForTimeout(300);
  const before2 = await axisOf(1);
  await dragPane(0, -160);
  const after2b = await axisOf(1);
  check(
    "unlinked, it does not",
    before2.hash === after2b.hash,
    `pane 1 hash ${before2.hash} → ${after2b.hash}`,
  );
  await page.locator(".chart-topbar-btn.link").click();
  await page.waitForTimeout(300);

  // --- the tool rail (components/charts/ChartToolRail) ---------------------
  // One rail for four charts, outside every canvas, acting on the focused pane.
  const railBtns = () =>
    page.locator(".chart-rail .chart-tool").evaluateAll((els) =>
      els.map((e) => e.getAttribute("aria-label")),
    );
  /** Which *tool* is lit. The pin at the foot is lit whenever the rail is pinned
   *  — that is its state, not an armed tool — so it is excluded by name rather
   *  than by counting everything with `.on`. */
  const railArmed = () =>
    page.locator(".chart-rail .chart-tool.on").evaluateAll((els) =>
      els
        .map((e) => e.getAttribute("aria-label"))
        .filter((l) => l !== "Pin the rail" && l !== "Unpin the rail"),
    );
  check(
    "one rail on the page, none left inside a canvas",
    (await page.locator(".chart-rail").count()) === 1 &&
      (await page.evaluate(
        () =>
          [...document.querySelectorAll(".chart-tools")].filter(
            (e) => getComputedStyle(e).display !== "none",
          ).length,
      )) === 0,
    `${(await railBtns()).length} buttons: ${(await railBtns()).join(", ")}`,
  );
  // ＋Order is on the rail on a desktop. In the canvas it was touch-only, on the
  // grounds that Space+click is strictly the better mouse gesture — which is
  // true, and is also why nobody with a mouse ever found the tool.
  check("the rail offers ＋Order with a mouse", (await railBtns()).includes("＋ Order"));

  await focusPane(2);
  await page.locator('.chart-rail .chart-tool[aria-label="Measure"]').click();
  await page.waitForTimeout(400);
  check("arming from the rail lights it", (await railArmed()).join() === "Measuring…", `lit: ${(await railArmed()).join()}`);
  // The rail reads the focused pane, so pointing at a pane that has nothing
  // armed must show nothing armed — not the last pane's state.
  await focusPane(0);
  check(
    "the rail follows focus rather than remembering",
    (await railArmed()).join() === "Cursor",
    `lit on pane 1: ${(await railArmed()).join()}`,
  );
  // The pin. Unpinned the rail floats over the tape and the grid gets its 38px
  // back — which is the only assertion that matters here, since "you can see the
  // chart behind it" is a fact about width, not about opacity.
  const gridW = () =>
    page.evaluate(() => Math.round(document.querySelector(".sim-chart").getBoundingClientRect().width));
  const wPinned = await gridW();
  await page.locator('.chart-rail .chart-tool[aria-label="Unpin the rail"]').click();
  await page.waitForTimeout(600);
  const wFloat = await gridW();
  check(
    "unpinning the rail gives its column back to the charts",
    wFloat > wPinned && (await page.locator(".chart-rail.floating").count()) === 1,
    `${wPinned}px → ${wFloat}px`,
  );
  await page.locator('.chart-rail .chart-tool[aria-label="Pin the rail"]').click();
  await page.waitForTimeout(600);
  check("…and pinning takes it back", (await gridW()) === wPinned, `${await gridW()}px`);

  await focusPane(2);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
  check("Esc on the pane disarms the rail", (await railArmed()).join() === "Cursor",
    `lit: ${(await railArmed()).join()}`);

  const placed = [];
  for (const pane of [0, 1, 2, 3]) placed.push(await spaceClick(pane));
  check(
    "every pane can rest an order",
    placed.join() === "1,2,3,4",
    `working after each pane: ${placed.join(" → ")}`,
  );

  await pickLayout("One chart");
  check("the picker takes them away again", (await paneCount()) === 1, `${await paneCount()} pane(s)`);
  check("no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  await browser.close();
  const bad = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - bad}/${results.length} passed`);
  process.exitCode = bad ? 1 : 0;
}
