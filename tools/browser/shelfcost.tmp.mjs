// What one frame of the shelf raster actually costs, on a playing replay.
//
// "Kinda laggy" is not a number. This one is: the raster is a price x time
// field, so its per-frame draw is columns x rows, and neither is bounded by the
// viewport today. Counting canvas calls rather than CPU-profiling because the
// suspected cost is call *volume* — a profile would report time inside
// `fillRect` without saying how many times it was reached.
//
// Two passes over the SAME pinned sitting, layer off then on, so the difference
// is the layer and not the market.
//
//   node tools/browser/shelfcost.tmp.mjs
import { launch, openChart, BASE } from "./lib.mjs";

const SYMBOL = "NQZ5";
const DAY = "2025-12-17";
const RUN_MS = Number(process.env.SHELF_RUN_MS ?? 150000);

async function seed(page, on) {
  await page.goto(`${BASE}/`);
  await page.evaluate(
    ([on, sym, day]) => {
      const KEY = "chart.indicatorVisibility";
      let vis = {};
      try { vis = JSON.parse(localStorage.getItem(KEY) ?? "{}"); } catch { vis = {}; }
      localStorage.setItem(
        KEY,
        JSON.stringify({ ...vis, volumeShelf: on, volumeShelfBoxes: on }),
      );
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: 0, contextTicks: 0 }),
      );
    },
    [on, SYMBOL, DAY],
  );
}

/** Count canvas calls per animation frame while the tape keeps playing. */
async function sample(page, ms) {
  return page.evaluate(async (ms) => {
    const proto = CanvasRenderingContext2D.prototype;
    const origRect = proto.fillRect;
    const origStroke = proto.strokeRect;
    const fillDesc = Object.getOwnPropertyDescriptor(proto, "fillStyle");
    // Path2D.rect is the raster's real unit of work now that cells are batched:
    // `fillStyle` counts the opacity steps (capped at 16) and says nothing about
    // how many cells went into them.
    const pathProto = Path2D.prototype;
    const origPathRect = pathProto.rect;
    let rects = 0, strokes = 0, styles = 0, cells = 0;
    proto.fillRect = function (...a) { rects++; return origRect.apply(this, a); };
    proto.strokeRect = function (...a) { strokes++; return origStroke.apply(this, a); };
    pathProto.rect = function (...a) { cells++; return origPathRect.apply(this, a); };
    Object.defineProperty(proto, "fillStyle", {
      ...fillDesc,
      set(v) { styles++; fillDesc.set.call(this, v); },
    });

    const gaps = [];
    let last = performance.now();
    const t0 = last;
    await new Promise((done) => {
      const tick = (now) => {
        gaps.push(now - last);
        last = now;
        if (now - t0 >= ms) return done();
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });

    proto.fillRect = origRect;
    proto.strokeRect = origStroke;
    pathProto.rect = origPathRect;
    Object.defineProperty(proto, "fillStyle", fillDesc);

    const n = gaps.length;
    const sorted = [...gaps].sort((a, b) => a - b);
    return {
      frames: n,
      fps: n / ((last - t0) / 1000),
      medianGap: sorted[n >> 1],
      worstGap: sorted[n - 1],
      rectsPerFrame: rects / n,
      strokesPerFrame: strokes / n,
      stylesPerFrame: styles / n,
      cellsPerFrame: cells / n,
    };
  }, ms);
}

/** One measured state. `zoomOut` widens the time scale first — the default view
 *  after an autoscale reset is a handful of bars, which is the case the column
 *  cull handles best and therefore the least informative one to measure. A
 *  session-wide view is where the raster has real work to do. */
async function pass(page, on, zoomOut = 0) {
  await seed(page, on);
  await openChart(page, "/charts/replay", { timeout: 180000 });
  const play = page.locator("button", { hasText: "Play" }).first();
  if (await play.count()) await play.click().catch(() => {});
  await page.waitForTimeout(RUN_MS);
  // Autoscale reset: the replay never scrolls to the live edge, so without this
  // the raster is drawing off-screen and the counters read a chart with nothing
  // in view. (Off-screen cells are still *drawn* — that is part of the point.)
  const cs = await page.$$eval("canvas", (els) =>
    els.map((e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; }),
  );
  const axis = cs.filter((c) => c.h > 200).sort((a, b) => b.x + b.w - (a.x + a.w))[0];
  if (axis) await page.mouse.dblclick(axis.x + axis.w / 2, axis.y + axis.h / 2).catch(() => {});
  await page.waitForTimeout(1500);
  if (zoomOut > 0) {
    const main = cs.filter((c) => c.h > 200).sort((a, b) => b.w - a.w)[0];
    if (main) {
      await page.mouse.move(main.x + main.w / 2, main.y + main.h / 2);
      for (let i = 0; i < zoomOut; i++) await page.mouse.wheel(0, 120);
    }
    await page.waitForTimeout(1500);
  }
  return sample(page, 4000);
}

const { browser, page, errors } = await launch({ headed: false });
try {
  const off = await pass(page, false);
  const on = await pass(page, true);
  const wide = await pass(page, true, 25);
  const row = (l, r) =>
    `${l.padEnd(20)} ${r.fps.toFixed(1).padStart(5)}fps  median ${r.medianGap.toFixed(1).padStart(5)}ms  worst ${r.worstGap.toFixed(0).padStart(4)}ms  ` +
    `fillStyle ${Math.round(r.stylesPerFrame).toString().padStart(5)}/f  rasterCells ${Math.round(r.cellsPerFrame).toString().padStart(6)}/f  strokeRect ${r.strokesPerFrame.toFixed(1).padStart(5)}/f`;
  console.log(`\n${row("shelves off", off)}\n${row("shelves on", on)}\n${row("shelves on, zoomed out", wide)}\n`);
  console.log(
    `on vs off:   ${(on.medianGap - off.medianGap).toFixed(1)}ms/frame\n` +
      `wide vs off: ${(wide.medianGap - off.medianGap).toFixed(1)}ms/frame\n`,
  );
  if (errors.length) console.log(`console errors: ${errors.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
}
