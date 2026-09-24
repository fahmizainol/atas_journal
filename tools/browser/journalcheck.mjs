// The Journal's charts, after the /charts layers were ported onto them.
//
// What this asks, in the order the layers were added:
//
//   1. the day chart opens and its legend lists the new rows by name — the
//      weekly VWAP and weekly VA (which were silently absent until the roll-root
//      fix in api/session_chart), the session VP and its nodes, the composite and
//      its nodes, the vol ruler, and Modern VWAP;
//   2. the composite's gutter actually paints. It has no context bars to hang
//      itself on here, so it falls back to the third gutter in from the right
//      (CompositeProfilePrimitive.span) — a fallback nothing else exercises, and
//      the one thing about this port that could silently draw nothing;
//   3. the vol ruler claims a pane, and hiding it gives the pane back;
//   4. the ƒ picker opens with the community half alone — this chart passes no
//      app layers, and a head with nothing under it would read as a failed load;
//   5. the trade chart carries the same rows, since it is the same component on
//      the same payload.
//
// Run: node tools/browser/journalcheck.mjs [--headed]
import { launch, openChart, probeChart, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");

/** A day the journal has trades on *and* the tick cache holds. Overridable, since
 *  which days are on disk is a property of this machine. */
const DAY = process.env.JOURNAL_DAY ?? "2026-06-30";
/** The FilterBar scope the day belongs to, carried in the querystring the way the
 *  shell's own links do. The default day is a replay sitting; the live sessions
 *  are 2026 MNQ, whose ticks were never bought. */
const MODES = process.env.JOURNAL_MODES ?? "replay";

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

/** Every legend row's text, expanded. */
async function legendRows(page) {
  // The list collapses behind its header; open it if it is shut.
  const head = page.locator(".chart-legend-head").first();
  const expanded = await head.getAttribute("aria-expanded");
  if (expanded !== "true") await head.click();
  await page.waitForTimeout(150);
  return page.$$eval(".chart-legend-row", (els) => els.map((e) => e.textContent.trim()));
}

/** Colours found in a vertical strip of the price canvas, as a share of it —
 *  how the composite's gutter is asked whether it painted. `x0`/`x1` are
 *  fractions of the canvas width. */
async function stripInk(page, x0, x1) {
  return page.evaluate(
    ([a, b]) => {
      const c = [...document.querySelectorAll("canvas")].sort(
        (p, q) => q.width * q.height - p.width * p.height,
      )[0];
      const ctx = c.getContext("2d");
      const x = Math.floor(c.width * a);
      const w = Math.max(1, Math.floor(c.width * (b - a)));
      const d = ctx.getImageData(x, 0, w, c.height).data;
      const tally = new Map();
      for (let i = 0; i < d.length; i += 4) {
        const k = `${d[i]},${d[i + 1]},${d[i + 2]}`;
        tally.set(k, (tally.get(k) ?? 0) + 1);
      }
      const sorted = [...tally.entries()].sort((p, q) => q[1] - p[1]);
      const bg = sorted[0][0].split(",").map(Number);
      let off = 0;
      for (const [k, n] of sorted) {
        const p = k.split(",").map(Number);
        if (
          Math.abs(p[0] - bg[0]) >= 12 ||
          Math.abs(p[1] - bg[1]) >= 12 ||
          Math.abs(p[2] - bg[2]) >= 12
        )
          off += n;
      }
      return { bg, offBgShare: off / (d.length / 4) };
    },
    [x0, x1],
  );
}

const paneCount = (page) => page.$$eval("canvas", (cs) => cs.length);

const { browser, page } = await launch({ headed });
try {
  console.log(`day chart — ${BASE}/calendar/${DAY}?mode=${MODES}`);
  await openChart(page, `/calendar/${DAY}?mode=${MODES}`);
  await shot(page, "journal-day");

  const rows = (await legendRows(page)).join(" | ");
  console.log(`  legend: ${rows}`);
  for (const [name, needle] of [
    ["weekly VWAP row", "VWAP · Weekly"],
    ["weekly VA row", "Developing VA · Weekly"],
    ["session VP row", "Session VP · NY"],
    ["session VP nodes row", "Session VP nodes"],
    ["composite row", "Composite VP"],
    ["vol ruler row", "Vol ruler"],
    ["Modern VWAP row", "Modern VWAP"],
  ]) {
    ok(name, rows.includes(needle));
  }

  // The three gutters, right to left: the viewport profile owns the last 11% of
  // the pane, the session's VP the 11% before it, and the composite the 11%
  // before that (CompositeProfilePrimitive.GUTTER_FRAC, two 7px gaps). Sampled
  // just inside each so a pixel of rounding can't move the answer.
  const COMPOSITE_STRIP = [0.68, 0.76];
  const SESSION_STRIP = [0.79, 0.87];
  const gutter = await stripInk(page, ...COMPOSITE_STRIP);
  const sessionGutter = await stripInk(page, ...SESSION_STRIP);
  ok(
    "composite gutter paints",
    gutter.offBgShare > 0.02,
    `${(gutter.offBgShare * 100).toFixed(1)}% off-background`,
  );
  ok(
    "session VP gutter paints",
    sessionGutter.offBgShare > 0.02,
    `${(sessionGutter.offBgShare * 100).toFixed(1)}% off-background`,
  );

  // The vol ruler owns a pane. Hiding it must give the pane back — the layer
  // creates and destroys its series rather than hiding them, because hiding the
  // last series of a pane leaves an empty pane behind.
  const withRuler = await paneCount(page);
  const rulerRow = page.locator(".chart-legend-row", { hasText: "Vol ruler" }).first();
  await rulerRow.click();
  await page.waitForTimeout(400);
  const withoutRuler = await paneCount(page);
  ok("vol ruler pane goes on hide", withoutRuler < withRuler, `${withRuler} → ${withoutRuler}`);
  await rulerRow.click();
  await page.waitForTimeout(400);
  ok("vol ruler pane comes back", (await paneCount(page)) === withRuler);

  // The composite's rule, behind its row's "…". Turning it re-derives the
  // composite in place — a repaint, never a rebuild — so the row's own label is
  // the observable: it quotes how many days the rule kept.
  const compRow = page.locator(".chart-legend-item", { hasText: "Composite VP" }).first();
  await compRow.locator(".chart-legend-dots").click();
  await page.waitForSelector(".ind-settings, .chart-legend-item select", { timeout: 10000 });
  const ruleSel = compRow.locator("select").first();
  await ruleSel.selectOption("days");
  await page.waitForTimeout(600);
  const afterDays = (await legendRows(page)).find((r) => r.includes("Composite VP")) ?? "";
  ok("composite rule re-derives", afterDays.includes("5 prior sessions"), afterDays);
  await ruleSel.selectOption("off");
  await page.waitForTimeout(600);
  const afterOff = (await legendRows(page)).find((r) => r.includes("Composite VP")) ?? "";
  ok("composite rule off is reported", afterOff.includes("off"), afterOff);
  // The candles run through this strip too, so it never empties — what has to
  // move is the share, by more than a repaint's worth of noise.
  const gutterOff = await stripInk(page, ...COMPOSITE_STRIP);
  ok(
    "composite gutter clears when the rule is off",
    gutterOff.offBgShare < gutter.offBgShare - 0.03,
    `${(gutter.offBgShare * 100).toFixed(1)}% → ${(gutterOff.offBgShare * 100).toFixed(1)}%`,
  );
  await ruleSel.selectOption("balance");
  await page.waitForTimeout(600);
  await page.keyboard.press("Escape");

  // The ƒ picker: community catalogue only on this chart.
  await page.locator(".study-pick button").first().click();
  await page.waitForSelector(".study-pop", { timeout: 10000 });
  await page.waitForTimeout(1200); // the catalogue is a 1.8 MB import
  const heads = await page.$$eval(".study-head", (els) => els.map((e) => e.textContent.trim()));
  console.log(`  picker heads: ${heads.join(" | ")}`);
  ok("picker offers the catalogue", heads.some((h) => h.startsWith("Community")));
  ok("picker hides the app-layer half", !heads.some((h) => h.includes("This chart")));
  await shot(page, "journal-picker");
  await page.keyboard.press("Escape");

  // The chart still draws after all that.
  const probe = await probeChart(page);
  ok("chart still drawn", probe.ink > 1000, `${probe.ink} ink samples`);

  // The trade detail's chart is the day replayer scoped to the one trade — the
  // same panel as the day view's, already armed on the trade.
  console.log(`\ntrade replay — ${BASE}/trades?mode=${MODES}`);
  await page.goto(`${BASE}/trades?mode=${MODES}`, { waitUntil: "networkidle" });
  await page.locator("table tbody tr").first().click();
  // Open by default — the toggle exists only to shed the tape's cost.
  await page.waitForSelector(".chart-legend", { timeout: 60000 });
  await page.waitForTimeout(1500);
  await shot(page, "journal-trade");
  const trows = (await legendRows(page)).join(" | ");
  console.log(`  legend: ${trows}`);
  // The bucketing sits in the legend head, not in a row.
  const tfLabel = await page.$eval(".chart-legend .tf", (e) => e.textContent.trim());
  ok("trade replay opens on 500t", tfLabel.includes("500t"), tfLabel);
  ok("trade replay has a transport", (await page.$(".sim-transport")) !== null);
} catch (e) {
  console.error(e);
  await shot(page, "journal-error");
  fails.push(`threw: ${e.message}`);
} finally {
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} failed: ${fails.join(", ")}` : "\nall ok");
process.exit(fails.length ? 1 : 0);
