// Throwaway: the HTF trend layer (lib/htfTrend) on the real replay chart.
//
// Pure canvas, so what is checkable is its ink:
//   * the EMA lines in the layer's gold (#facc15), which nothing else draws once
//     every other layer is off;
//   * the tint as a RIBBON (solid 5px strip at the pane bottom, green or red) —
//     the wash is 7% alpha and not attributable, the ribbon is;
//   * off: both gone; lines off: gold gone, ribbon stays;
//   * the legend row names the frames and a state.
import { launch, openChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";
const SYMBOL = "NQZ5";
const DAY = "2025-12-17";
const [Y, M, D] = DAY.split("-").map(Number);
const CLOCK = Date.UTC(Y, M - 1, D, 18, 0, 0);

const P = (over = {}) => ({ frames: "300,900", length: 20, lines: true, tint: "ribbon", ...over });

const seed = async (page, on, params) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([on, params, sym, day, clk]) => {
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: clk, attemptId: null, contextTicks: 0 }),
      );
      const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
      for (const k of [
        "vwapGlobex", "vwapNy", "vwapWeekly", "vwapAnchored",
        "modernVwap", "modernVwapSignals", "dynamicSwingVwap",
        "developingProfileGlobex", "developingProfileNy", "developingProfileWeekly",
        "developingVpNy", "developingVpNyNodes", "initialBalance", "ibExtensions",
        "volumeProfile", "volumeShelf", "volumeShelfBoxes", "bigTrades",
        "cvd", "cvdOsc", "volRuler", "externalChart", "rankedZones", "econEvents",
        "compositeProfile", "compositeNodes", "sweepBursts", "absorption", "replayTrades",
      ]) vis[k] = false;
      vis.htfTrend = on;
      localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
      localStorage.setItem("chart.htfTrend", JSON.stringify(params));
    },
    [on, params, SYMBOL, DAY, CLOCK],
  );
};

const probe = (page) =>
  page.evaluate(() => {
    // Every canvas the main pane stacks: LWC draws "top" pane views (the lines)
    // on the overlay canvas, not the one the candles are on.
    const all = [...document.querySelectorAll("canvas")];
    const big = all.sort((a, b) => b.width * b.height - a.width * a.height)[0];
    if (!big) return { error: "no canvas" };
    const pane = all.filter((c) => c.width === big.width && c.height === big.height);
    let gold = 0;
    let ribbon = 0;
    const dpr = window.devicePixelRatio || 1;
    const band = Math.ceil(12 * dpr);
    for (const c of pane) {
      const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
      const near = (i, h, tol = 40) =>
        Math.abs(d[i] - h[0]) < tol && Math.abs(d[i + 1] - h[1]) < tol && Math.abs(d[i + 2] - h[2]) < tol;
      for (let y = 0; y < c.height; y++) {
        for (let x = 0; x < c.width; x++) {
          const i = (y * c.width + x) * 4;
          if (d[i + 3] < 128) continue;
          if (near(i, [250, 204, 21], 30)) gold++;
          if (y > c.height - band && (near(i, [30, 150, 75], 45) || near(i, [180, 55, 58], 45))) ribbon++;
        }
      }
    }
    return { gold, ribbon, canvases: pane.length };
  });

const results = [];
const check = (name, ok, detail = "") => {
  results.push([name, ok]);
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
};

const { browser, page } = await launch({ headed: process.argv.includes("--headed") });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const legend = async () =>
  ((await page.locator(".chart-legend >> text=/HTF trend/").first().textContent({ timeout: 10000 })) ?? "").trim();

try {
  await seed(page, false, P());
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  const off = await probe(page);
  await shot(page, "htf-off");

  await seed(page, true, P());
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  const on = await probe(page);
  const row = await legend();
  await shot(page, "htf-on-ribbon");

  await seed(page, true, P({ lines: false }));
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  const noLines = await probe(page);
  await shot(page, "htf-nolines");

  await seed(page, true, P({ tint: "wash" }));
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  await shot(page, "htf-wash");

  console.log({ off, on, noLines, row });
  check("off draws no gold", off.gold < 50, `${off.gold}px`);
  check("on draws the EMA lines", on.gold > 300, `${on.gold}px`);
  check("on draws the ribbon", on.ribbon > on.gold / 50 && on.ribbon > 200, `${on.ribbon}px`);
  check("off draws no ribbon", off.ribbon < on.ribbon / 5, `${off.ribbon}px`);
  check("lines off removes the gold", noLines.gold < on.gold / 5, `${noLines.gold}px`);
  check("lines off keeps the ribbon", noLines.ribbon > on.ribbon / 2, `${noLines.ribbon}px`);
  check("legend names frames + state", /HTF trend · 5m \+ 15m · (up|down|mixed)/.test(row), row);
  check("nothing threw", errors.length === 0, errors[0] ?? "");
} finally {
  await page
    .evaluate(() => {
      localStorage.removeItem("sim.resume.replay");
      localStorage.removeItem("chart.htfTrend");
    })
    .catch(() => {});
  await browser.close();
}

const bad = results.filter(([, ok]) => !ok).length;
console.log(`\n${results.length - bad}/${results.length} passed`);
process.exitCode = bad ? 1 : 0;
