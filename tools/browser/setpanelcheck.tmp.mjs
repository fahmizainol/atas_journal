// The indicator settings panel, after it stopped hanging under its row.
//
// The bug this is about is only visible on a phone: the legend can run twenty
// rows, it has no scroll of its own, and a panel anchored `top: 100%` off the
// last of those rows opened below the fold of the screen. Nothing could reach
// it — not scrolling (the legend is absolute over a canvas), not zooming.
//
// So the check is the one the eye would do: open the panel off the *last* row
// that has a "…", and ask whether it is on the screen and whether the pixel at
// its middle belongs to it (i.e. nothing is painted over it). Then the same on a
// desktop viewport, because the panel is centred at every width now and the
// desktop is where that is a change rather than a fix.
//
// Run: node tools/browser/setpanelcheck.tmp.mjs [--headed]
import { chromium } from "playwright";
import { shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const browser = await chromium.launch({ channel: "chrome", headless: !headed });

/** Open /charts and wait for ink on the biggest canvas. */
async function openCharts(page) {
  await page.goto(`${BASE}/charts/replay`, { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForSelector(".chart-legend", { timeout: 90000 });
  for (let i = 0; i < 160; i++) {
    const inked = await page.evaluate(() => {
      const c = [...document.querySelectorAll("canvas")].sort(
        (a, b) => b.width * b.height - a.width * a.height,
      )[0];
      if (!c || !c.width) return false;
      const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
      let n = 0;
      for (let i = 0; i < d.length; i += 64) if (d[i] !== d[0] || d[i + 1] !== d[1]) n++;
      return n > 200;
    });
    if (inked) break;
    await page.waitForTimeout(500);
  }
  await page.waitForTimeout(800);
}

/** Expand the layer list if it is collapsed (the open/closed state is persisted
 *  per pane, so it can come up either way). */
async function expandLegend(page) {
  const head = page.locator(".chart-legend-head").first();
  if ((await head.getAttribute("aria-expanded")) !== "true") await head.click();
  await page.waitForTimeout(250);
}

/** Where the panel is, and whether it owns the pixel at its own centre. */
async function readPanel(page) {
  return page.evaluate(() => {
    const p = document.querySelector(".chart-set");
    if (!p) return null;
    const r = p.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    return {
      top: Math.round(r.top),
      left: Math.round(r.left),
      bottom: Math.round(r.bottom),
      right: Math.round(r.right),
      w: Math.round(r.width),
      h: Math.round(r.height),
      vw: window.innerWidth,
      vh: window.innerHeight,
      // The pixel at the middle of the panel: ours, or something drawn over it?
      hitsPanel: !!(hit && p.contains(hit)),
      hitTag: hit ? `${hit.tagName.toLowerCase()}.${hit.className}`.slice(0, 60) : "none",
      title: p.querySelector(".chart-set-head span")?.textContent ?? "",
    };
  });
}

/** Open the settings of the LAST legend row that has a "…" — the row the old
 *  anchoring put off-screen — and assert the panel landed somewhere reachable. */
async function checkAt(page, tag) {
  await expandLegend(page);
  const dots = page.locator(".chart-legend-item .chart-legend-dots");
  const n = await dots.count();
  ok(`${tag}: rows with settings exist`, n > 1, `${n} "…" buttons`);
  const last = dots.nth(n - 1);
  const rowBox = await last.boundingBox();
  await last.click();
  await page.waitForSelector(".chart-set", { timeout: 5000 });
  await page.waitForTimeout(200);

  const p = await readPanel(page);
  ok(`${tag}: panel opened`, !!p, p ? `"${p.title}" ${p.w}x${p.h}` : "no .chart-set");
  if (!p) return;

  // The bug, stated as a fact: every edge inside the viewport.
  const inside = p.top >= 0 && p.left >= 0 && p.bottom <= p.vh && p.right <= p.vw;
  ok(
    `${tag}: fully on screen`,
    inside,
    `panel ${p.left},${p.top}→${p.right},${p.bottom} in ${p.vw}x${p.vh}`,
  );

  // Centred, not anchored: its middle is the screen's middle (±2px rounding),
  // and it is nowhere near the row that opened it when that row is low down.
  const dx = Math.abs((p.left + p.right) / 2 - p.vw / 2);
  const dy = Math.abs((p.top + p.bottom) / 2 - p.vh / 2);
  ok(`${tag}: centred on the viewport`, dx <= 2 && dy <= 2, `off-centre by ${dx},${dy}px`);
  ok(
    `${tag}: left the row behind`,
    !rowBox || Math.abs(p.top - (rowBox.y + rowBox.height)) > 8,
    `row bottom ${rowBox ? Math.round(rowBox.y + rowBox.height) : "?"} vs panel top ${p.top}`,
  );

  // Reachable, not just visible: the pixel at its middle is the panel's own.
  ok(`${tag}: nothing painted over it`, p.hitsPanel, `hit ${p.hitTag}`);

  // A press inside must not be read as a press outside — that click-away rule is
  // written against the row's wrapper, and the panel only moved visually.
  const head = page.locator(".chart-set .chart-set-head span").first();
  await head.click();
  await page.waitForTimeout(150);
  ok(`${tag}: a press inside keeps it open`, (await page.locator(".chart-set").count()) === 1);

  // And a press on the tape still closes it.
  await page.mouse.click(Math.round(p.vw * 0.5), Math.round(p.vh * 0.88));
  await page.waitForTimeout(200);
  ok(`${tag}: a press outside closes it`, (await page.locator(".chart-set").count()) === 0);
}

// ---- phone: the viewport the bug was reported on ----------------------------
{
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  await openCharts(page);
  await checkAt(page, "phone");
  await expandLegend(page);
  const dots = page.locator(".chart-legend-item .chart-legend-dots");
  await dots.nth((await dots.count()) - 1).click();
  await page.waitForSelector(".chart-set");
  console.log("  shot", await shot(page, "setpanel-phone"));
  ok("phone: no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await ctx.close();
}

// ---- desktop: the width where centring is a change, not a fix ---------------
{
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  await openCharts(page);
  await checkAt(page, "desktop");
  await expandLegend(page);
  const dots = page.locator(".chart-legend-item .chart-legend-dots");
  await dots.nth((await dots.count()) - 1).click();
  await page.waitForSelector(".chart-set");
  console.log("  shot", await shot(page, "setpanel-desktop"));
  ok("desktop: no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await ctx.close();
}

await browser.close();
console.log(fails.length ? `\nFAILED: ${fails.join(", ")}` : "\nall ok");
process.exit(fails.length ? 1 : 0);
