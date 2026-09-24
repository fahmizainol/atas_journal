// Throwaway: does the day-goal control actually re-render the grid?
import { chromium } from "playwright";
const b = await chromium.launch({ channel: "chrome", headless: true });
const p = await b.newPage({ viewport: { width: 1280, height: 1000 } });
const errs = [];
p.on("pageerror", (e) => errs.push(e.message));
await p.goto("file://" + process.argv[2], { waitUntil: "load" });
await p.waitForTimeout(600);

const read = async () => ({
  head: (await p.textContent("#head")).replace(/\s+/g, " ").trim().slice(0, 90),
  first: (await p.textContent("#table tbody tr:first-child")).replace(/\s+/g, " ").trim().slice(0, 80),
  rows: await p.$$eval("#table tbody tr", (r) => r.length),
});

const before = await read();
console.log("goal none :", before.head);
console.log("           ", before.first);

await p.click('.seg[data-key="day_goal"] button[data-v="500"]');
await p.waitForTimeout(400);
const after = await read();
console.log("goal +$500:", after.head);
console.log("           ", after.first);

await p.click('.seg[data-key="sizing"] button[data-v="risk400"]');
await p.waitForTimeout(400);
const sized = await read();
console.log("risk400   :", sized.head);

// hover the first row and confirm a tooltip appears
const box = await p.$eval("#chart .hit", (e) => e.getBoundingClientRect().toJSON()).catch(() => null);
let tipOk = false;
if (box) {
  await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await p.waitForTimeout(200);
  tipOk = await p.$eval("#tip", (t) => getComputedStyle(t).opacity !== "0" && t.textContent.length > 20);
}
console.log(`\nchanged on goal: ${before.head !== after.head} | rows stable: ${before.rows === after.rows} ` +
            `(${before.rows}) | tooltip: ${tipOk} | js errors: ${errs.length}`);
await b.close();
