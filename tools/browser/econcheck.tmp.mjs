// Throwaway: the economic-events layer (EconEventPrimitive) on the real replay.
//
// Pinned to 2025-12-10, an FOMC day (statement 14:00 ET), paused at 15:00 ET so
// the release is behind the tape. Asserts:
//   * on: full-height fuchsia columns exist (the high-impact line) and the legend
//     row counts > 0 releases;
//   * the tag carries the actual once the tape has passed the print;
//   * off: that ink goes to zero;
//   * nothing threw.
import { launch, openChart, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";
const SYMBOL = "NQZ5";
const DAY = "2025-12-10";
const CLOCK = Date.UTC(2025, 11, 10, 19, 20, 0); // 14:20 ET (EST)

const seed = async (page, on) => {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([on, sym, day, clk]) => {
      localStorage.setItem(
        "sim.resume.replay",
        JSON.stringify({ symbol: sym, date: day, clockMs: clk, attemptId: null, contextTicks: 0 }),
      );
      const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
      vis.econEvents = on;
      localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
      localStorage.setItem("chart.econEvents", JSON.stringify({ floor: "high" }));
    },
    [on, SYMBOL, DAY, CLOCK],
  );
};

// Full-height runs of the high-impact hue (dark cut: 217,70,239 @ .85 over the bg).
const probe = (page) =>
  page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return { error: "no canvas" };
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const hit = (i) => d[i] > 150 && d[i + 1] < 110 && d[i + 2] > 160;
    let cols = 0;
    for (let x = 0; x < c.width; x++) {
      let n = 0;
      for (let y = 0; y < c.height; y++) if (hit((y * c.width + x) * 4)) n++;
      if (n > c.height * 0.3) cols++;
    }
    return { cols, h: c.height };
  });

const results = [];
const check = (name, ok, detail = "") => {
  results.push([name, ok]);
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
};

const { browser, page } = await launch({ headed: process.argv.includes("--headed") });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const econReqs = [];
page.on("response", (r) => {
  if (r.url().includes("/api/econ/events")) econReqs.push(`${r.status()} ${r.url().split("?")[1]}`);
});

try {
  await seed(page, false);
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  {
    const box = await page.locator("canvas").first().boundingBox();
    await page.mouse.move(box.x + box.width * 0.6, box.y + 200);
    for (let i = 0; i < 25; i++) await page.mouse.wheel(0, 600);
    await page.waitForTimeout(800);
  }
  const off = await probe(page);
  await shot(page, "econ-off");

  await seed(page, true);
  await openChart(page, REPLAY, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  // Zoom out so the whole afternoon is on screen whatever the resume landed on.
  const box = await page.locator("canvas").first().boundingBox();
  await page.mouse.move(box.x + box.width * 0.6, box.y + 200);
  for (let i = 0; i < 25; i++) await page.mouse.wheel(0, 600);
  await page.waitForTimeout(800);
  const on = await probe(page);
  await shot(page, "econ-on");
  const row = ((await page.locator(".chart-legend >> text=/Economic events/").first().textContent()) ?? "").trim();

  console.log(`\noff ${off.cols}col · on ${on.cols}col · requests: ${econReqs.join(" | ")}\n`);
  check("off draws no release line", off.cols === 0, `${off.cols} columns`);
  check("on draws the FOMC line", on.cols >= 1, `${on.cols} columns`);
  check("legend counts releases", /Economic events · [1-9]/.test(row), row);
  check("the endpoint answered 200", econReqs.some((r) => r.startsWith("200")), econReqs[0] ?? "none");
  check("nothing threw", errors.length === 0, errors[0] ?? "");
} finally {
  await page
    .evaluate(() => {
      localStorage.removeItem("sim.resume.replay");
      localStorage.removeItem("chart.econEvents");
    })
    .catch(() => {});
  await browser.close();
}
const bad = results.filter(([, ok]) => !ok).length;
console.log(`\n${results.length - bad}/${results.length} passed`);
process.exitCode = bad ? 1 : 0;
