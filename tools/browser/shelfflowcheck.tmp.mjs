// The shelf raster's flow field: does it paint, and is it a different picture?
//
// Measured by the fill colours the raster actually assigns, not by counting
// coloured pixels. The flow field is green/red and so are the candles, so a
// pixel census would be reading the price action as much as the layer. The
// fills are unambiguous: the size field composes one hue, the flow field
// composes the delta lane's two, and nothing else on the chart uses either as a
// bare `rgba(...)` triplet at these alphas.
//
// Switched through the knob rather than by reloading with a different pref,
// because the field is supposed to be a *repaint* — every column already carries
// both quantities — and a reload would prove nothing about that.
//
// Run: node tools/browser/shelfflowcheck.tmp.mjs [--headed]
import { launch, openChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const RUN_MS = Number(process.env.SHELF_RUN_MS ?? 150000);
const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** Distinct raster fill colours and the rects drawn into them, over a window. */
const sample = (page, ms = 1500) =>
  page.evaluate(async (ms) => {
    const proto = CanvasRenderingContext2D.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "fillStyle");
    const pathProto = Path2D.prototype;
    const origRect = pathProto.rect;
    const seen = new Set();
    let cells = 0;
    let frames = 0;
    pathProto.rect = function (...a) { cells++; return origRect.apply(this, a); };
    Object.defineProperty(proto, "fillStyle", {
      ...desc,
      set(v) { if (typeof v === "string" && v.startsWith("rgba(")) seen.add(v); desc.set.call(this, v); },
    });
    const t0 = performance.now();
    await new Promise((done) => {
      const tick = () => {
        frames++;
        if (performance.now() - t0 >= ms) return done();
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    pathProto.rect = origRect;
    Object.defineProperty(proto, "fillStyle", desc);
    return { colours: [...seen], cellsPerFrame: frames ? cells / frames : -1 };
  }, ms);

/** Which of the three known raster hues a colour set contains.
 *
 *  Raster fills only, which is what the alpha filter is for. The *boxes* are
 *  drawn in the shelf's orange whatever the field is showing — they are the size
 *  reading and the field switch is not supposed to move them — so counting every
 *  orange fill on the chart would report the layer's correct behaviour as a
 *  failure. It did, on the first run of this check. The raster's ramp tops out
 *  at MAX_ALPHA = 0.5 and the box strokes sit at 0.55 and 0.95, so the two are
 *  separable without guessing. */
const hues = (all) => {
  const colours = all.filter((c) => {
    const m = c.match(/,\s*([\d.]+)\)$/);
    return m && Number(m[1]) <= 0.5;
  });
  const has = (rgb) => colours.some((c) => c.startsWith(`rgba(${rgb},`));
  return {
    size: has("249, 115, 22") || has("194, 65, 12"),
    up: colours.some((c) => /^rgba\((\d+), (\d+), (\d+),/.test(c) && (() => {
      const [, r, g, b] = c.match(/^rgba\((\d+), (\d+), (\d+),/).map(Number);
      return g > r + 20 && g > b + 20;
    })()),
    down: colours.some((c) => {
      const m = c.match(/^rgba\((\d+), (\d+), (\d+),/);
      if (!m) return false;
      const [, r, g, b] = m.map(Number);
      return r > g + 40 && r > b + 40 && !(r > 200 && g > 100 && b < 60);
    }),
  };
};

const { browser, page, errors } = await launch({ headed });
try {
  await page.goto(`${BASE}/`);
  await page.evaluate(() => {
    const KEY = "chart.indicatorVisibility";
    // Every other layer off, and this is load-bearing rather than tidy. The
    // shelf ramp's orange is `palette.orange`, which the weekly VWAP band fill,
    // the big-trade marks, the event bands and the short-trade markers all also
    // use — so "is the raster still painting its own hue?" cannot be answered on
    // a chart with those on. The first run of this check reported the layer
    // broken for exactly that reason. With the layer alone on the canvas, a
    // colour is the layer.
    const OFF = [
      "vwapGlobex", "vwapNy", "vwapWeekly", "vwapAnchored", "atr", "cvd", "cvdOsc",
      "levels", "initialBalance", "ibExtensions", "volumeProfile",
      "developingProfileGlobex", "developingProfileNy", "developingProfileWeekly",
      "ema9", "ema20", "ema50", "ema200", "rsi", "touches", "va_snaps",
      "replayTrades", "bigTrades", "compositeProfile", "compositeNodes",
      "developingVpNy", "developingVpNyNodes", "sweepBursts", "absorption",
      "volRuler", "modernVwap", "modernVwapSignals", "dynamicSwingVwap",
    ];
    const vis = Object.fromEntries(OFF.map((k) => [k, false]));
    localStorage.setItem(KEY, JSON.stringify({ ...vis, volumeShelf: true, volumeShelfBoxes: true }));
    localStorage.setItem("chart.shelfField", "size");
    localStorage.setItem("sim.resume.replay", JSON.stringify({
      symbol: "NQZ5", date: "2025-12-17", clockMs: 0, contextTicks: 0,
    }));
  });
  await openChart(page, "/charts/replay", { timeout: 180000, waitUntil: "domcontentloaded" });

  const play = page.locator("button", { hasText: "Play" }).first();
  if (await play.count()) await play.click().catch(() => {});
  await page.waitForTimeout(RUN_MS);
  const cs = await page.$$eval("canvas", (els) => els.map((e) => {
    const r = e.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  }));
  const axis = cs.filter((c) => c.h > 200).sort((a, b) => b.x + b.w - (a.x + a.w))[0];
  if (axis) await page.mouse.dblclick(axis.x + axis.w / 2, axis.y + axis.h / 2).catch(() => {});
  await page.waitForTimeout(2000);

  const sizePass = await sample(page);
  const sizeHues = hues(sizePass.colours);
  await shot(page, "shelf-field-size");
  ok("size field paints its own hue", sizeHues.size && sizePass.cellsPerFrame > 0,
    `${Math.round(sizePass.cellsPerFrame)} cells/frame`);
  ok("size field is not two-sided", !(sizeHues.up && sizeHues.down));

  // --- switch the field through the knob, no reload ---
  const row = page.locator('[data-ind-item="volumeShelf"]');
  await row.locator(".chart-legend-dots").click();
  await row.locator("select").first().selectOption("flow");
  await page.waitForTimeout(2500);

  const flowPass = await sample(page);
  const flowHues = hues(flowPass.colours);
  await shot(page, "shelf-field-flow");
  ok("flow field paints", flowPass.cellsPerFrame > 0, `${Math.round(flowPass.cellsPerFrame)} cells/frame`);
  ok("flow field draws both sides", flowHues.up && flowHues.down,
    `buy-led ${flowHues.up}, sell-led ${flowHues.down}`);
  ok("flow field replaces the size hue rather than adding to it", !flowHues.size);

  const label = (await row.locator(".chart-legend-row span").nth(1).innerText()).trim();
  ok("legend says which field is drawn", label.includes("order flow"), label);

  ok("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} finally {
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} FAILED` : "\nall ok");
process.exit(fails.length ? 1 : 0);
