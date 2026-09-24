// Throwaway: pressing "seek" on a review card.
//
// Two things it has to do, and until 2026-08-17 it did neither: put the tape
// five seconds in front of the entry and **run**. It landed paused instead,
// which on the first card of a sitting is a chart with none of your trades on
// it yet — the report that started this.
//
// It also forced 1×, which this now asserts it does *not*. That third promise
// was dropped on 2026-08-21: it was never kept anyway (the frame loop reads
// `speedRef`, not the React state the seek set), and a review is a dozen seeks,
// so the transport's speed is the one that holds.
//
//   node reviewseek.tmp.mjs [--headed]
//
// **Read-only.** It drives whatever review the account currently owes and
// touches nothing but the seek buttons — no card is saved, no review is filed,
// review mode itself is unarmed by construction. If nothing is owed it says so
// and passes nothing.
import { launch, openChart } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const { browser, page, errors } = await launch({ headed });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

const legend = () =>
  page.evaluate(() =>
    [...document.querySelectorAll(".chart-legend-item")]
      .map((e) => e.textContent.trim())
      .find((t) => /^Trades ·/.test(t)) ?? "",
  );
const playing = async () => ((await page.locator(".sim-transport button").first().textContent()) ?? "").includes("Pause");
const speed = () => page.locator(".sim-transport select").inputValue();
const tapeMs = () => page.locator(".sim-scrub").inputValue().then(Number);
const pos = () => page.locator(".sim-quick-pos").count();

