// Shared plumbing for the browser checks.
//
// Why this exists: the charts are canvas. lightweight-charts and every primitive
// in components/charts (VwapBandPrimitive, VolumeProfilePrimitive,
// OrdersPrimitive, the composite, the event bands) draw pixels — the DOM holds
// nothing about them. So neither tsc nor a DOM test can tell you whether a band
// rendered, rendered in the right place, or rendered at all. A real browser
// reading the canvas back is the only observable there is.
//
// Deliberately a script harness rather than a test suite: it is here so a change
// to chart code can be looked at, not so CI can go red. Assertions are coarse and
// about facts a screenshot would also show — "the surface is #000", "the canvas
// is not blank", "nothing threw" — because the fragile thing to assert about a
// canvas is exact pixels, and the useful thing is shape.
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const SHOTS = resolve(HERE, "shots");
export const BASE = process.env.APP_URL ?? "http://localhost:5173";

/** Launch the system Chrome. `channel` rather than Playwright's own bundle:
 *  /usr/bin/google-chrome is already on this machine, and downloading ~300MB of
 *  second browser to look at your own app is a poor trade. */
/** ISO seconds, the stamp format every account payload uses. */
export const iso = (d) => new Date(d).toISOString().replace(/\.\d{3}Z$/, "Z");

/**
 * A live, healthy replay account, as `/api/replays/account` returns one.
 *
 * Lives here rather than in the file that drives the account states, because it
 * is not only that file's: any check that opens `/charts/replay` and wants a
 * session with nothing owed has to stub this endpoint, and the moment there are
 * two copies one of them is a payload the app stopped serving. That already
 * happened — `rules` arrived with the account registry, `accountcheck` grew it,
 * `presetcheck`'s own copy did not, and the page crashed on a field it reads
 * unconditionally rather than failing a check.
 *
 * A factory, not a constant: callers mutate the result to make the states they
 * are after, and the two timestamps have to be relative to now.
 */
export const healthyAccount = () => ({
  now: iso(Date.now()),
  equity: 50_000,
  floor: 48_000,
  peak_close: 50_000,
  status: "live",
  day_net: 0,
  day_loss_remaining: 1_200,
  target_remaining: 3_000,
  // Real payloads carry this since `liveEquity` — the page indexes into it the
  // moment a sitting opens, so a stub without it is a crash, not a shortfall.
  counted_ids: [],
  cooldown_until: null,
  can_reset: false,
  // How the account's finished lives ended. The bar chip reads straight into it
  // (`fmtRecord`), so — like `counted_ids` and `rules` — a stub without it is a
  // render crash and every check downstream times out waiting for the chart.
  record: { passed: 0, blown: 0 },
  // Whether *this* life has passed. Null is "still going", which is the state a
  // healthy account is in.
  passed: null,
  review_flagged: null,
  epoch: { index: 0, started_at: iso(Date.now() - 864e5), sittings: 0, net: 0 },
  last_death: null,
  caps: { minis: 4, micros: 40 },
  // Which account answered. The chip draws itself off this, so a stub without
  // it would test the funded rendering twice.
  account: "funded",
  label: "LucidPro 50K",
  // Which tape day the day figures are about. Null is "no day open", which is
  // what the page gets before a session is picked.
  day: null,
  // The rule shape and this account's figures. Real payloads have carried this
  // since accounts became a registry, and the page reads into it for the floor
  // meter's full bar and for whether the floor is a constant — so a stub
  // without it is a crash, not a shortfall, exactly like `counted_ids` above.
  rules: {
    template: "lucid_pro",
    template_label: "LucidPro",
    trailing: "eod",
    // Whether the page may drive its own clock. True on every template that
    // prices a real product, so the transport is hidden — which is what the
    // funded page has always done, and is now read from here rather than from
    // a hardcoded test for the id.
    real_time: true,
    start: 50_000,
    max_loss: 2_000,
    trail_cap: 52_100,
    day_loss: 1_200,
    day_goal: null,
    profit_target: 3_000,
    max_minis: 4,
    max_micros: 40,
  },
});

