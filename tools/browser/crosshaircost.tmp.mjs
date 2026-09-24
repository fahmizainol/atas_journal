// What a crosshair move actually costs.
//
// The premise being tested: lightweight-charts v5 already splits the canvas. On
// InvalidationLevel.Cursor it skips the series/grid/background draw entirely and
// repaints ONLY the top canvas — clearRect, the crosshair, then every primitive
// view reporting zOrder "top". So a pointer move should be cheap *unless*
// something expensive rides the top layer. This repo puts three profile overlays
// up there (DevelopingProfilePrimitive's NodeRenderer, RangeProfilePrimitive's
// OverlayRenderer, VolumeShelfPrimitive's BoxRenderer each build a second "top"
// view beside their "bottom" fill), so the question is whether they are worth
// anything.
//
// TWO CONFOUNDS this harness exists to defeat, both of which produced a
// convincing four-fold "speedup" that was not real:
//
//  1. Replay draws a RANDOM day unless a sitting is bookmarked, and frame cost
//     is a function of how much tape the day holds. Unpinned, a before/after
//     compares two different markets. Hence pin().
//  2. A playing replay gets heavier as it runs — more bars, bigger profiles. So
//     sampling "pointer off" for 8s and then "sweeping" for 8s charges the
//     second arm for the tape the first arm accumulated. Hence the interleave:
//     short alternating bursts, so drift lands on both arms equally, compared by
//     median burst rather than by one long sample.
//
//   node crosshaircost.tmp.mjs             headless, /charts/replay
//   node crosshaircost.tmp.mjs --headed
//   node crosshaircost.tmp.mjs --panes=4
//
// MEASURE AGAINST PRODUCTION, and not because production is nicer: ~38% of
// dev-server profile time is React's `jsxDEV`, which does not exist in a built
// bundle, and it swamps everything this file is trying to see. On :5173 the page
// reads 28 fps idle / 20 fps sweeping; the same sitting on a production bundle
// reads 59 / 57. Use the preview server the repo already carries for this —
//
//   pnpm --dir frontend build
//   pnpm --dir frontend exec vite preview --config vite.preview.config.ts
//   APP_URL=http://localhost:4300 node crosshaircost.tmp.mjs
//
// (`vite preview` does not read `server.proxy`, which is why that config exists.)
// Dev servers up (`pnpm dev` at the repo root) is enough for a correctness run.
import { launch, openChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const ROUTE = process.argv.slice(2).find((a) => a.startsWith("/")) ?? "/charts/replay";
/** Length of one burst, and how many of each arm. Short bursts keep the two arms
 *  close together in time; several of each let the median absorb a stray GC. */
const BURST_S = 2;
const ROUNDS = 4;

// Same pinned sitting as shelfcost.tmp.mjs, for the same reason.
const SYMBOL = "NQZ5";
const DAY = "2025-12-17";

const { browser, page, errors } = await launch({ headed });

/** Bookmark the sitting so every burst — and every run — is the same tape.
 *  `contextTicks` is part of the bookmark: without it the page treats the record
 *  as incomplete and re-rolls the day. `panes` seeds the grid size the same way
 *  renderbench does, so the two agree about what they ran. */
async function pin(panes) {
  await page.goto(`${BASE}/`);
  await page.evaluate(
    ([sym, day, n]) => {
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: 0, contextTicks: 0 }),
      );
      if (n >= 1) {
        const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
        localStorage.setItem("sim.prefs", JSON.stringify({ ...p, panes: n }));
      }
    },
    [SYMBOL, DAY, panes],
  );
}

/** Sample rAF cadence for `secs` while `during` runs. The sampler is in-page;
 *  the sweep is driven from here, because a mousemove dispatched in-page does
 *  not go through the same hit-test path as a real one. */
