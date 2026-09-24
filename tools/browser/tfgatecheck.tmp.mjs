// Does the throttled overlay draw still land on the right picture?
//
// The gate in ReplayChart.applyStep skips the developing bands and value areas
// on most frames and replays the held points on the next drawing frame. If that
// buffering ever dropped a point, the streamed picture would drift away from
// the one a rebuild produces — silently, because a VWAP band that is a few
// hundredths out still looks like a VWAP band.
//
// So: play with the gate, then seek to the clock the replay is *already* at.
// A seek is a full `snapshotTo` + `setData` from tick zero — the ungated ground
// truth — at the same instant, with the same viewport (reframe: "follow"). The
// two canvases have to agree.
//
//   APP_URL=http://localhost:8000 node tfgatecheck.tmp.mjs 15s
import { launch, openChart, probeChart } from "./lib.mjs";

// One fixed session — with no bookmark, Simulator draws a day at random out of
// ~640, and two runs of this check would be comparing different markets. See
// tfcost.tmp.mjs for the same pin and the same reason.
const PIN = {
  symbol: "NQU6",
  date: "2026-08-21",
  clockMs: Date.UTC(2026, 7, 21, 9, 30, 0),
  attemptId: null,
  contextTicks: 0,
};

const tf = process.argv[2] ?? "15s";
const { browser, page, errors } = await launch({ headed: false });

try {
  await page.addInitScript(({ id, pin }) => {
    const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
    localStorage.setItem("sim.prefs", JSON.stringify({ ...p, timeframe: id, layout: "one" }));
    localStorage.setItem("sim.resume.replay", JSON.stringify(pin));
    for (const k of Object.keys(localStorage))
      if (k.startsWith("sim.review")) localStorage.removeItem(k);
  }, { id: tf, pin: PIN });
  await openChart(page, "/charts/replay");
  await page.waitForTimeout(1000);

  await page.keyboard.press("k");
  await page.waitForTimeout(6000);
  await page.keyboard.press("k"); // pause — leaves the gate mid-buffer on purpose
  await page.waitForTimeout(400);

  const clockText = await page.evaluate(() => document.querySelector(".sim-clock")?.textContent ?? null);
  const streamed = await probeChart(page);

  // Seek to where it already is: same clock, full rebuild.
  const at = await page.$eval(".sim-scrub", (el) => el.value);
  await page.$eval(
    ".sim-scrub",
    (el, v) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    },
    at,
  );
  await page.waitForTimeout(1200);
  const rebuilt = await probeChart(page);

  // `silhouette` is a comma-joined "top:bot" per sampled column — the first and
  // last non-background row. A band drawn a few pixels off, or a value area a
  // bar behind, moves it; a redraw of the same picture does not. `ink` is the
  // total non-background pixel count, which catches a layer that vanished
  // without moving the outline.
  const cols = (s) => String(s ?? "").split(",").map((p) => p.split(":").map(Number));
  const a = cols(streamed.silhouette);
  const b = cols(rebuilt.silhouette);
  const n = Math.min(a.length, b.length);
  if (!n || a.length !== b.length) throw new Error(`silhouette shape ${a.length} vs ${b.length}`);
  let diff = 0;
  let worst = 0;
  for (let i = 0; i < n; i++) {
    const d = Math.abs(a[i][0] - b[i][0]) + Math.abs(a[i][1] - b[i][1]);
    if (!Number.isFinite(d)) throw new Error(`bad column ${i}: ${a[i]} vs ${b[i]}`);
    if (d) diff++;
    worst = Math.max(worst, d);
  }
  const inkDrift = Math.abs(streamed.ink - rebuilt.ink) / Math.max(1, rebuilt.ink);
  console.log(`\n${tf} @ ${clockText} (scrub ${at})`);
  console.log(`  columns sampled    ${n}`);
  console.log(`  columns differing  ${diff}`);
  console.log(`  worst column drift ${worst}px`);
  console.log(`  ink  streamed ${streamed.ink} / rebuilt ${rebuilt.ink}  (${(inkDrift * 100).toFixed(2)}%)`);
  console.log(`  bg   streamed ${streamed.bg} / rebuilt ${rebuilt.bg}`);
  const pass = diff === 0 && inkDrift < 0.01;
  console.log(pass ? "\nPASS — streamed picture == rebuilt picture" : "\nDIFFERS — inspect");
  if (errors.length) console.log(`console errors: ${errors.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
}
