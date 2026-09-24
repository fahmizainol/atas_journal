// Throwaway: what are the sizer's actual inputs and outputs on a real replay?
// Dumps every cell with its title (which carries risk + fees + losers-to-death)
// and the row labels, so "why is 9 MNQ only $194" can be answered arithmetically.
import { launch, openChart } from "./lib.mjs";
const { browser, page } = await launch({});
try {
  await openChart(page, "/charts/replay");
  for (let i = 0; i < 8; i++) await page.keyboard.press("]");
  await page.keyboard.press("k");
  await page.locator("button.sim-knob-rr.preset", { hasText: /\d+t/ }).waitFor({ timeout: 60000 });
  await page.keyboard.press("k");
  await page.waitForTimeout(600);
  await page.locator("button.sim-knob-rr.preset").first().click();
  await page.waitForTimeout(500);
  const out = await page.evaluate(() => {
    const pop = document.querySelector(".sim-sizer-pop");
    const g = (sel) => [...pop.querySelectorAll(sel)];
    return {
      head: pop.querySelector(".sim-sizer-head")?.innerText.replace(/\s+/g, " "),
      rows: g(".sim-sizer-lbl").map((e) => ({ t: e.innerText.replace(/\s+/g, " "), title: e.title })),
      cells: g(".sim-sizer-cell").map((e) => ({
        t: e.innerText.replace(/\s+/g, " "),
        title: e.title,
      })),
      note: g(".sim-sizer-note").map((e) => e.innerText.replace(/\s+/g, " ")),
    };
  });
  console.log(JSON.stringify(out, null, 1));
  // and what the account says its day limit is
  const acct = await page.evaluate(() =>
    document.querySelector(".sim-acct, .sim-guards, .sim-card")?.innerText?.replace(/\s+/g, " ").slice(0, 300),
  );
  console.log("\nACCOUNT:", acct);
} finally {
  await browser.close();
}
