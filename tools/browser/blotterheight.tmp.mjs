// Throwaway: how tall is the blotter actually getting in the dock, now that the
// ticket card sits above it? Prints every card's height in the panel.
import { launch, openChart } from "./lib.mjs";
const { browser, page } = await launch({});
try {
  await openChart(page, "/charts/replay");
  await page.locator("button[title='Show the ticket and blotter']").first().click();
  await page.waitForTimeout(800);
  const out = await page.evaluate(() => {
    const p = document.querySelector(".sim-panel.open");
    if (!p) return { err: "no panel" };
    const kids = [...p.children].map((el) => ({
      cls: el.className.toString().slice(0, 40),
      h: Math.round(el.getBoundingClientRect().height),
    }));
    const b = p.querySelector(".sim-blotter");
    const l = p.querySelector(".sim-blotter-list");
    return {
      panel: Math.round(p.getBoundingClientRect().height),
      scroll: p.scrollHeight,
      kids,
      blotter: b ? Math.round(b.getBoundingClientRect().height) : null,
      list: l ? Math.round(l.getBoundingClientRect().height) : null,
    };
  });
  console.log(JSON.stringify(out, null, 1));
} finally {
  await browser.close();
}
