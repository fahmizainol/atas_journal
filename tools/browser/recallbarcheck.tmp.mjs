// Recall as its own chrome-less workspace. The rep should read like an Anki
// card: a 36px bar, then the tape taking everything left, then the transport
// under it, then the one row that ends the rep — Flip on the front, the four
// ratings on the back — sitting last, at the bottom of the screen.
//
// The layout claim being tested is that the page is exactly one viewport tall
// and never scrolls, which is what lets that last row be pinned without being
// positioned. So: order top-to-bottom, no scroll on either face, and the chart
// bigger than the 456px it got as a Lab tab.
//
// Supersedes recallphone.tmp.mjs, whose phone assertions (no sideways scroll,
// chart spans the screen, tool rail folded) are folded in below.
//
// Nothing is rated: a rating writes a rep and reschedules the card.
//
// Run: node tools/browser/recallbarcheck.tmp.mjs [--headed]
import { chromium } from "playwright";
import { shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const browser = await chromium.launch({ channel: "chrome", headless: !headed });

async function openRecall(page) {
  await page.goto(`${BASE}/recall`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("[data-recall-chart] canvas", { timeout: 60000 });
  await page.waitForTimeout(1200);
}

/** Vertical extents of the four rows that make up a rep. */
const rows = (page, actionSel) =>
  page.evaluate((sel) => {
    const box = (s) => {
      const e = document.querySelector(s);
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, h: r.height };
    };
    const btn = document.querySelector(sel);
    const de = document.scrollingElement;
    return {
      bar: box(".chart-topbar"),
      chart: box("[data-recall-chart]"),
      transport: box("[data-recall-transport]"),
      actions: btn ? box(".recall-actions") : null,
      vh: window.innerHeight,
      vw: window.innerWidth,
      scrollH: de.scrollHeight,
      clientH: de.clientHeight,
      scrollW: de.scrollWidth,
      clientW: de.clientWidth,
      actionsPos: btn ? getComputedStyle(btn.closest(".recall-actions")).position : null,
      btnH: btn ? btn.getBoundingClientRect().height : 0,
    };
  }, actionSel);

/** The order the page is meant to read in, top to bottom. */
function checkOrder(r, face) {
  ok(`${face}: bar is at the very top`, r.bar && r.bar.top <= 1,
    r.bar && `top ${Math.round(r.bar.top)}`);
  ok(`${face}: chart sits under the bar`, r.chart.top >= r.bar.bottom - 1,
    `chart ${Math.round(r.chart.top)} vs bar ${Math.round(r.bar.bottom)}`);
  ok(`${face}: transport sits under the chart`, r.transport.top >= r.chart.bottom - 1,
    `transport ${Math.round(r.transport.top)} vs chart ${Math.round(r.chart.bottom)}`);
  ok(`${face}: page never scrolls`, r.scrollH <= r.clientH + 1,
    `${r.scrollH} vs ${r.clientH}`);
}

// ---- phone -----------------------------------------------------------------
{
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 3,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  await openRecall(page);

  ok("the shell draws no chrome", (await page.locator(".app-topbar").count()) === 0);
  ok("the shell is marked chromeless", (await page.locator(".app-shell.chromeless").count()) === 1);
  ok("the page carries its own 36px bar",
    Math.round(await page.locator(".chart-topbar").evaluate((e) => e.getBoundingClientRect().height)) === 36);
  ok("the deck stats moved into the bar",
    (await page.locator(".chart-topbar [data-recall-stat='due']").count()) === 1);
  ok("the card identity moved into the bar",
    (await page.locator(".chart-topbar [data-recall-card]").count()) === 1);

  const front = await rows(page, "[data-recall-flip]");
  checkOrder(front, "front");
  ok("front: the transport is above Flip", front.transport.bottom <= front.actions.top + 1,
    `transport ends ${Math.round(front.transport.bottom)}, Flip starts ${Math.round(front.actions.top)}`);
  ok("front: Flip is the bottom-most row", Math.abs(front.actions.bottom - front.vh) <= 10,
    `bottom ${Math.round(front.actions.bottom)} of ${front.vh}`);
  ok("front: Flip spans the screen", front.actions.left <= 9 && front.actions.right >= front.vw - 9,
    `${Math.round(front.actions.left)}…${Math.round(front.actions.right)} of ${front.vw}`);
  ok("front: Flip is a thumb-sized target", front.btnH >= 30, `${Math.round(front.btnH)}px tall`);
  ok("front: Flip is not fixed-positioned", front.actionsPos !== "fixed", front.actionsPos);

  ok("the chart spans the screen", front.chart.right - front.chart.left >= front.vw - 1,
    `${Math.round(front.chart.right - front.chart.left)}px of ${front.vw}`);
  ok("the chart is much bigger than it was in Lab", front.chart.h > 520,
    `${Math.round(front.chart.h)}px (was 456)`);
  ok("no sideways scroll", front.scrollW <= front.clientW + 1, `${front.scrollW} vs ${front.clientW}`);
  ok("tool rail starts folded",
    (await page.locator("[data-recall-chart] .chart-tools.folded").count()) === 1);

  // With no shell chrome, ☰ is the only way off this page — and it has to list
  // Recall as its own workspace, not as a Lab tab.
  await page.locator(".chart-topbar .nav-menu-btn").click();
  await page.waitForSelector(".nav-menu-pop", { timeout: 5000 });
  const nav = await page.evaluate(() => {
    const heads = [...document.querySelectorAll(".nav-menu-head")].map((e) => e.textContent);
    const here = document.querySelector(".nav-menu-item.here")?.textContent ?? "";
    const r = document.querySelector(".nav-menu-pop").getBoundingClientRect();
    return { heads, here, onScreen: r.left >= 0 && r.top >= 0 && r.right <= innerWidth };
  });
  ok("☰ opens on screen", nav.onScreen);
  ok("Recall is its own workspace in the menu", nav.heads.includes("Recall"), nav.heads.join(" | "));
  ok("Lab no longer lists Recall",
    !nav.heads.includes("Recall") || nav.heads.indexOf("Recall") === nav.heads.length - 1);
  ok("the menu knows where you are", nav.here.startsWith("Recall"), nav.here);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);

  ok("no page errors", errors.length === 0, errors[0] ?? "");
  await shot(page, "recallbar-1-front");

  // ---- the back face -------------------------------------------------------
  await page.locator("[data-recall-flip]").click();
  await page.waitForSelector("[data-recall-ratings] button", { timeout: 30000 });
  await page.waitForTimeout(500);

  const back = await rows(page, "[data-recall-rate]");
  checkOrder(back, "back");
  ok("back: the ratings sit where Flip did", Math.abs(back.actions.bottom - back.vh) <= 10,
    `bottom ${Math.round(back.actions.bottom)} of ${back.vh}`);
  ok("back: the ratings span the screen", back.actions.left <= 9 && back.actions.right >= back.vw - 9,
    `${Math.round(back.actions.left)}…${Math.round(back.actions.right)} of ${back.vw}`);
  ok("back: all four ratings are reachable",
    (await page.locator("[data-recall-ratings] button").count()) === 4);
  // Flipping gives the review its room, so the tape does give some back — but
  // the card is still the chart, and the answer is capped (`.recall-side`'s
  // 42dvh) so it can never take more than it leaves.
  ok("back: the review cannot crush the tape", back.chart.h >= back.vh * 0.45,
    `chart ${Math.round(back.chart.h)} of ${back.vh} (${Math.round((back.chart.h / back.vh) * 100)}%), ` +
      `front was ${Math.round(front.chart.h)}`);

  // A long review scrolls inside the answer column, under buttons that stay put.
  const held = await page.evaluate(() => {
    const side = document.querySelector("[data-recall-back]");
    const before = document.querySelector(".recall-actions").getBoundingClientRect().top;
    side.scrollTop = side.scrollHeight;
    return { moved: document.querySelector(".recall-actions").getBoundingClientRect().top - before,
             scrolled: side.scrollTop };
  });
  ok("back: the ratings hold while the review scrolls under them", Math.abs(held.moved) <= 1,
    `moved ${Math.round(held.moved)}px over ${Math.round(held.scrolled)}px of scroll`);
  ok("no page errors", errors.length === 0, errors[0] ?? "");
  await shot(page, "recallbar-2-back");
  await ctx.close();
}

// ---- desktop -----------------------------------------------------------------
{
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  await openRecall(page);

  const front = await rows(page, "[data-recall-flip]");
  checkOrder(front, "desktop");
  ok("desktop: chart and answer are side by side", front.actions.left > front.chart.right,
    `answer starts ${Math.round(front.actions.left)}, chart ends ${Math.round(front.chart.right)}`);
  ok("desktop: the chart got the shell's 215px back", front.chart.h > 700,
    `${Math.round(front.chart.h)}px (was ~630 with chrome)`);
  ok("desktop: no sidebar toggle on a chrome-less page",
    (await page.locator(".sidebar-toggle").count()) === 0);
  ok("no page errors (desktop)", errors.length === 0, errors[0] ?? "");
  await shot(page, "recallbar-3-desktop");
  await ctx.close();
}

await browser.close();
console.log(fails.length ? `\n${fails.length} FAILED: ${fails.join(", ")}` : "\nall good");
process.exit(fails.length ? 1 : 0);
