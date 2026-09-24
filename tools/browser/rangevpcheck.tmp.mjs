// The fixed-range VP tool's two new behaviours, in a real browser.
//
// Neither is visible to tsc and neither has a server side to pytest: a profile
// dropped on the last bar has to keep taking in bars as the replay runs, and
// every profile has to draw the path its POC/VAH/VAL took across the slice.
//
//   node rangevpcheck.tmp.mjs
//   node rangevpcheck.tmp.mjs --headed
//
// The latch half is read out of `chart.drawings` rather than off the canvas.
// Bar times are what the tool actually stores, so the assertion is exact and
// survives the viewport scrolling under it — which it does, on every new bar,
// and which makes every pixel measurement of a box's position a measurement of
// two things at once. The chart has plenty of indigo in it besides this tool's
// chrome (the composite wash, the VWAP bands), so edge-finding by colour finds
// those instead.
//
// The trace half has to be pixels, since a path is only ever drawn. It is read
// as *distinct heights carrying the POC's gold* inside a window well within the
// box: a profile with no trace crosses that window once, at its POC. The same
// window is re-read with the profile deleted, so the reading is a delta against
// whatever else on this chart is gold rather than an absolute.
//
// It writes nothing — it draws profiles and steps bars, and neither touches the
// account or the journal.
import { launch, openChart, shot, SHOTS } from "./lib.mjs";

const REPLAY = "/charts/replay";
const STEPS = 6;
const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });

const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

// The replay draws a *random* day and then bookmarks the sitting, so the first
// load of a fresh profile picks the market and every load after it resumes that
// one. Without the throwaway load the two measurements below would be taken on
// two different days.
await openChart(page, REPLAY);
await openChart(page, REPLAY);
// `openChart` returns on the canvas, which is ready before the rail that floats
// over it has settled. Reaching for a tool button through the remount times out.
await page.waitForTimeout(1500);

const g = await page.evaluate(() => {
  const c = [...document.querySelectorAll("canvas")].sort(
    (a, b) => b.width * b.height - a.width * a.height,
  )[0];
  const r = c.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
});

/** The ranges this session has stored, in drop order. */
const stored = async () => {
  const raw = await page.evaluate(() => localStorage.getItem("chart.drawings"));
  const store = JSON.parse(raw ?? "{}");
  const key = Object.keys(store)[0];
  return key ? store[key].ranges : [];
};

/** Arm the tool and drag between two fractions of the canvas width.
 *
 *  Neither end may sit left of ~0.15: the tool rail's column overlays the canvas
 *  there, and a press that lands on it never reaches the chart — the drag
 *  silently does nothing and the tool stays armed, which is what the *next*
 *  drawRange then fails on. */
