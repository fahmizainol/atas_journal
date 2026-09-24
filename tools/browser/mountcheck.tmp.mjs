// StrictMode double-mounts effects in dev; assert a page load still beats once.
// Fully stubbed — no request reaches PostHog.
import { chromium } from "playwright";
const b = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await b.newContext({
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
});
await ctx.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => false }));
const beats = [];
await ctx.route("**://*.posthog.com/**", async (route) => {
  const u = new URL(route.request().url());
  if (u.pathname.endsWith("config.js")) return route.fulfill({ status: 200, contentType: "application/javascript", body: "" });
  if (u.pathname.endsWith("/config")) return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  if (u.pathname.startsWith("/flags")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ featureFlags: {}, errorsWhileComputingFlags: false }) });
  let t = route.request().postData() ?? "";
  for (const e of JSON.parse(t)?.batch ?? []) if (e.event === "heartbeat") beats.push(e.timestamp);
  return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":1}' });
});
const page = await ctx.newPage();
await page.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(12000);
console.log(`beats on one mount: ${beats.length}`, beats);
console.log(beats.length === 1 ? "ok — StrictMode double-mount collapsed" : "FAIL");
await b.close();
process.exit(beats.length === 1 ? 0 : 1);
