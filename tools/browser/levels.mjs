// The near-price levels panel (LevelApproach): does it draw, does it stay out of
// the way of everything else in that gutter, and is it where it should be — which
// is the trading pane and nowhere else.
//
// The arithmetic behind the rows is not this file's job: tests/test_level_approach.py
// runs the TypeScript against the Python it was ported from. What can only be
// checked in a browser is placement (four other things live in the axis gutter,
// one of them draggable), the pane gate, and that a DOM overlay over a canvas
// perturbs nothing on the canvas.
//
// Run: node tools/browser/levels.mjs [--headed]
import { launch, openChart, probeChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const DAY = process.env.JOURNAL_DAY ?? "2026-06-30";

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** Play the tape far enough that the classification window has closed bars in
 *  it. Before that every row honestly reads "—", which is right but proves
 *  nothing about the reader. */
async function runIn(page, ms) {
  await page.keyboard.press("k").catch(() => {});
  await page.waitForTimeout(ms);
  await page.keyboard.press("k").catch(() => {});
  await page.waitForTimeout(300);
}

const boxes = (page, sel) =>
  page.$$eval(sel, (els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    }),
  );

const overlaps = (a, b) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

const { browser, page } = await launch({ headed });
try {
  await openChart(page, "/charts/replay");
  // A known state: the panel open, whatever a previous run left behind.
  await page.evaluate(() => localStorage.setItem("chart.levelPanel", "1"));
  await openChart(page, "/charts/replay");
  await runIn(page, 25_000);

  const rows = await page.locator(".chart-levels-row").count();
  ok("the panel draws rows", rows > 0, `${rows} rows`);

  const shown = await page.$$eval(".chart-levels-row", (els) =>
    els.map((e) => ({
      dist: e.querySelector(".chart-levels-dist")?.textContent ?? "",
      // The class no longer has a column of its own — arming took the width and
      // it moved to the name's hover, where the row's own reading is appended
      // after the per-member list. Read it from there.
      cls: e.querySelector(".chart-levels-name")?.getAttribute("title") ?? "",
      price: Number(e.querySelector(".chart-levels-dist")?.getAttribute("data-price")),
    })),
  );
  ok(
    "every row carries a live distance",
    shown.length > 0 && shown.every((r) => /^[+-]?\d+t$/.test(r.dist)),
    shown.map((r) => r.dist).join(" "),
  );
  // Price order, not distance order — the rows are rebuilt on bar close while the
  // distances repaint with the tape, so a distance ranking falls out of order the
  // moment price moves. See clusterLevels.
  ok(
    "rows read down the way the price scale does",
    shown.every((r, i) => i === 0 || shown[i - 1].price >= r.price),
    shown.map((r) => r.price).join(" > "),
  );
  ok(
    "the classification is being made, not just the box drawn",
    shown.some((r) => /\b(drift|price-led|level-led|both|mixed)\b/.test(r.cls)),
    shown.map((r) => /\n\n(.+?) —/.exec(r.cls)?.[1] ?? "—").join(" · "),
  );

  // --- the gutter is shared -------------------------------------------------
  const panel = (await boxes(page, ".chart-levels"))[0];
  for (const sel of [".chart-jump", ".chart-badges", ".chart-edge", ".chart-ohlc"]) {
    const others = await boxes(page, sel);
    ok(
      `clear of ${sel}`,
      others.every((o) => !overlaps(panel, o)),
      others.length ? "" : "(none on screen)",
    );
  }
  // The order pad claims the same corner family, parked and minimised both.
  for (const state of ["parked", "minimised"]) {
    if (state === "minimised") {
      await page.locator(".sim-quick button[title*='inimi']").first().click().catch(() => {});
      await page.waitForTimeout(400);
    }
    const pad = [...(await boxes(page, ".sim-quick")), ...(await boxes(page, ".sim-quick-badge"))];
    ok(
      `clear of the order pad (${state})`,
      pad.every((o) => !overlaps(panel, o)),
      pad.length ? "" : "(no pad on screen)",
    );
  }

  await shot(page, "levels-panel");

  // --- a DOM overlay may not touch the canvas -------------------------------
  const before = await probeChart(page);
  // By its label, not by position: the head gained a ⤢ reach toggle ahead of the
  // close button when arming arrived, and "the first button" silently became a
  // different control.
  await page.locator('.chart-levels-head button[aria-label="Hide the levels panel"]').click();
  await page.waitForTimeout(400);
  ok("collapses to a pill", (await page.locator(".chart-levels-pill").count()) === 1);
  const after = await probeChart(page);
  ok(
    "the canvas is untouched by the fold",
    JSON.stringify(before.silhouette) === JSON.stringify(after.silhouette),
  );

  await openChart(page, "/charts/replay");
  ok("the fold survives a reload", (await page.locator(".chart-levels-pill").count()) === 1);

  // --- where it may appear --------------------------------------------------
  await page.evaluate(() => localStorage.setItem("chart.levelPanel", "1"));
  await openChart(page, "/charts/replay");
  await page.locator(".chart-layout-btn").click();
  await page.locator('.chart-layout-menu button[title="Two side by side"]').click();
  await page.waitForTimeout(2500);
  ok(
    "only the trading pane offers it",
    (await page.locator(".chart-levels").count()) === 1,
    `${await page.locator(".sim-pane").count()} panes`,
  );

  // The journal's day view runs the same chart component and passes no pane key
  // — which is exactly the case a "primary pane" guess would have got wrong.
  await openChart(page, `/calendar/${DAY}?mode=replay`);
  ok("absent in the journal's day view", (await page.locator(".chart-levels").count()) === 0);
} catch (e) {
  ok("ran to the end", false, String(e).split("\n")[0]);
} finally {
  console.log(fails.length ? `\n${fails.length} failed: ${fails.join(", ")}` : "\nall ok");
  await browser.close();
  process.exit(fails.length ? 1 : 0);
}
