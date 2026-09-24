// One-off look: both tape-event layers on together — the hatch, the per-kind
// wash, the label glyphs and the collision stroke. Pins the sitting (a replay
// with no bookmark draws a random day) and plays the tape at 300x so the RTH
// open's events publish, then screenshots and reads the legend counts back.
//
// Run: node tools/browser/eventlook.tmp.mjs [--headed]
import { launch, openChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const SYMBOL = process.env.EV_SYMBOL ?? "NQZ5";
const DAY = process.env.EV_DAY ?? "2025-12-17";
const RUN_MS = Number(process.env.EV_RUN_MS ?? 120000);

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const { browser, page, errors } = await launch({ headed });
try {
  await page.goto(`${BASE}/`);
  await page.evaluate(
    ([sym, day]) => {
      const KEY = "chart.indicatorVisibility";
      let vis = {};
      try {
        vis = JSON.parse(localStorage.getItem(KEY) ?? "{}");
      } catch {
        vis = {};
      }
      localStorage.setItem(
        KEY,
        JSON.stringify({
          ...vis,
          sweepBursts: true,
          absorption: true,
          // Quiet the layers that share the events' hues, so what's orange or
          // blue in the shot is the bands.
          volumeShelf: false,
          volumeShelfBoxes: false,
          bigTrades: false,
        }),
      );
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: 0, contextTicks: 0 }),
      );
      // Fastest tape, so the RTH open (where absorption can first score) is
      // reached inside the run window.
      let prefs = {};
      try {
        prefs = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
      } catch {
        prefs = {};
      }
      localStorage.setItem("sim.prefs", JSON.stringify({ ...prefs, speed: 300 }));
    },
    [SYMBOL, DAY],
  );
  await openChart(page, "/charts/replay", { timeout: 180000, waitUntil: "domcontentloaded" });

  const play = page.locator("button", { hasText: "Play" }).first();
  if (await play.count()) await play.click().catch(() => {});
  await page.waitForTimeout(RUN_MS);

  // Autoscale back onto the tape ("your view is yours" — the replay never
  // scrolls for you), then let a repaint land.
  const cs = await page.$$eval("canvas", (els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    }),
  );
  const axis = cs.filter((c) => c.h > 200).sort((a, b) => b.x + b.w - (a.x + a.w))[0];
  if (axis) await page.mouse.dblclick(axis.x + axis.w / 2, axis.y + axis.h / 2).catch(() => {});
  // Zoom the time scale out until the day (and its bands) is in view, then
  // autoscale price onto it.
  const main = cs.filter((c) => c.h > 200).sort((a, b) => b.w * b.h - a.w * a.h)[0];
  if (main) {
    await page.mouse.move(main.x + main.w * 0.6, main.y + main.h / 2);
    for (let i = 0; i < 40; i++) {
      await page.mouse.wheel(0, 600);
      await page.waitForTimeout(80);
    }
  }
  if (axis) await page.mouse.dblclick(axis.x + axis.w / 2, axis.y + axis.h / 2).catch(() => {});
  await page.waitForTimeout(2000);

  const legend = await page.evaluate(() => {
    const rows = [...document.querySelectorAll("*")]
      .map((n) => n.textContent ?? "")
      .filter((t) => /Sweep bursts · |Absorption · /.test(t) && t.length < 120);
    return rows.slice(0, 4);
  });
  console.log("legend:", JSON.stringify(legend, null, 2));
  const sw = legend.find((t) => t.includes("Sweep bursts"));
  const ab = legend.find((t) => t.includes("Absorption"));
  ok("sweep legend row present", !!sw, sw ?? "");
  ok("absorb legend row present", !!ab, ab ?? "");
  const count = (t) => Number((t?.match(/· (\d+)\s*$/) ?? [])[1] ?? -1);
  ok("some bursts drawn", count(sw) > 0, `count ${count(sw)}`);
  ok("some absorption drawn", count(ab) > 0, `count ${count(ab)}`);
  ok(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
  await shot(page, "eventlook");
} finally {
  await browser.close();
}
process.exit(fails.length ? 1 : 0);
