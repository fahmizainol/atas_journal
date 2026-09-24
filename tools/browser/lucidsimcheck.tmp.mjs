// Drive the Lucid drawdown simulator research page: does it draw, do the two
// plans actually diverge on the same seed, and does the floor ratchet?
import { launch, shot } from "./lib.mjs";

const URL = process.env.SIM_URL ?? "http://localhost:8899/api/research/lucid-drawdown-simulator/raw";

const stat = (page, key) =>
  page.$$eval(".stat", (els, k) => {
    const el = els.find((e) => e.querySelector(".k").textContent.trim() === k);
    return el ? el.querySelector(".v").textContent.trim() : null;
  }, key);

const canvasInk = (page) =>
  page.evaluate(() => {
    const c = document.getElementById("chart");
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const seen = new Set();
    for (let i = 0; i < d.length; i += 4) seen.add(`${d[i]},${d[i + 1]},${d[i + 2]}`);
    return seen.size;
  });

const { browser, page, errors } = await launch();
await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForSelector("#chart");

const results = {};
for (const plan of ["intraday", "eod"]) {
  await page.click(`#seg-plan button[data-v="${plan}"]`);
  await page.click("#b-week");
  await page.waitForTimeout(120);
  results[plan] = {
    equity: await stat(page, "Equity"),
    floor: await stat(page, "Your floor"),
    room: await stat(page, "Room"),
    tax: await stat(page, "Tax so far"),
    banner: await page.$eval("#banner", (e) => e.textContent.trim()),
    rows: await page.$$eval("#ledger tbody tr", (r) => r.length),
    floors: await page.$$eval("#ledger tbody tr", (rows) =>
      rows.map((r) => r.children[6].textContent.trim())),
    closes: await page.$$eval("#ledger tbody tr", (rows) =>
      rows.map((r) => r.children[4].textContent.trim())),
    ink: await canvasInk(page),
  };
  await shot(page, `lucidsim-${plan}`);
}

console.log(JSON.stringify(results, null, 2));

// The whole point of the page: same seed, same trades, different floor.
const a = results.intraday, b = results.eod;
// Same seed must mean the same trades: every day before the earlier death has
// to close identically under both plans, or the two floors aren't comparable.
const upto = Math.min(a.closes.length, b.closes.length) - (a.banner ? 1 : 0);
const sameTrades = a.closes.slice(0, upto).every((c, i) => c === b.closes[i]);
console.log("\nsame seed => identical trades up to the death:", sameTrades, `(${upto} days)`);
console.log("the two floors differ on that same path:", a.floor !== b.floor);
console.log("intraday floor sits higher than EOD's:",
  Number(a.floor.replace(/\D/g, "")) > Number(b.floor.replace(/\D/g, "")));
console.log("intraday floor ratchets during the run:", new Set(a.floors).size > 1);
console.log("canvas drew:", a.ink > 5 && b.ink > 5);
console.log("page errors:", errors.length ? errors : "none");

await browser.close();
