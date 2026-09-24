// Throwaway: the sitting's note on Recall's back.
//
// Drives the real deck against the real journal — no stubs, because the thing
// being checked is a join through live data (trade -> its session row -> the
// note the review wrote). What it asks:
//
//   1. the deck draws a card and its **front** says nothing about the sitting
//   2. flipping fetches a back that carries `session_note` as its own field
//   3. and when there is one, it is on screen, apart from the trade's own note
//
// It rates nothing, so the schedule is untouched — the one thing on this page
// that would be a real write.
//
// `domcontentloaded`, not `networkidle`: a Recall card streams a day of tape and
// the page is rarely idle inside a minute, which is what makes recallcheck's own
// goto flaky here rather than anything being wrong with the page.
import { BASE, launch } from "./lib.mjs";

const { browser, page, errors } = await launch({ headed: process.argv.includes("--headed") });
const results = [];
const check = (label, ok, detail = "") => {
  results.push([label, ok, detail]);
  console.log(`  ${ok ? "✓" : "✗"} ${label}${detail ? `  — ${detail}` : ""}`);
};

/** Every back the page fetches, so the payload can be read without re-asking
 *  for it (a second fetch of the same key would be a different request than the
 *  one the card actually rendered). */
const backs = [];
page.on("response", async (r) => {
  if (!/\/api\/recall\/back\//.test(r.url())) return;
  try {
    backs.push(await r.json());
  } catch {
    /* not JSON — the failure shows up as a missing back below */
  }
});

try {
  await page.goto(`${BASE}/recall`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector("[data-recall-card]", { timeout: 90000 });

  // The front, before anything is flipped. The deck payload is recallcheck's
  // business; what matters here is that the *sitting* is not on screen either.
  const frontText = await page.locator("[data-recall-card]").first().innerText();
  const deck = await page.evaluate(async () => {
    const r = await fetch("/api/recall/deck");
    return r.json();
  });
  check("the deck front carries no session_note field",
    deck.cards.every((c) => !("session_note" in c)),
    `${deck.cards.length} card(s)`);

  await page.waitForSelector("[data-recall-flip]", { timeout: 60000 });
  await page.locator("[data-recall-flip]").click();
  await page.waitForSelector("[data-recall-back]", { timeout: 30000 });
  await page.waitForTimeout(500);

  const back = backs[0];
  check("the back arrived with a session_note field", !!back && "session_note" in back,
    back ? JSON.stringify(back.session_note) : "no back seen");

  const note = (back?.session_note ?? "").trim();
  if (note) {
    const el = page.locator("[data-recall-session-note]");
    check("…and it is on screen", (await el.count()) === 1);
    const shown = (await el.innerText()).trim();
    check("…reading as the sitting's own words", shown.includes(note.slice(0, 40)), shown.slice(0, 90));
    check("…and it was not on the front", !frontText.includes(note.slice(0, 20)));
    // Distinct from the trade's note: two paragraphs, two scopes.
    check("…and the trade's own note is still its own",
      (back.note ?? "") !== back.session_note || !back.note);
  } else {
    // A real state, and the honest thing to report: no sitting in the deck has
    // been given a note yet, so only the payload half is proven here.
    check("no sitting in this deck has a note yet — render half unproven", true,
      "write one in a review and re-run");
    check("nothing is rendered for an empty note",
      (await page.locator("[data-recall-session-note]").count()) === 0);
  }

  // --- the render, on a deck that has no note to show yet -------------------
  // No sitting in the live journal carries one (the field is hours old), and
  // writing one to prove a render would be putting test prose in a real
  // journal. So the *server's own* answer is fetched and handed back with a
  // note added: the page cannot tell the difference, and nothing is written.
  const INJECTED = "trended off the globex VAL all session; I kept fading it";
  await page.route("**/api/recall/back/**", async (route) => {
    const real = await route.fetch();
    const body = await real.json();
    route.fulfill({ json: { ...body, session_note: INJECTED } });
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("[data-recall-card]", { timeout: 90000 });
  const front2 = await page.locator("[data-recall-card]").first().innerText();
  await page.waitForSelector("[data-recall-flip]", { timeout: 60000 });
  await page.locator("[data-recall-flip]").click();
  await page.waitForSelector("[data-recall-back]", { timeout: 30000 });
  await page.waitForTimeout(400);

  const el = page.locator("[data-recall-session-note]");
  // `count()` does not auto-wait — asked before the flip's fetch lands it reads
  // 0 and says the render failed, which is how this check first lied.
  await el.first().waitFor({ state: "visible", timeout: 20000 });
  check("a sitting with a note renders it on the back — once", (await el.count()) === 1);
  const shown = (await el.innerText()).trim();
  check("…as the sitting's own words, labelled", shown.includes(INJECTED) && /session/i.test(shown),
    shown.slice(0, 100));
  check("…and none of it was on the front", !front2.includes(INJECTED.slice(0, 20)));
  await page.screenshot({ path: "shots/recallsession.png" });

  check(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);
} catch (e) {
  console.log(`\n!! ${e?.stack ?? e}`);
  results.push(["ran to the end", false, String(e?.message ?? e)]);
} finally {
  console.log(`\n${results.filter((r) => r[1]).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.every((r) => r[1]) ? 0 : 1);
}
