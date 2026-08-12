// Does a playing chart's cost scale with its width?
//
// This is the question a split-pane layout turns on. If the chart's JS cost is
// proportional to the pixels (i.e. to the bars on screen), then cutting the
// window in two and putting a chart in each half costs about what one full-width
// chart costs today, and multi-pane is nearly free. If the cost is flat per
// instance, two panes cost twice one, whatever their size.
//
// Reads ScriptDuration rather than fps: JS is the part a GPU can't take away,
// and this harness rasterizes in SwiftShader so its paint numbers are
// pessimistic in a way the user's machine is not.
//
//   APP_URL=http://localhost:4300 node renderscale.mjs
import { launch, openChart } from "./lib.mjs";

const WIDTHS = [1600, 1100, 800, 560];
const WINDOW = 10;

for (const width of WIDTHS) {
  const { browser, page, errors } = await launch({ headed: false });
  try {
    await page.setViewportSize({ width, height: 900 });
    await openChart(page, "/charts/replay");
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Performance.enable");
    const read = async () => {
      const { metrics } = await cdp.send("Performance.getMetrics");
      const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
      return { ts: m.Timestamp, task: m.TaskDuration, script: m.ScriptDuration };
    };
    await page.keyboard.press("k");
    await page.waitForTimeout(800);
    const a = await read();
    await page.waitForTimeout(WINDOW * 1000);
    const b = await read();
    const wall = b.ts - a.ts;
    const bars = await page.evaluate(() => {
      const c = document.querySelector(".sim-chart canvas");
      return c ? c.getBoundingClientRect().width : null;
    });
    console.log(
      `${String(width).padStart(5)}px viewport (chart ${bars ? Math.round(bars) : "?"}px)  ` +
        `script ${(((b.script - a.script) / wall) * 100).toFixed(1).padStart(5)}%   ` +
        `task ${(((b.task - a.task) / wall) * 100).toFixed(1).padStart(5)}%` +
        (errors.length ? `   [${errors.length} console errors]` : ""),
    );
  } finally {
    await browser.close();
  }
}
