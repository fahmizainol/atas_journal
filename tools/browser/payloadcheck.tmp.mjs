// Throwaway: what exactly does a heartbeat carry, and is it the only event?
// Fully stubbed — nothing reaches PostHog.
import { chromium } from "playwright";
import { gunzipSync } from "node:zlib";

const b = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await b.newContext({
  userAgent:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
  viewport: { width: 1600, height: 950 },
});
await ctx.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => false }));

const seen = [];
await ctx.route("**://*.posthog.com/**", async (route) => {
  const u = new URL(route.request().url());
  if (u.pathname.endsWith("config.js"))
    return route.fulfill({ status: 200, contentType: "application/javascript", body: "" });
  if (u.pathname.endsWith("/config"))
    // Answer the way the real project does, so anything it switches on shows up.
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        analytics: { endpoint: "/i/v0/e/" },
        autocaptureExceptions: true,
        autocapture_opt_out: false,
        captureDeadClicks: true,
        capturePerformance: { network_timing: true, web_vitals: true },
        supportedCompression: ["gzip-js"],
      }),
    });
  if (u.pathname.startsWith("/flags"))
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ featureFlags: {}, errorsWhileComputingFlags: false }),
    });
  // The stub advertises gzip, so read the buffer and unwrap it.
  let parsed = null;
  const buf = route.request().postDataBuffer();
  if (buf) {
    let text;
    try {
      text = gunzipSync(buf).toString("utf8");
    } catch {
      text = buf.toString("utf8");
    }
    if (text.startsWith("data=")) {
      try {
        text = Buffer.from(decodeURIComponent(text.slice(5)), "base64").toString("utf8");
      } catch {
        /* not base64 */
      }
    }
    try {
      parsed = JSON.parse(text);
    } catch {
      /* not json */
    }
  }
  for (const e of parsed?.batch ?? []) seen.push(e);
  return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":1}' });
});

const page = await ctx.newPage();
await page.goto("http://127.0.0.1:5173/charts/replay", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(6_000);
// Click and scroll a bit — if autocapture or dead clicks were live, this is what
// would produce them.
await page.mouse.move(800, 500);
await page.mouse.click(800, 500);
await page.mouse.wheel(0, 300);
await page.waitForTimeout(20_000);

console.log(`events sent: ${seen.length}`);
for (const e of seen) console.log(`  · ${e.event}`);

const hb = seen.find((e) => e.event === "heartbeat");
if (hb) {
  const props = Object.entries(hb.properties ?? {}).sort(([a], [b2]) => a.localeCompare(b2));
  console.log(`\nheartbeat carries ${props.length} properties:\n`);
  for (const [k, v] of props) {
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    console.log(`  ${k.padEnd(28)} ${s.length > 70 ? s.slice(0, 70) + "…" : s}`);
  }
  console.log(`\ntop-level keys: ${Object.keys(hb).join(", ")}`);
}
await b.close();
