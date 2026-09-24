// Does a change alter what the replay actually draws?
//
// Pins the day, because Simulator draws one at random out of ~640 when there is
// no bookmark — the trap that makes any before/after canvas comparison
// meaningless, and the reason smoke.mjs's `appearance` check flaps.
//
//   node look.tmp.mjs <tag> [date ...]
import { launch, openChart, probeChart } from "./lib.mjs";

const tag = process.argv[2] ?? "x";
const DATES = process.argv.slice(3).length ? process.argv.slice(3) : ["2026-08-18", "2026-08-19", "2026-08-21"];

const { browser, page } = await launch({ headed: false });
try {
  for (const date of DATES) {
    const [y, m, d] = date.split("-").map(Number);
    const pin = {
      symbol: "NQU6",
      date,
      clockMs: Date.UTC(y, m - 1, d, 9, 30, 0),
      attemptId: null,
      contextTicks: 0,
    };
    await page.addInitScript((p) => {
      localStorage.setItem("sim.resume.replay", JSON.stringify(p));
      for (const k of Object.keys(localStorage)) if (k.startsWith("sim.review")) localStorage.removeItem(k);
    }, pin);
    await openChart(page, "/charts/replay");
    await page.waitForTimeout(1500);
    const r = await probeChart(page);
    // A hash of the whole silhouette, so a one-column change cannot hide.
    let h = 0;
    for (const ch of String(r.silhouette)) h = (h * 31 + ch.charCodeAt(0)) | 0;
    console.log(`${tag} ${date}  bg ${r.bg}  ink ${r.ink}  sil#${h}`);
  }
} finally {
  await browser.close();
}