async function burst(secs, during) {
  const started = page.evaluate((ms) => {
    window.__cc = [];
    let last = performance.now();
    const t0 = last;
    return new Promise((done) => {
      const tick = (now) => {
        window.__cc.push(now - last);
        last = now;
        if (now - t0 >= ms) return done(performance.now() - t0);
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }, secs * 1000);

  if (during) await during(secs);
  const elapsed = await started;

  return page.evaluate((el) => {
    const f = window.__cc.slice(1).sort((a, b) => a - b);
    return {
      fps: f.length / (el / 1000),
      p50: f[Math.floor(f.length * 0.5)],
      p95: f[Math.floor(f.length * 0.95)],
    };
  }, elapsed);
}

/** Sweep the pointer across the chart at roughly one move per frame. A
 *  horizontal sweep at mid-height is the worst honest case: every step changes
 *  the crosshair's bar, so the readout and every top-layer view redraw. */
function sweeper(box) {
  return async (secs) => {
    const y = box.y + box.height / 2;
    const x0 = box.x + box.width * 0.15;
    const x1 = box.x + box.width * 0.85;
    const until = Date.now() + secs * 1000;
    let i = 0;
    while (Date.now() < until) {
      // Triangle wave, ~3px a step: a real hand does not teleport, and a jump
      // wide enough to skip bars would under-count the redraws.
      const t = (i % 200) / 200;
      await page.mouse.move(x0 + (x1 - x0) * (t < 0.5 ? t * 2 : 2 - t * 2), y);
      i++;
    }
  };
}

const med = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];

const PANES = Number((process.argv.find((a) => a.startsWith("--panes=")) ?? "").split("=")[1]) || 0;

try {
  await pin(PANES);
  await openChart(page, ROUTE, { waitUntil: "domcontentloaded" });

  const box = await page.locator("canvas").first().boundingBox();
  const sitting = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("sim.resume.replay") ?? "null"),
  );
  const paneCount = await page.evaluate(() => document.querySelectorAll(".chart-legend").length);
  console.log(
    `\n${ROUTE} — ${Math.round(box.width)}x${Math.round(box.height)} — sitting ${sitting?.symbol} ${sitting?.date} — ${paneCount} pane(s)`,
  );
  console.log(`interleaved: ${ROUNDS} x ${BURST_S}s per arm\n`);

  const sweep = sweeper(box);

  // Play, at speed: an idle chart has ~13ms of slack a frame, and rAF cadence
  // cannot see work that fits in the slack. Spending it first is what makes the
  // difference between the arms readable at all.
  await page.mouse.move(5, 5);
  await page.keyboard.press("k");
  await page.waitForTimeout(700);
  for (const key of ["]", "]", "]"]) await page.keyboard.press(key);
  await page.waitForTimeout(500);

  const off = [];
  const swept = [];
  for (let r = 0; r < ROUNDS; r++) {
    await page.mouse.move(5, 5);
    await page.waitForTimeout(120);
    off.push(await burst(BURST_S));
    swept.push(await burst(BURST_S, sweep));
    console.log(
      `  round ${r + 1}   off ${off[r].fps.toFixed(1).padStart(5)} fps p50 ${off[r].p50
        .toFixed(1)
        .padStart(5)}ms   sweeping ${swept[r].fps.toFixed(1).padStart(5)} fps p50 ${swept[r].p95
        .toFixed(1)
        .padStart(5)}ms`,
    );
  }
  await page.keyboard.press("k");

  const f = (a) => med(a.map((x) => x.fps));
  const p50 = (a) => med(a.map((x) => x.p50));
  const p95 = (a) => med(a.map((x) => x.p95));
  console.log(`
  median of ${ROUNDS} bursts
    pointer off      ${f(off).toFixed(1).padStart(5)} fps   p50 ${p50(off).toFixed(1)}ms   p95 ${p95(off).toFixed(1)}ms
    sweeping         ${f(swept).toFixed(1).padStart(5)} fps   p50 ${p50(swept).toFixed(1)}ms   p95 ${p95(swept).toFixed(1)}ms
    cost of a move   ${(f(off) - f(swept)).toFixed(1)} fps   p50 ${(p50(swept) - p50(off)).toFixed(2)}ms   p95 ${(p95(swept) - p95(off)).toFixed(2)}ms
`);

  await shot(page, "crosshaircost");
  if (errors.length) console.log(`console errors: ${errors.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
}