export async function launch({ headed = false } = {}) {
  // HARNESS_CHANNEL=msedge when Chrome is not installed (same Chromium, same canvas).
  const browser = await chromium.launch({
    channel: process.env.HARNESS_CHANNEL ?? "chrome",
    headless: !headed,
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await ctx.newPage();
  // Everything the page complained about, kept for the check to report. A chart
  // that draws correctly while throwing on every frame is not a pass.
  const errors = [];
  page.on("console", (m) => {
    // The browser's own text for a bad response is "Failed to load resource",
    // with no URL — useless on its own, and the response listener below names it.
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) {
      errors.push(m.text());
    }
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 400) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
  return { browser, page, errors };
}

/**
 * Open a chart route and wait until it has actually drawn.
 *
 * `networkidle` is not enough on its own: the tape arrives, then the engine
 * builds bars, then the chart paints — so the page can be idle and blank. Waiting
 * for ink on the canvas is what "loaded" means here.
 *
 * It is also not always *possible*. A route holding an open stream — /charts/replay
 * mounts the page shell, which subscribes to the import feed and the live routing
 * SSE — never goes network-idle at all, so waiting for it burns the whole timeout
 * and then fails on a page that has been drawn and correct for minutes. Pass
 * `waitUntil: "domcontentloaded"` there; the legend and the ink are what the
 * function actually asserts, and they do not care how the bytes arrived.
 */
export async function openChart(page, route, { timeout = 60000, waitUntil = "networkidle" } = {}) {
  await page.goto(`${BASE}${route}`, { waitUntil, timeout });
  await page.waitForSelector(".chart-legend", { timeout });
  await page.waitForFunction(
    () => {
      const c = biggestCanvas();
      if (!c) return false;
      const d = c.getContext("2d").getImageData(0, 0, c.width, Math.min(c.height, 200)).data;
      // Any pixel unlike the top-left one means something was drawn.
      for (let i = 4; i < d.length; i += 4) {
        if (d[i] !== d[0] || d[i + 1] !== d[1] || d[i + 2] !== d[2]) return true;
      }
      return false;
      function biggestCanvas() {
        return [...document.querySelectorAll("canvas")].sort(
          (a, b) => b.width * b.height - a.width * a.height,
        )[0];
      }
    },
    null,
    { timeout },
  );
  // One frame of settle so late primitives (profiles, bands) are in the capture.
  await page.waitForTimeout(500);
}

/**
 * Read the price canvas back.
 *
 * `bg` is the most common colour on the canvas, which is the surface — the thing
 * the appearance preference actually changes. Modal rather than a corner pixel:
 * the corner is as likely to land on a grid line as on the background, and a
 * check that reports the grid colour half the time is worse than no check.
 *
 * `silhouette` is the first and last non-background row per sampled column. It is
 * the cheap way to ask "is the same thing still drawn in the same place": recolour
 * the chart and it must not move, so the silhouette is how a lost zoom or a reset
 * range gets caught. Sampled every 8px/2px because this runs in-page on a
 * ~1500x650 buffer and precision beyond that buys nothing.
 */
export async function probeChart(page) {
  return page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c) return { error: "no canvas" };
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const at = (x, y) => {
      const i = (y * c.width + x) * 4;
      return [d[i], d[i + 1], d[i + 2]];
    };
    const tally = new Map();
    for (let x = 0; x < c.width; x += 4) {
      for (let y = 0; y < c.height; y += 4) {
        const k = at(x, y).join(",");
        tally.set(k, (tally.get(k) ?? 0) + 1);
      }
    }
    const bg = [...tally.entries()].sort((a, b) => b[1] - a[1])[0][0].split(",").map(Number);
    const isBg = (p) =>
      Math.abs(p[0] - bg[0]) < 12 && Math.abs(p[1] - bg[1]) < 12 && Math.abs(p[2] - bg[2]) < 12;
    const sil = [];
    let ink = 0;
    for (let x = 0; x < c.width; x += 8) {
      let top = -1;
      let bot = -1;
      for (let y = 0; y < c.height; y += 2) {
        if (!isBg(at(x, y))) {
          if (top < 0) top = y;
          bot = y;
          ink++;
        }
      }
      sil.push(`${top}:${bot}`);
    }
    return { w: c.width, h: c.height, bg, ink, silhouette: sil.join(",") };
  });
}

/** Sample the volume gutter, which sits in the bottom fifth of the price canvas
 *  (scaleMargins top 0.82/0.85). Returns the distinct colours found there, so a
 *  check can ask whether the volume bars followed the candle scheme. */
export async function probeVolumeBand(page) {
  return page.evaluate(() => {
    const c = [...document.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    const y0 = Math.floor(c.height * 0.88);
    const d = c.getContext("2d").getImageData(0, y0, c.width, c.height - y0).data;
    const seen = new Map();
    for (let i = 0; i < d.length; i += 4) {
      const k = `${d[i]},${d[i + 1]},${d[i + 2]}`;
      seen.set(k, (seen.get(k) ?? 0) + 1);
    }
    return [...seen.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  });
}

export async function shot(page, name) {
  await mkdir(SHOTS, { recursive: true });
  const path = resolve(SHOTS, `${name}.png`);
  await page.screenshot({ path });
  return path;
}

/** The appearance panel's selects, opened off the indicator legend's header.
 *
 *  Named rather than positional. The panel grew a Preset row above the two
 *  knobs, which moved every index in the caller by one — and a positional read
 *  of a panel that gains rows is a check that starts asserting about the wrong
 *  control without failing first. */
export async function openAppearance(page) {
  const dots = page.locator(".chart-legend-item[data-ind-item='__appearance'] .chart-legend-dots");
  if ((await page.locator(".chart-set").count()) === 0) await dots.click();
  await page.waitForSelector(".chart-set");
  const row = (label) => page.locator(`.chart-set-row:has(.chart-set-label:text-is("${label}")) select`);
  return { preset: row("Preset"), surface: row("Background"), candles: row("Candles") };
}
