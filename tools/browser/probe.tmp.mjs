import { BASE, launch } from "./lib.mjs";
const { browser, page, errors } = await launch({});
await page.goto(`${BASE}/recall`, { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForTimeout(12000);
console.log("URL:", page.url());
console.log("TEXT:", (await page.locator("body").innerText()).slice(0, 600));
console.log("ERRORS:", errors.slice(0, 5));
await page.screenshot({ path: "shots/recallprobe.png" });
await browser.close();
