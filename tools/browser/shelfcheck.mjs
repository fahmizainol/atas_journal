// Volume shelves: does the raster actually paint, do the boxes land on top of
// it, and does the layer stay dark when it is off?
//
// The arithmetic is not this file's job — tests/test_vol_shelf.py runs the
// Python port against a fixture the TypeScript generates. What can only be
// checked in a browser is that the primitive is attached, that its two z-orders
// land on the right side of the candles, and that turning the layer on changes
// the canvas at all. A layer that computes perfectly and paints nothing looks
// identical to a working one from the terminal.
//
// Driven through /charts/replay rather than a journal chart because that route
// opens without a selection step, and because the primitive is the same object
// on both — CandlestickChart and ReplayChart attach one VolumeShelfPrimitive
// each. The sitting is PINNED: a replay with no bookmark draws a random day, and
// a check whose input changes every run measures nothing.
//
// Run: node tools/browser/shelfcheck.mjs [--headed]
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const SYMBOL = process.env.SHELF_SYMBOL ?? "NQZ5";
const DAY = process.env.SHELF_DAY ?? "2025-12-17";
/** Tape minutes to run before reading the canvas.
 *
 *  The layer is *meant* to be empty before this: a reading needs a full trailing
 *  window and a box needs to have held on top of that. A resume bookmark cannot
 *  shortcut it — `clockMs` reopens the sitting at the session start regardless,
 *  which the first version of this check discovered by passing on a chart with
 *  one candle on it. So the tape gets played. */
const RUN_MS = Number(process.env.SHELF_RUN_MS ?? 150000);

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** Layer switches and the resume bookmark, set before the app boots. The legend
 *  is a click-path with a scroll in it and this check is about the canvas. */
async function seed(page, patch) {
  await page.goto(`${BASE}/`);
  await page.evaluate(
    ([p, sym, day]) => {
      const KEY = "chart.indicatorVisibility";
      let vis = {};
      try {
        vis = JSON.parse(localStorage.getItem(KEY) ?? "{}");
      } catch {
        vis = {};
      }
      localStorage.setItem(KEY, JSON.stringify({ ...vis, ...p }));
      // contextTicks matters: without it the bookmark reopens on a different
      // amount of history and the window walks over different bars. clockMs is
      // 0 because the sitting reopens at the session start regardless — the tape
      // is played instead, see RUN_MS.
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: 0, contextTicks: 0 }),
      );
    },
    [patch, SYMBOL, DAY],
  );
}

/** Share of sampled pixels in the shelf ramp's hue.
 *
 *  By hue rather than by exact colour: the raster composites its alpha over
 *  whatever is beneath it, so no cell is ever the literal ramp value. Orange is
 *  the shelf's alone on this chart — the profiles are blue and violet, the range
 *  tool indigo — so "much more orange than before" reads soundly as "it painted".
 */
async function orangeShare(page) {
  return page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return -1;
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let hit = 0;
    let seen = 0;
    for (let i = 0; i < d.length; i += 16) {
      const r = d[i];
      const g = d[i + 1];
      const b = d[i + 2];
      seen++;
      if (r > b + 24 && g > b + 8 && r > g + 12) hit++;
    }
    return seen ? hit / seen : -1;
  });
}

/** `strokeRect` calls per frame — the boxes, counted as draws rather than pixels.
 *
 *  Pixels do not work here, and the reason is worth keeping. A *closed* shelf is
 *  stroked at 0.55 alpha, which over this surface composites to about
 *  (143, 68, 16); the raster at full strength is (131, 66, 22). They are the
 *  same colour to within a rounding error, so no threshold can hold one and drop
 *  the other — and two earlier versions of this check learned that the slow way,
 *  the first by comparing total warm pixels (0.65% either way, 1px strokes being
 *  far below that resolution) and the second by picking a brightness only a
 *  *live* box could reach, which then read 1020 in all three states.
 *
 *  Nothing else on this chart strokes a rectangle — the shelves-off pass is the
 *  control that says so — so the call count is unambiguous where the colour is
 *  not. Sampled over a window rather than at an instant because the replay draws
 *  continuously and one frame's tally is a sample of one. */
