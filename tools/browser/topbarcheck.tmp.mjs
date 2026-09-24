// Does the chart top bar scroll itself on a narrow screen, instead of dragging
// the whole page sideways — and do its popovers survive being inside a scroll
// container?
//
//   node tools/browser/topbarcheck.tmp.mjs [--headed]
import { launch, openChart, shot } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

/** Page-level horizontal overflow: the thing the user was dragging. */
const pageOverflow = () =>
  page.evaluate(() => {
    const d = document.documentElement;
    return { scrollW: d.scrollWidth, clientW: d.clientWidth, bodyScrollW: document.body.scrollWidth };
  });

const barMetrics = () =>
  page.evaluate(() => {
    const b = document.querySelector(".chart-topbar");
    if (!b) return null;
    const cs = getComputedStyle(b);
    return {
      scrollW: b.scrollWidth,
      clientW: b.clientWidth,
      h: Math.round(b.getBoundingClientRect().height),
      overflowX: cs.overflowX,
      scrollLeft: b.scrollLeft,
    };
  });

/** Where a popover actually landed, and whether the scroller ate it. */
const popBox = (sel) =>
  page.evaluate((s) => {
    const el = document.querySelector(s);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      pos: getComputedStyle(el).position,
      x: Math.round(r.x),
      y: Math.round(r.y),
      w: Math.round(r.width),
      h: Math.round(r.height),
      inViewport:
        r.width > 0 && r.height > 0 && r.left >= -1 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1,
    };
  }, sel);

try {
  // ---- phone ----------------------------------------------------------------
  await page.setViewportSize({ width: 390, height: 844 });
  await openChart(page, "/charts/replay");
  await page.waitForTimeout(400);

  const bar = await barMetrics();
  const over = await pageOverflow();
  console.log("\nphone 390x844", { bar, over });

  check(
    "the bar is the scroller",
    bar?.overflowX === "auto" && bar.scrollW > bar.clientW + 4,
    `overflowX=${bar?.overflowX} scrollW=${bar?.scrollW} clientW=${bar?.clientW}`,
  );
  check("the bar still fits 36px", bar?.h != null && bar.h <= 40, `${bar?.h}px`);
  check(
    "the page no longer drags sideways",
    over.scrollW <= over.clientW + 1,
    `doc scrollW=${over.scrollW} clientW=${over.clientW}`,
  );

  // The far end of the bar is reachable by scrolling the bar alone.
  await page.evaluate(() => {
    const b = document.querySelector(".chart-topbar");
    b.scrollLeft = b.scrollWidth;
  });
  await page.waitForTimeout(200);
  const endReach = await page.evaluate(() => {
    const btns = [...document.querySelectorAll(".chart-topbar-end .chart-topbar-btn")];
    const last = btns[btns.length - 1];
    if (!last) return null;
    const r = last.getBoundingClientRect();
    return { right: Math.round(r.right), vw: innerWidth, visible: r.right <= innerWidth + 1 && r.left >= -1 };
  });
  check("the last control is reachable by scrolling the bar", endReach?.visible === true, JSON.stringify(endReach));
  const afterScroll = await pageOverflow();
  check(
    "scrolling the bar did not move the page",
    afterScroll.scrollW <= afterScroll.clientW + 1,
    `doc scrollW=${afterScroll.scrollW}`,
  );
  await shot(page, "topbar-phone-scrolled");

  // ---- popovers, which the scroll container would otherwise clip ------------
  await page.evaluate(() => {
    document.querySelector(".chart-topbar").scrollLeft = 0;
  });
  await page.locator(".nav-menu-btn").click();
  await page.waitForTimeout(250);
  const nav = await popBox(".nav-menu-pop");
  check(
    "nav menu escapes the scroller",
    nav?.pos === "fixed" && nav.h > 60 && nav.inViewport,
    JSON.stringify(nav),
  );
  await shot(page, "topbar-phone-nav");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);

  // ƒ — the study picker.
  const fBtn = page.locator(".study-pick > button").first();
  await fBtn.scrollIntoViewIfNeeded();
  await fBtn.click();
  await page.waitForTimeout(400);
  const study = await popBox(".study-pop");
  check(
    "study picker escapes the scroller",
    study?.pos === "fixed" && study.h > 60 && study.inViewport,
    JSON.stringify(study),
  );
  await shot(page, "topbar-phone-study");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);

  // ⋯ — the overflow timeframes.
  const more = page.locator(".chart-topbar .tf-more").first();
  if (await more.count()) {
    await more.scrollIntoViewIfNeeded();
    await more.click();
    await page.waitForTimeout(250);
    const tf = await popBox(".tf-pop");
    check("timeframe ⋯ escapes the scroller", tf?.pos === "fixed" && tf.h > 20 && tf.inViewport, JSON.stringify(tf));
    await shot(page, "topbar-phone-tf");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
  } else {
    check("timeframe ⋯ escapes the scroller", false, "no ⋯ button on the bar");
  }

  // ---- the whole narrow range, not just a phone ----------------------------
  // The bar wants 1178px (barwidth.tmp.mjs), so every width under that used to
  // drag the page — 1024 and 1100 included.
  for (const width of [1100, 1024, 900, 768, 600]) {
    await page.setViewportSize({ width, height: 844 });
    await page.waitForTimeout(400);
    const o = await pageOverflow();
    const b = await barMetrics();
    check(
      `${width}px: page does not drag, bar does`,
      o.scrollW <= o.clientW + 1 && b.overflowX === "auto",
      `doc=${o.scrollW}/${o.clientW} bar=${b.scrollW}/${b.clientW}`,
    );
  }

  // ---- desktop must be untouched -------------------------------------------
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".chart-legend");
  await page.waitForTimeout(1200);
  const deskBar = await barMetrics();
  check("desktop bar is not a scroll container", deskBar?.overflowX === "visible", `overflowX=${deskBar?.overflowX}`);
  await page.locator(".nav-menu-btn").click();
  await page.waitForTimeout(250);
  const deskNav = await popBox(".nav-menu-pop");
  check("desktop nav menu stays anchored", deskNav?.pos === "absolute", JSON.stringify(deskNav));
  await shot(page, "topbar-desktop-nav");
  // The legend used to tie the bar's popovers at 60 and win on DOM order, so
  // the menu opened and you read the chart's rows through its background.
  // elementFromPoint cannot see it (the legend is pointer-events:none), so ask
  // the stack directly.
  const layers = await page.evaluate(() => {
    const z = (s) => getComputedStyle(document.querySelector(s)).zIndex;
    return { nav: z(".nav-menu-pop"), legend: z(".chart-legend") };
  });
  check(
    "desktop: the bar's menu outranks the chart legend",
    Number(layers.nav) > Number(layers.legend),
    JSON.stringify(layers),
  );
  await page.keyboard.press("Escape");
  await shot(page, "topbar-desktop");

  const bad = errors.filter((e) => !/favicon/i.test(e));
  check("no page errors", bad.length === 0, bad.slice(0, 3).join(" | "));
} finally {
  console.log(
    `\n${results.filter((r) => r[1]).length}/${results.length} passed` +
      (results.some((r) => !r[1]) ? `  FAILED: ${results.filter((r) => !r[1]).map((r) => r[0]).join(", ")}` : ""),
  );
  await browser.close();
}
