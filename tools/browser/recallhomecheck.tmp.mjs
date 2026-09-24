// Does a touch client land on Recall when it opens the app — and only then?
//
// Three facts, none of which need the API to be up: the shell tags itself with
// the active workspace (`ws-lab` / `ws-journal`, Layout.tsx), so the route the
// router chose is readable without a single row of data.
//
//   1. touch + fresh load of "/"        -> /recall        (the launch redirect)
//   2. then tap Journal in the topbar   -> "/", Overview  (not permanent)
//   3. mouse + fresh load of "/"        -> "/", Overview  (desktop untouched)
//
// A plain viewport resize is NOT enough to test this: `(pointer: coarse)` only
// matches when the context is built with hasTouch/isMobile, which is why this
// makes its own context instead of using lib.mjs's 1600x900 desktop one.
import { chromium } from "playwright";

const BASE = process.env.APP_URL ?? "http://localhost:5173";

const shell = (page) =>
  page.evaluate(() => document.querySelector(".app-shell")?.className ?? "(no shell)");

const path = (page) => new URL(page.url()).pathname;

async function open(browser, opts, at = "/") {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  await page.goto(`${BASE}${at}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app-shell", { timeout: 20000 });
  // The redirect is an effect, so give the router a tick to run it.
  await page.waitForTimeout(600);
  return { ctx, page, errors };
}

const browser = await chromium.launch({ channel: "chrome", headless: true });
const fails = [];
const check = (name, got, want) => {
  const ok = got === want;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}\n        got  ${got}\n        want ${want}`);
  if (!ok) fails.push(name);
};

// ---- 1. the launch redirect -------------------------------------------------
const phone = await open(browser, {
  viewport: { width: 390, height: 844 },
  hasTouch: true,
  isMobile: true,
});
console.log(
  "coarse pointer seen by the app:",
  await phone.page.evaluate(() => window.matchMedia("(pointer: coarse)").matches),
);
check("phone launch lands on Recall", path(phone.page), "/recall");
check("phone launch shows the Lab shell", await shell(phone.page), "app-shell ws-lab");

// ---- 2. ...and is spent, not permanent --------------------------------------
await phone.page.getByRole("tab", { name: "Journal" }).click();
await phone.page.waitForTimeout(600);
check("tapping Journal still reaches Overview", path(phone.page), "/");
check("...on the Journal shell", await shell(phone.page), "app-shell ws-journal");

// A second trip away and back must also stay put.
await phone.page.getByRole("tab", { name: "Lab" }).click();
await phone.page.waitForTimeout(300);
await phone.page.getByRole("tab", { name: "Journal" }).click();
await phone.page.waitForTimeout(600);
check("and again on a later visit", path(phone.page), "/");

// ---- 3. tablet in landscape: wider than a laptop, still a fingertip ---------
const tablet = await open(browser, {
  viewport: { width: 1280, height: 800 },
  hasTouch: true,
  isMobile: true,
});
check("tablet landscape lands on Recall", path(tablet.page), "/recall");

// ---- 4. a deep link is a deep link, now and later ---------------------------
// Launching at /trades must not arm a redirect that fires whenever the user
// eventually taps through to Overview.
const deep = await open(
  browser,
  { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
  "/trades",
);
check("phone deep link stays put", path(deep.page), "/trades");
await deep.page.getByRole("tab", { name: "Lab" }).click();
await deep.page.waitForTimeout(300);
await deep.page.getByRole("tab", { name: "Journal" }).click();
await deep.page.waitForTimeout(600);
check("and Overview is reachable after it", path(deep.page), "/");

// ---- 5. desktop must be untouched ------------------------------------------
const desk = await open(browser, { viewport: { width: 1600, height: 900 } });
check("desktop launch stays on Overview", path(desk.page), "/");
check("...on the Journal shell", await shell(desk.page), "app-shell ws-journal");

const errors = [...phone.errors, ...tablet.errors, ...deep.errors, ...desk.errors];
if (errors.length) console.log("\npage errors:\n" + errors.map((e) => "  " + e).join("\n"));

await browser.close();
console.log(fails.length ? `\n${fails.length} FAILED: ${fails.join(", ")}` : "\nall passed");
process.exit(fails.length ? 1 : 0);
