// How much wall-clock one ReplayChart costs per frame while a replay plays.
//
// The engine side is already known to be cheap (a full-day snapshotTo over a
// 1.17M-print glued tape is ~60ms, an advance() frame is ~0.006ms), so the
// question a multi-pane layout actually turns on is the *draw*: 15 series and
// ~10 canvas primitives repainting per frame. This samples the page's own rAF
// cadence and its long tasks during playback, which is the number that would be
// multiplied by the pane count.
//
//   node renderbench.mjs            headless
//   node renderbench.mjs --headed   watch it
//
// Needs the dev servers up (`pnpm dev` at the repo root).
import { launch, openChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const SECONDS = 12;
// A route can be passed in, so variants of the same page can be compared:
//   node renderbench.mjs "/charts/replay?spike=gated"
// argv.slice(2), because argv[0] is the node binary — an absolute path, and so
// a match for any "starts with /" test.
const ROUTE = process.argv.slice(2).find((a) => a.startsWith("/")) ?? "/charts/replay";

const { browser, page, errors } = await launch({ headed });

try {
  // `--panes=N` seeds the page's own preference before measuring — see
  // renderbudget.mjs, which does the same so the two agree about what they ran.
  const panes = Number((process.argv.find((a) => a.startsWith("--panes=")) ?? "").split("=")[1]);
  await openChart(page, ROUTE);
  if (panes >= 1) {
    await page.evaluate((n) => {
      const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
      localStorage.setItem("sim.prefs", JSON.stringify({ ...p, panes: n }));
    }, panes);
    await openChart(page, ROUTE);
  }

  // Idle baseline first: the same sampler with nothing playing, so the playing
  // number can be read as "what the replay costs" rather than "what Chrome costs".
  const sample = async (label, secs) => {
    const r = await page.evaluate(async (ms) => {
      const frames = [];
      const longTasks = [];
      const po = new PerformanceObserver((l) => {
        for (const e of l.getEntries()) longTasks.push(e.duration);
      });
      try { po.observe({ entryTypes: ["longtask"] }); } catch { /* not supported */ }
      let last = performance.now();
      const t0 = last;
      await new Promise((done) => {
        const tick = (now) => {
          frames.push(now - last);
          last = now;
          if (now - t0 >= ms) return done();
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
      po.disconnect();
      frames.shift();
      frames.sort((a, b) => a - b);
      const at = (q) => frames[Math.floor(frames.length * q)];
      return {
        n: frames.length,
        fps: (frames.length / ((performance.now() - t0) / 1000)).toFixed(1),
        p50: at(0.5), p95: at(0.95), p99: at(0.99), max: frames[frames.length - 1],
        longTasks: longTasks.length,
        longTaskMs: longTasks.reduce((a, b) => a + b, 0),
      };
    }, secs * 1000);
    console.log(
      `${label.padEnd(22)} ${r.fps.padStart(5)} fps   frame p50 ${r.p50.toFixed(1)}ms  p95 ${r.p95.toFixed(1)}ms  p99 ${r.p99.toFixed(1)}ms  max ${r.max.toFixed(1)}ms   longtasks ${r.longTasks} (${r.longTaskMs.toFixed(0)}ms)`,
    );
    return r;
  };

  console.log(`\nsampling ${SECONDS}s each — ${ROUTE}, 1600x900\n`);
  await sample("idle (paused)", 4);

  // Play. `k` is the transport key, bound on window — so no click is needed to
  // reach it, and not clicking keeps the pointer off the chart's own tools.
  await page.keyboard.press("k");
  await page.waitForTimeout(700);
  const playing = await page.evaluate(
    () => !!document.querySelector("button[title^='Pause'], .sim-transport [aria-pressed='true']"),
  );
  console.log(`  (transport reports playing: ${playing})`);
  await sample("playing 1x", SECONDS);

  // And at speed, where the engine hands the chart more tail per frame.
  for (const key of ["]", "]", "]"]) await page.keyboard.press(key);
  await page.waitForTimeout(500);
  const speed = await page.evaluate(() => {
    const el = document.querySelector(".sim-transport");
    return el ? el.textContent.replace(/\s+/g, " ").trim().slice(0, 80) : null;
  });
  console.log(`  (transport: ${speed})`);
  await sample("playing fast", SECONDS);

  // Now the same session with the per-frame-recomputed layers switched off —
  // what a *read-only* second pane could be made to cost, as opposed to a full
  // copy of the trading chart.
  const LIGHT = {
    volumeProfile: false,
    compositeProfile: false,
    compositeNodes: false,
    developingVpNy: false,
    developingVpNyNodes: false,
    developingProfileGlobex: false,
    developingProfileNy: false,
    bigTrades: false,
  };
  await page.evaluate((off) => {
    const raw = localStorage.getItem("chart.indicatorVisibility");
    const vis = raw ? JSON.parse(raw) : {};
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify({ ...vis, ...off }));
  }, LIGHT);
  await openChart(page, ROUTE);
  await page.keyboard.press("k");
  await page.waitForTimeout(700);
  await sample("playing, profiles off", SECONDS);
  // Put the preference back — this harness shares localStorage with the app.
  await page.evaluate((keys) => {
    const raw = localStorage.getItem("chart.indicatorVisibility");
    if (!raw) return;
    const vis = JSON.parse(raw);
    for (const k of keys) delete vis[k];
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
  }, Object.keys(LIGHT));

  await shot(page, "renderbench");
  if (errors.length) console.log(`\nconsole errors: ${errors.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
}
