// Throwaway: why does the usage heartbeat stop counting?
//
// Runs against the DEV server, because the dev server is the runtime — the
// tailnet URL proxies to :5173, and vite injects VITE_POSTHOG_KEY there, so the
// module is live in dev exactly as it is in a build.
//
//   node tools/browser/beatcheck.tmp.mjs
//
// Nothing reaches PostHog: every request to their host is answered locally.
//
// Two questions, one run:
//   1. Does it keep beating? Beat timestamps over four minutes.
//   2. Does using the app reach `window`? The idle bound is armed by listeners
//      on window, and anything that calls stopPropagation on the way up — React
//      handlers stop at the root container — never arms it. A page open longer
//      than IDLE_MS whose input never reaches window goes permanently silent
//      while looking, from the inside, like it is being used.
import { chromium } from "playwright";

const BASE = process.env.APP_URL || "http://127.0.0.1:5173";
const PATH = process.env.APP_PATH || "/charts";
const INPUT_EVENTS = ["pointerdown", "pointermove", "keydown", "wheel", "scroll"];

const browser = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await browser.newContext({
  userAgent:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
  viewport: { width: 1600, height: 950 },
});
// posthog-js drops anything it reads as a bot; answer both tells.
await ctx.addInitScript(() => {
  Object.defineProperty(navigator, "webdriver", { get: () => false });
});
// Mirror the module's own listeners. Same target, same phase, so whatever
// reaches these reaches its `touch`, and whatever is stopped short misses both.
await ctx.addInitScript((events) => {
  const hits = {};
  for (const e of events) {
    hits[e] = 0;
    window.addEventListener(e, () => (hits[e] += 1), { passive: true });
  }
  Object.defineProperty(window, "__inputHits", { get: () => hits });
}, INPUT_EVENTS);

const beats = [];
const t0 = Date.now();
await ctx.route("**://*.posthog.com/**", async (route) => {
  const u = new URL(route.request().url());
  if (u.pathname.endsWith("config.js"))
    return route.fulfill({ status: 200, contentType: "application/javascript", body: "" });
  if (u.pathname.endsWith("/config"))
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  if (u.pathname.startsWith("/flags"))
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ featureFlags: {}, errorsWhileComputingFlags: false }),
    });
  const body = route.request().postData() ?? "";
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch {
    parsed = null;
  }
  for (const e of parsed?.batch ?? [])
    if (e.event === "heartbeat")
      beats.push({ at: ((Date.now() - t0) / 1000).toFixed(1), section: e.properties?.section });
  return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":1}' });
});

const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 200)}`);
});
page.on("requestfailed", (r) => {
  if (/posthog/.test(r.url())) errors.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`);
});

await page.goto(`${BASE}${PATH}`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(8_000);

// --- question 2: does using the app arm the idle bound? ---
const box = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  if (!c) return null;
  const r = c.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
});
const before = await page.evaluate(() => ({ ...window.__inputHits }));

const gestures = [];
const sample = async (label, fn) => {
  const pre = await page.evaluate(() => ({ ...window.__inputHits }));
  await fn();
  await page.waitForTimeout(400);
  const post = await page.evaluate(() => ({ ...window.__inputHits }));
  const delta = {};
  for (const k of INPUT_EVENTS) if (post[k] - pre[k]) delta[k] = post[k] - pre[k];
  gestures.push([label, delta]);
};

if (box) {
  const cx = box.x + box.w / 2;
  const cy = box.y + box.h / 2;
  await sample("mouse move over chart", async () => {
    for (let i = 0; i < 12; i++) await page.mouse.move(cx - 120 + i * 20, cy - 40 + i * 6);
  });
  await sample("wheel over chart (zoom)", async () => {
    await page.mouse.move(cx, cy);
    await page.mouse.wheel(0, -300);
    await page.mouse.wheel(0, 200);
  });
  await sample("drag across chart (pan)", async () => {
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    for (let i = 0; i < 10; i++) await page.mouse.move(cx - i * 15, cy);
    await page.mouse.up();
  });
} else {
  gestures.push(["no <canvas> found on the page", {}]);
}
await sample("keypress", () => page.keyboard.press("Escape"));
await sample("mouse move over page chrome", async () => {
  for (let i = 0; i < 8; i++) await page.mouse.move(200 + i * 20, 18);
});

// --- question 1: does it keep beating? ---
const runFor = Number(process.env.RUN_MS || 245_000);
while (Date.now() - t0 < runFor) await page.waitForTimeout(1_000);

const after = await page.evaluate(() => ({ ...window.__inputHits }));

console.log(`\npage: ${BASE}${PATH}   watched ${((Date.now() - t0) / 1000).toFixed(0)}s\n`);
console.log(`beats: ${beats.length}`);
for (const b of beats) console.log(`   +${b.at}s   section=${b.section}`);
console.log(`\ndoes input reach window?  (canvas ${box ? "found" : "MISSING"})`);
for (const [label, delta] of gestures) {
  const keys = Object.keys(delta);
  console.log(`   ${keys.length ? " ok " : "MISS"}  ${label}: ${keys.length ? JSON.stringify(delta) : "nothing reached window"}`);
}
console.log(`\nwindow input totals: ${JSON.stringify(after)}  (at first gesture: ${JSON.stringify(before)})`);
if (errors.length) {
  console.log(`\nerrors (${errors.length}):`);
  for (const e of errors.slice(0, 12)) console.log(`   ${e}`);
}
await browser.close();
