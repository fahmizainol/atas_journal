// Does a heartbeat actually reach PostHog? No stubbing — real network, real key.
import { chromium } from "playwright";
const b = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await b.newContext({
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
});
await ctx.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => false }));
const page = await ctx.newPage();
page.on("request", (r) => { if (r.url().includes("posthog.com")) console.log("[>]", r.method(), r.url().slice(0, 90)); });
page.on("response", async (r) => {
  if (!r.url().includes("posthog.com")) return;
  let body = ""; try { body = (await r.text()).slice(0, 160); } catch {}
  console.log("[<]", r.status(), r.url().slice(0, 70), body);
});
page.on("requestfailed", (r) => { if (r.url().includes("posthog.com")) console.log("[x]", r.url().slice(0, 70), r.failure()?.errorText); });
const started = new Date();
await page.goto("http://127.0.0.1:5173/charts/replay?mode=all", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(Number(process.env.RUN_MS || 15000));
console.log(`\nwindow: ${started.toISOString()} .. ${new Date().toISOString()}`);
await b.close();