try {
  // Park the ticket on the micro before anything opens. A review sends nowhere,
  // so nothing on the canvas may claim to be routed to MNQ or price the
  // sitting's own NQ positions at the micro's $2 a point — which is what it did
  // while the overlays read the *preference* instead of each order's own stamp.
  await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    const p = JSON.parse(localStorage.getItem("sim.prefs") ?? "{}");
    localStorage.setItem("sim.prefs", JSON.stringify({ ...p, micro: true }));
  });

  await openChart(page, "/charts/replay");
  await page.waitForTimeout(2500);
  const cards = await page.locator("[data-review-trade]").count();
  if (!cards) {
    console.log("  · nothing owed — no review on the page, nothing to check");
    process.exit(0);
  }
  check(`the review is open with its cards (${cards})`, cards > 0);

  const mnq = await page.evaluate(() =>
    [...document.querySelectorAll("*")]
      .filter((e) => e.children.length === 0 && /MNQ/.test(e.textContent))
      .map((e) => e.textContent.trim())
      .slice(0, 5));
  check("a review is routed nowhere — no MNQ on the page, ticket parked on it or not",
    mnq.length === 0, mnq.join(" | "));

  // The panel gets its own column while reviewing. Unpinned it laid over the
  // right 300px of the tape — which is where a seek puts the playhead, so the
  // trades were drawn and behind the panel. Measured rather than asserted off
  // the class name: the class is what was changed, the overlap is the bug.
  const geo = await page.evaluate(() => {
    const p = document.querySelector(".sim-panel")?.getBoundingClientRect();
    const c = [...document.querySelectorAll("canvas")]
      .sort((a, b) => b.width * b.height - a.width * a.height)[0]
      ?.getBoundingClientRect();
    return { panelX: Math.round(p?.x ?? 0), chartRight: Math.round((c?.x ?? 0) + (c?.width ?? 0)) };
  });
  check(`the panel does not lay over the tape (panel at ${geo.panelX}, chart ends ${geo.chartRight})`,
    geo.panelX >= geo.chartRight - 2);

  // Not 1×, so "the seek left the speed alone" is an assertion rather than a
  // coincidence. The seek forced 1× until 2026-08-21 — and since the speed is
  // saved to `sim.prefs`, that forcing did not just interrupt the review, it
  // wrote 1 over the setting for every session after it.
  await page.selectOption(".sim-transport select", "30");
  await page.evaluate(() => document.activeElement?.blur?.());

  // A card with trades behind it: the ones already taken should be on the tape
  // the moment it lands, and one more should arrive while it runs.
  const i = Math.min(4, cards - 1);
  const btn = page.locator(`[data-review-trade="${i}"] button`).first();
  const label = (await btn.textContent()).replace(/\s+/g, " ");
  await btn.click();
  await page.waitForTimeout(200);

  check(`the seek runs the tape (card ${label})`, await playing());
  check("and leaves the speed where you set it", (await speed()) === "30",
    `select says ${await speed()}×`);
  const drawn = await legend();
  check("the sitting's earlier trades are already on it", /\d+ closed/.test(drawn), drawn || "no trades layer");

  // Down to 1× by hand for the pacing check below, which is about the frame
  // loop reading `speedRef` rather than about what the seek does.
  await page.selectOption(".sim-transport select", "1");
  await page.evaluate(() => document.activeElement?.blur?.());
  await page.waitForTimeout(200);
  const t0 = await tapeMs();
  await page.waitForTimeout(4000);
  const moved = ((await tapeMs()) - t0) / 1000;
  check(`the tape moves at wall-clock speed (${moved}s in ~4s)`, moved > 2 && moved < 7);

  // **The trade re-fills.** The reason the seek is worth pressing: five seconds
  // later the entry you came to look at happens in front of you. It did not,
  // for as long as `seekTo` truncated the log in review mode — the order was
  // thrown away on the way to the clock that was meant to show it, so the tape
  // ran past the entry and nothing ever came on.
  for (let n = 0; n < 12 && !(await pos()); n++) await page.waitForTimeout(500);
  check("and the entry lands in front of you", (await pos()) > 0,
    `clock ${(await page.locator(".sim-clock").textContent())?.trim()}`);
  await page.keyboard.press("k");

  // **Every card, at any time.** The seek above left a position on the screen,
  // and the holding rule — right for a sitting, where a rewind past your own
  // fill un-happens it — would refuse this jump backwards. A review carries no
  // size and truncates nothing, so the cards stay reachable whatever the tape
  // has just played into.
  await page.locator('[data-review-trade="1"] button').first().click();
  await page.waitForTimeout(700);
  const refusal = await page.locator("[data-refusal]").first().textContent().catch(() => null);
  check("an earlier card is reachable with a position on the screen", !refusal,
    refusal?.trim() ?? "no refusal");
  await page.keyboard.press("k");
  await page.waitForTimeout(200);

  // And a seek backwards does not take the rest of the sitting with it: the
  // whole log has to survive a rewind to the first card, or reviewing trade 1
  // leaves trades 2..17 off the tape until a reload.
  await page.locator('[data-review-trade="0"] button').first().click();
  await page.waitForTimeout(600);
  await page.keyboard.press("k");
  await page.locator(`[data-review-trade="${cards - 1}"] button`).first().click();
  await page.waitForTimeout(800);
  await page.keyboard.press("k");
  const back = await legend();
  check("a rewind to the first card keeps the rest of the sitting", /\d+ closed/.test(back),
    back || "no trades layer");

  // --- the second visit ----------------------------------------------------
  // Everything above passes on a *first* load and used to fail on every one
  // after it: `writeResume` stamped the bookmark with the recorder's attempt id,
  // and the recorder is unarmed in review mode, so it wrote null — and the id is
  // what decides whether the stored log may be replayed onto this tape. Cards on
  // the right, an empty chart, at every clock, for ever.
  const lastCard = async () => {
    const n = await page.locator("[data-review-trade]").count();
    if (!n) return "no cards";
    await page.locator(`[data-review-trade="${n - 1}"] button`).first().click();
    await page.waitForTimeout(900);
    await page.keyboard.press("k");
    await page.waitForTimeout(200);
    return legend();
  };

  await openChart(page, "/charts/replay");
  await page.waitForTimeout(2000);
  const second = await lastCard();
  check("a second visit still has the sitting on it", /\d+ closed/.test(second), second);

  // And the state a browser that has already been in a review is *in*: the null
  // stamped by an earlier build. The review must recover from it on sight,
  // rather than needing the bookmark to be rewritten once first.
  await page.evaluate(() => {
    const b = JSON.parse(localStorage.getItem("sim.resume.replay") ?? "{}");
    localStorage.setItem("sim.resume.replay", JSON.stringify({ ...b, attemptId: null }));
  });
  await openChart(page, "/charts/replay");
  await page.waitForTimeout(2000);
  const healed = await lastCard();
  check("and a bookmark left with a null attempt id recovers on the first load",
    /\d+ closed/.test(healed), healed);

  check(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  console.log(`\n!! ${e?.stack ?? e}`);
  results.push(["ran to the end", false, String(e?.message ?? e)]);
} finally {
  console.log(`\n${results.filter((r) => r[1]).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.every((r) => r[1]) ? 0 : 1);
}
