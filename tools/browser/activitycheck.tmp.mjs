// Throwaway: does the usage heartbeat actually beat, and does it say where?
//
// Runs against a PRODUCTION build served by vite preview, because the key is
// inlined at build time — a dev server started without VITE_POSTHOG_KEY has the
// whole module compiled out, so :5173 can never exercise this.
//
//   pnpm --dir frontend build          (with VITE_POSTHOG_KEY set)
//   pnpm --dir frontend exec vite preview --config vite.preview.config.ts
//   node tools/browser/activitycheck.tmp.mjs
//
// Nothing reaches PostHog: every request to their host is intercepted and
// answered locally, and the bodies are what this asserts on.
import { chromium } from "playwright";
import { gunzipSync } from "node:zlib";

const BASE = process.env.APP_URL || "http://localhost:4300";
const PING_MS = 60_000;

/** posthog-js gzips its batch and may form-encode it; unwrap whichever. */
function payload(req) {
  const buf = req.postDataBuffer();
  if (!buf) return null;
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
      /* not base64 after all */
    }
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function session(fn, { hidden = false } = {}) {
  // System Chrome, same as lib.mjs — Playwright's own bundle isn't installed.
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  // posthog-js drops every event it thinks came from a bot, and an automated
  // browser is one twice over: "headlesschrome" is on its user-agent blocklist,
  // and `navigator.webdriver` alone is enough on its own (see Bo() in the
  // bundle). Both have to be answered or the library never sends anything and
  // this file measures nothing. It stands in for the desktop Chrome the app is
  // actually used from, so give it that browser's answers.
  const ctx = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
  });
  await ctx.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => false });
  });
  const beats = [];
  await ctx.route("**://*.posthog.com/**", async (route) => {
    const url = new URL(route.request().url());
    // Let init finish: posthog fetches its remote config and flags before it
    // will flush anything, so these have to answer plausibly rather than be
    // swallowed along with the captures.
    if (url.pathname.endsWith("config.js")) {
      return route.fulfill({ status: 200, contentType: "application/javascript", body: "" });
    }
    if (url.pathname.endsWith("/config")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (url.pathname.startsWith("/flags")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ featureFlags: {}, errorsWhileComputingFlags: false }),
      });
    }
    // /e/ carries {api_key, batch:[event, ...]} — posthog batches even one event.
    const body = payload(route.request());
    const events = Array.isArray(body) ? body : (body?.batch ?? [body]);
    for (const e of events) if (e && e.event === "heartbeat") beats.push(e);
    await route.fulfill({ status: 200, contentType: "application/json", body: '{"status":1}' });
  });
  if (hidden) {
    // Beat time reads document.visibilityState, so this has to be in place
    // before the app mounts.
    await ctx.addInitScript(() => {
      Object.defineProperty(document, "visibilityState", { get: () => "hidden" });
      Object.defineProperty(document, "hidden", { get: () => true });
    });
  }
  const page = await ctx.newPage();
  try {
    return await fn(page, beats);
  } finally {
    await browser.close();
  }
}

const waitFor = async (beats, n, ms) => {
  const until = Date.now() + ms;
  while (Date.now() < until && beats.length < n) await new Promise((r) => setTimeout(r, 250));
  return beats.length >= n;
};

const results = [];
const check = (label, ok, detail = "") => results.push([label, ok, detail]);

// 1 & 2 — it beats on mount, tagged with where you are; and a client-side
// navigation retags the next beat without restarting the timer.
await session(async (page, beats) => {
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  const got = await waitFor(beats, 1, 20_000);
  check("beats on mount", got, got ? "" : "no heartbeat in 20s");
  const first = beats[0]?.properties ?? {};
  check(
    `first beat says where (workspace=${first.workspace} section=${first.section})`,
    first.workspace === "journal" && first.section === "/",
  );

  // Navigate well INTO the interval, not at the start of it. Clicking straight
  // after mount would make "beat 60s after mount" and "beat 60s after the click"
  // the same instant, and the check below could not tell them apart.
  await page.waitForTimeout(35_000);
  const t0 = Date.now();
  await page.getByRole("link", { name: "Calendar" }).first().click();
  await page.waitForURL("**/calendar**", { timeout: 10_000 });

  const second = await waitFor(beats, 2, PING_MS + 25_000);
  const elapsed = Date.now() - t0;
  check("second beat arrives", second, second ? "" : "none within a full interval + 25s");
  const p = beats[1]?.properties ?? {};
  check(
    `next beat retags to the new route (section=${p.section})`,
    p.section === "/calendar" && p.workspace === "journal",
  );
  // The beat is due ~25s after the click. A timer restarted by navigation would
  // instead pay a full PING_MS from here, so anything under half an interval
  // proves the route is read through a ref rather than closed over.
  check(
    `navigation did not restart the interval (${(elapsed / 1000).toFixed(0)}s after nav)`,
    second && elapsed < PING_MS / 2,
    second ? "" : "n/a",
  );
});

// 3 — a hidden tab is not time spent.
await session(
  async (page, beats) => {
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(15_000);
    check("hidden tab never beats", beats.length === 0, `${beats.length} beat(s)`);
  },
  { hidden: true },
);

let bad = 0;
for (const [label, ok, detail] of results) {
  if (!ok) bad++;
  console.log(`${ok ? "  ok" : "FAIL"}  ${label}${detail ? `  — ${detail}` : ""}`);
}
console.log(bad ? `\n${bad} failed` : "\nall passed");
process.exit(bad ? 1 : 0);