async function strokeRects(page, ms = 2000) {
  return page.evaluate(async (ms) => {
    const proto = CanvasRenderingContext2D.prototype;
    const orig = proto.strokeRect;
    let n = 0;
    let frames = 0;
    proto.strokeRect = function (...a) {
      n++;
      return orig.apply(this, a);
    };
    const t0 = performance.now();
    await new Promise((done) => {
      const tick = () => {
        frames++;
        if (performance.now() - t0 >= ms) return done();
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    proto.strokeRect = orig;
    return frames ? n / frames : -1;
  }, ms);
}

/** Run the tape, then point the chart at its own data.
 *
 *  Both halves are load-bearing. The layer needs a full window plus a hold
 *  before it can say anything, so a paused replay is a chart with one candle on
 *  it — and the replay deliberately never calls `scrollToRealTime` ("your view
 *  is yours"), so after playing, the visible *price* window no longer contains
 *  the tape. Without the reset every pixel count here reads zero while the layer
 *  is drawing perfectly, a few hundred points off screen. Double-clicking the
 *  price axis is lightweight-charts' own autoscale reset; the axis is its own
 *  narrow canvas to the right of the main one. */
async function run(page, ms) {
  const play = page.locator("button", { hasText: "Play" }).first();
  if (await play.count()) await play.click().catch(() => {});
  await page.waitForTimeout(ms);

  const cs = await page.$$eval("canvas", (els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    }),
  );
  const axis = cs.filter((c) => c.h > 200).sort((a, b) => b.x + b.w - (a.x + a.w))[0];
  if (axis) await page.mouse.dblclick(axis.x + axis.w / 2, axis.y + axis.h / 2).catch(() => {});
  await page.waitForTimeout(2000);
}

const { browser, page, errors } = await launch({ headed });
try {
  // --- off: the baseline the raster has to move ---
  await seed(page, { volumeShelf: false, volumeShelfBoxes: true });
  await openChart(page, "/charts/replay", { timeout: 180000, waitUntil: "domcontentloaded" });
  await run(page, RUN_MS);
  const probeOff = await probeChart(page);
  ok("chart draws with the layer off", probeOff.error == null, probeOff.error ?? "");
  const before = await orangeShare(page);
  const strokeOff = await strokeRects(page);
  await shot(page, "shelf-off");

  // --- on ---
  await seed(page, { volumeShelf: true, volumeShelfBoxes: true });
  await openChart(page, "/charts/replay", { timeout: 180000, waitUntil: "domcontentloaded" });
  await run(page, RUN_MS);
  const after = await orangeShare(page);
  const strokeOn = await strokeRects(page);
  await shot(page, "shelf-on");
  ok(
    "raster paints when the layer is turned on",
    after > before + 0.002,
    `orange share ${(before * 100).toFixed(2)}% -> ${(after * 100).toFixed(2)}%`,
  );

  // --- boxes off: the raster must survive it ---
  await seed(page, { volumeShelf: true, volumeShelfBoxes: false });
  await openChart(page, "/charts/replay", { timeout: 180000, waitUntil: "domcontentloaded" });
  await run(page, RUN_MS);
  const noBoxes = await orangeShare(page);
  const strokeNoBoxes = await strokeRects(page);
  await shot(page, "shelf-raster-only");
  ok(
    "raster stays when the boxes are switched off",
    noBoxes > before + 0.002,
    `orange share ${(noBoxes * 100).toFixed(2)}%`,
  );
  ok(
    "boxes actually draw",
    strokeOn >= 1 && strokeOff === 0,
    `${strokeOff} strokeRect/frame with the layer off -> ${strokeOn.toFixed(2)} with boxes on`,
  );
  ok(
    "switching the boxes off removes them",
    strokeNoBoxes === 0,
    `${strokeOn.toFixed(2)}/frame with boxes vs ${strokeNoBoxes.toFixed(2)} without`,
  );

  ok("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} finally {
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} FAILED` : "\nall ok");
process.exit(fails.length ? 1 : 0);