const drawRange = async (from, to) => {
  const show = page.locator('button[aria-label="Show the tools"]');
  if (await show.count()) await show.first().click();
  const tool = page.locator('button[aria-label="Fixed range VP"]').first();
  await tool.waitFor({ state: "visible" });
  await tool.evaluate((el) => el.click());
  const y = g.y + g.h * 0.5;
  await page.mouse.move(g.x + g.w * from, y);
  await page.mouse.down();
  // Two moves: the first has to clear DRAG_SLOP or the drop reads as a click and
  // the range is thrown away.
  await page.mouse.move(g.x + g.w * ((from + to) / 2), y, { steps: 4 });
  await page.mouse.move(g.x + g.w * to, y, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(300);
};

/** Distinct heights carrying the POC's gold, inside a canvas-local x window. */
const goldRows = (x1, x2) =>
  page.evaluate(
    ([a, b]) => {
      const c = [...document.querySelectorAll("canvas")].sort(
        (p, q) => q.width * q.height - p.width * p.height,
      )[0];
      const { width: W, height: H } = c;
      const d = c.getContext("2d").getImageData(0, 0, W, H).data;
      // #e0a52a at any alpha over a dark surface: red leads, green trails it,
      // blue well behind both.
      const gold = (x, y) => {
        const i = (y * W + x) * 4;
        const r = d[i];
        const gg = d[i + 1];
        const bl = d[i + 2];
        return r > 90 && r - bl > 45 && gg > bl && r - gg > 20;
      };
      const ys = new Set();
      for (let y = 0; y < H; y++) {
        let n = 0;
        for (let x = Math.max(0, a); x < Math.min(W, b); x++) if (gold(x, y)) n++;
        if (n >= 3) ys.add(y);
      }
      return ys.size;
    },
    [Math.round(x1), Math.round(x2)],
  );

const stepBars = async (n) => {
  for (let i = 0; i < n; i++) {
    await page.keyboard.press(".");
    await page.waitForTimeout(140);
  }
  await page.waitForTimeout(400);
};

// --- Two ranges: one dropped mid-chart, one dropped on the live edge --------
await drawRange(0.2, 0.42);
await drawRange(0.55, 0.995);

const before = await stored();
await shot(page, "rangevp-before");
check("both ranges stored", before.length === 2, `stored ${before.length}`);

if (before.length === 2) {
  const [fixed0, live0] = before;
  check("range dropped mid-chart did not latch", fixed0.live === false, `live=${fixed0.live}`);
  check("range dropped on the last bar latched", live0.live === true, `live=${live0.live}`);

  // A window well inside the latched box and clear of its right-edge labels.
  const winA = g.w * 0.6;
  const winB = winA + 220;
  const goldWith = await goldRows(winA, winB);

  // --- Does the latched one take in new bars? ------------------------------
  await stepBars(STEPS);
  // Drawing anything settles the tool, and a settle is what persists — so this
  // throwaway is how the two ranges above get written back with whatever `to`
  // they now hold. It is drawn left of the window the trace is read in.
  await drawRange(0.22, 0.28);
  const after = await stored();
  await shot(page, "rangevp-after");

  if (after.length >= 2) {
    const [fixed1, live1] = after;
    check(
      "latched range took in the new bars",
      live1.to > live0.to,
      `to ${live0.to} → ${live1.to} (+${live1.to - live0.to}s over ${STEPS} bars)`,
    );
    check(
      "latched range kept its left edge",
      live1.from === live0.from,
      `from ${live0.from} → ${live1.from}`,
    );
    check(
      "fixed range kept its span",
      fixed1.to === fixed0.to && fixed1.from === fixed0.from,
      `${fixed0.from}–${fixed0.to} → ${fixed1.from}–${fixed1.to}`,
    );
    check("latched range stayed latched", live1.live === true, `live=${live1.live}`);
  } else {
    check("both ranges survived the steps", false, `stored ${after.length}`);
  }

  // --- Is a developing trace actually drawn? -------------------------------
  // Read inside the box only, with no "profile removed" baseline to subtract:
  // the box lays a 10% indigo wash over everything under it, which shifts the
  // colour of every gold thing it covers — measured, the same window reads 56
  // gold heights bare and 9 under a box. A delta against bare is therefore a
  // measurement of the wash, not of the trace.
  //
  // So this is a floor, not a proof: with no trace the only gold inside the
  // window is the flat POC line (1-2 heights, and the same height in every
  // column). The crop beside it is the proof, and is meant to be looked at.
  await page.screenshot({
    path: `${SHOTS}/rangevp-trace-crop.png`,
    clip: { x: g.x + g.w * 0.55, y: g.y, width: g.w * 0.42, height: g.h },
  });
  check(
    "profile draws gold at many heights, not one flat POC",
    goldWith >= 4,
    `${goldWith} distinct gold heights in the box (a flat line is 1-2) — see shots/rangevp-trace-crop.png`,
  );
}

check("no console errors", errors.length === 0, errors.slice(0, 3).join(" | "));

await browser.close();
const failed = results.filter(([, ok]) => !ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
