// Throwaway: a clean look at the Dynamic Swing VWAP with every other layer off,
// so the line's direction tint and the anchor flags are the only things drawn.
import { launch, openChart, shot, BASE } from "./lib.mjs";

const [, , swingArg, scopeArg] = process.argv;
const swingPeriod = swingArg ? +swingArg : 50;
const bandScope = scopeArg === "all" ? "all" : "live";

await (async () => {
  const { browser, page, errors } = await launch();
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(({ swing, bandScope }) => {
    const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
    for (const k of Object.keys(vis)) vis[k] = false;
    // Everything the legend can offer, off — including the ones absent from a
    // stored blob written by an older build.
    for (const k of [
      "vwapGlobex", "vwapNy", "vwapWeekly", "vwapAnchored",
      "modernVwap", "modernVwapSignals",
      "developingProfileGlobex", "developingProfileNy", "developingProfileWeekly",
      "developingVpNy", "developingVpNyNodes", "initialBalance", "ibExtensions",
      "volumeProfile", "bigTrades", "cvd", "volRuler",
      "compositeProfile", "compositeNodes", "sweepBursts", "absorption", "replayTrades",
    ]) vis[k] = false;
    vis.dynamicSwingVwap = true;
    localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
    const prefs = JSON.parse(localStorage.getItem("sim.prefs") || "{}");
    prefs.dynamicSwingVwap = {
      swingPeriod: swing, apt: 20, adaptApt: true, volBias: 1,
      bands: 2, bandScope, shadow: true, flags: "labels",
    };
    localStorage.setItem("sim.prefs", JSON.stringify(prefs));
  }, { swing: swingPeriod, bandScope });
  await openChart(page, "/charts/replay");
  const path = await shot(page, `dsv-clean-${swingPeriod}-${bandScope}`);
  console.log(JSON.stringify({ path, errors }, null, 2));
  await browser.close();
})();
