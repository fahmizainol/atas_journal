// The measured context strip, on the two pages that own it.
//
// What this asks:
//
//   1. a trade the tape could be measured for shows the strip, with the four
//      horizons and real numbers in them — not a row of dashes;
//   2. the strip sits BELOW the journal form, not above it. That ordering is the
//      whole point of measuring separately: a machine's number sitting over the
//      tag box turns a record of what you thought into a prompt for what to
//      think, and nothing else in the UI would catch it moving;
//   3. a truncated post-window says so in words rather than reporting a zero
//      that reads as "the market stood still";
//   4. the numbers on screen are the numbers the API stored — no client-side
//      re-derivation crept in.
//
// Run: node tools/browser/contextcheck.mjs [--headed]
import { launch, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");

/** A trade this machine has both a journal row and cached ticks for. Overridable
 *  — which sessions are on disk is a property of the box, not of the feature. */
const TRADE_NO = process.env.CONTEXT_TRADE ?? "703";

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const API = process.env.API_URL ?? "http://localhost:8000";

async function main() {
  const stored = await fetch(`${API}/api/trades/${TRADE_NO}`).then((r) => r.json());
  ok("the API has context for this trade", stored.context != null,
     stored.context ? `method ${stored.context.method}` : "none — pick another CONTEXT_TRADE");
  if (!stored.context) return;

  const { browser, page } = await launch({ headed });
  try {
    await page.goto(`${BASE}/trades/${TRADE_NO}`, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(1500);

    const strip = page.locator("text=Measured context").first();
    const visible = await strip.isVisible().catch(() => false);
    ok("the strip renders on the Trades page", visible);
    if (!visible) {
      await shot(page, "context-missing");
      return;
    }

    // The trade detail renders inside an expanded row of the trades table, which
    // is itself a `.panel` — so "the panel containing this text" matches the
    // whole page. Anchor on the strip's own title and take its parent.
    const panel = page
      .locator(".section-title", { hasText: /^Measured context$/ })
      .first()
      .locator("xpath=..");
    const text = await panel.innerText();

    // 1. the horizons are there, with numbers rather than dashes
    for (const h of ["1m", "5m", "15m", "30m"]) {
      ok(`horizon ${h} is labelled`, text.includes(h));
    }
    for (const row of ["into entry", "after, best", "after, worst"]) {
      ok(`row "${row}" is present`, text.includes(row));
    }
    const dashes = (text.match(/–/g) ?? []).length;
    ok("the horizons carry numbers, not dashes", dashes === 0, `${dashes} dashes`);

    // 4. what is on screen is what was stored
    const mfe15 = stored.context.post_mfe_pts_15m;
    ok("the stored 15m follow-through is on screen",
       text.includes(`+${mfe15.toFixed(1)}`) || text.includes(mfe15.toFixed(1)),
       `expected ${mfe15.toFixed(1)}`);

    const rank = Math.round((stored.context.exit_rank ?? 0) * 100);
    ok("the exit rank is on screen", text.includes(`${rank}%`), `expected ${rank}%`);

    // 2. the separation that keeps the measurement out of the trader's answer.
    //    This used to be an ordering rule — the strip had to come after the form
    //    — because both lived in one column and "after" was the only way to keep
    //    a conclusion from sitting above the box you write your own in. The
    //    detail is two columns now, and the same rule reads as a side: machine
    //    left, human right. So what is asked is which column each landed in, not
    //    which came first, and the form must not share a column with the strip.
    const split = await page.evaluate(() => {
      const title = [...document.querySelectorAll(".section-title")].find(
        (e) => e.textContent?.trim() === "Measured context",
      );
      const strip = title?.parentElement;
      const form = document.querySelector(".trade-detail-rail textarea");
      if (!strip || !form) return null;
      return {
        stripInRail: !!strip.closest(".trade-detail-rail"),
        stripInMain: !!strip.closest(".trade-detail-main"),
        stripX: strip.getBoundingClientRect().x,
        formX: form.getBoundingClientRect().x,
      };
    });
    ok("the journal form is in the rail", split !== null,
       split === null ? "no textarea inside .trade-detail-rail" : "");
    ok("the measurement is in the trade column, not the journal's",
       !!split && split.stripInMain && !split.stripInRail);
    ok("machine left, human right", !!split && split.stripX < split.formX,
       split ? `strip x=${Math.round(split.stripX)}, form x=${Math.round(split.formX)}` : "");

    // 3. the truncation notice, only when the window really was short
    const truncated = stored.context.post_avail_s < 30 * 60;
    const saysShort = text.includes("of tape followed this exit");
    ok("the truncation notice matches the stored window",
       truncated === saysShort,
       `post_avail_s ${Math.round(stored.context.post_avail_s)}s, notice ${saysShort}`);

    await shot(page, "context-strip");

    // 5. the window chart. The trap it guards is the micro/mini split: a journal
    //    row says `MNQU6@CME` because that is the product traded, while the tape
    //    is cached under the mini's root — so drawing from the row's own label
    //    finds nothing and paints an empty window beside a full set of numbers.
    //    The measurement stores the contract it resolved; the chart must use it.
    const key = stored.trade.logical_trade_key;
    const chartData = await fetch(
      `${API}/api/trades/key/${key}/context`,
    ).then((r) => r.json());
    ok("the window's bars resolve through the measured contract",
       chartData.bars.length > 0,
       `${chartData.bars.length} bars on ${stored.context.symbol} for a ` +
       `${stored.trade.instrument} trade`);

    const toggle = page.locator("button", { hasText: "Show the window" }).first();
    ok("the window is offered", await toggle.isVisible().catch(() => false));
    if (await toggle.isVisible().catch(() => false)) {
      await toggle.scrollIntoViewIfNeeded();
      await toggle.click();
      await page.waitForTimeout(2500);
      const canvases = await panel.locator("canvas").count();
      ok("the window draws", canvases > 0, `${canvases} canvases`);
      const notices = await panel.locator(".notice").count();
      ok("the window has no failure notice", notices === 0,
         notices ? await panel.locator(".notice").first().innerText() : "");
      await shot(page, "context-window");
    }
  } finally {
    await browser.close();
  }
}

await main();
console.log(fails.length ? `\n${fails.length} FAILED: ${fails.join(", ")}` : "\nall ok");
process.exit(fails.length ? 1 : 0);
