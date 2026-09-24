// What a bucketing costs the replay, across bucketings.
//
// The question this was written for: a seconds bar played visibly worse than
// 500t or 1m, and the suspicion was something wrong in the seconds branch. It
// is not — frame cost is linear in *how many bars the bucketing puts on the
// chart*, and the two families sit on one curve (50t is as slow as 15s, 200t
// sits next to 30s). A seconds bar only looks special because NQ averages ~5
// prints/second over the glued tape and ~1/second through the overnight the
// replay starts in, so a wall-clock bucket keeps minting bars for fifteen quiet
// hours where a tick bar mints almost none.
//
//   APP_URL=http://localhost:8000 node tfcost.tmp.mjs 500t 1m 30s 15s
//
// Point APP_URL at the API (:8000), which serves frontend/dist: the dev server's
// React costs ~2.5x in jsxDEV that ships to nobody, and it swamps the thing
// being measured. `pnpm build` first.
//
// Three traps this harness had to grow, all of which produce numbers that look
// fine and mean nothing:
//   - **The day is drawn at random.** With no bookmark, Simulator's `anyDay`
//     picks uniformly from ~640 cached days, so every run measures a different
//     session with a different print density and a different bar count. The
//     bookmark below pins one day, which is what makes two runs comparable at
//     all. Clearing the bookmark — the obvious thing to do for a clean start —
//     is exactly what un-pins it.
//   - The bookmark also pins the clock, so runs cannot march deeper into the
//     session one after another. Without it each successive run in the same
//     browser resumes where the last stopped and holds more bars than it.
//   - `k` does not always play (nothing loaded, end of tape, a review owed). A
//     replay that never started reads as 60fps. Both the Pause button and a
//     moving clock are checked, and a run that fails either is flagged.
import { launch, openChart } from "./lib.mjs";

// One fixed session, at the bell. Times are the naive-local epoch the tape is
// shipped in (api/tape_codec), which is why this is a UTC construction of a New
// York wall clock. `attemptId: null` is a bookmark with no order log behind it —
// a day and a clock, which is all the pin needs.
const DAY = { symbol: "NQU6", date: "2026-08-21" };
const PIN = { ...DAY, clockMs: Date.UTC(2026, 7, 21, 9, 30, 0), attemptId: null, contextTicks: 0 };

const LIST = process.argv.slice(2).filter((a) => !a.startsWith("-"));
const TFS = LIST.length ? LIST : ["500t", "1m", "30s", "15s"];
const PLAY = 8;

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
const clock = () => page.evaluate(() => document.querySelector(".sim-clock")?.textContent ?? null);

const sample = async (secs) =>
  page.evaluate(async (ms) => {
    const frames = [];
    const long = [];
    const po = new PerformanceObserver((l) => {
      for (const e of l.getEntries()) long.push(e.duration);
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
    const at = (q) => frames[Math.floor(frames.length * q)] ?? 0;
    return {
      fps: frames.length / ((performance.now() - t0) / 1000),
      p50: at(0.5), p95: at(0.95),
      longMs: long.reduce((a, b) => a + b, 0),
    };
  }, secs * 1000);

try {
  console.log(`\n${PLAY}s playing, one pane, 1600x900 — ${process.env.APP_URL ?? "default"}\n`);
  for (const tf of TFS) {
    await page.addInitScript(({ id, pin }) => {
      const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
      localStorage.setItem("sim.prefs", JSON.stringify({ ...p, timeframe: id, layout: "one" }));
      // Same day, same clock, every run — see the header. `replay` is the
      // funded account's scope name (lib/replayResume).
      localStorage.setItem("sim.resume.replay", JSON.stringify(pin));
      for (const k of Object.keys(localStorage))
        if (k.startsWith("sim.review")) localStorage.removeItem(k);
    }, { id: tf, pin: PIN });
    await openChart(page, "/charts/replay");
    await page.waitForTimeout(1000);

    await page.keyboard.press("k");
    await page.waitForTimeout(700);
    const playing = (await page.locator(".sim-transport button", { hasText: "Pause" }).count()) === 1;
    const c0 = await clock();
    const r = await sample(PLAY);
    const c1 = await clock();
    await page.keyboard.press("k");

    const ok = playing && c0 !== c1;
    console.log(
      `${tf.padEnd(5)} ${r.fps.toFixed(1).padStart(5)} fps   p50 ${r.p50.toFixed(1).padStart(5)}ms   ` +
        `p95 ${r.p95.toFixed(1).padStart(5)}ms   longtasks ${(r.longMs / 10 / PLAY).toFixed(0).padStart(3)}% of wall   ` +
        `${c0} -> ${c1}${ok ? "" : "   <-- NOT PLAYING, discard"}`,
    );
  }
  if (errors.length) console.log(`\nconsole errors: ${errors.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
}
