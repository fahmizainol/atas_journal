// Throwaway: does docs/research/modern-vwap-swing-anchor.html actually draw?
//
// It is a bare-content research page (the API wraps it in a shell), so file://
// is a fair approximation of what the Lab's iframe serves.
import { launch, SHOTS } from "./lib.mjs";
import { mkdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = pathToFileURL(
  resolve(HERE, "../../docs/research/modern-vwap-swing-anchor.html"),
).href;

const ink = async (page, id) =>
  page.evaluate((sel) => {
    const c = document.getElementById(sel);
    const g = c.getContext("2d");
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] || d[i + 1] || d[i + 2]) n++;
    return { w: c.width, h: c.height, litPct: +((100 * n) / (d.length / 4)).toFixed(2) };
  }, id);

const { browser, page, errors } = await launch();
await mkdir(SHOTS, { recursive: true });

await page.goto(PAGE, { waitUntil: "load" });
await page.waitForTimeout(600);

const report = {};
report.default = { main: await ink(page, "main"), zoom: await ink(page, "zoom") };
report.prov = await page.textContent("#prov");
report.verdictLen = (await page.textContent("#verdict")).trim().length;
report.rows = await page.$$eval("#anchTable tbody tr", (r) => r.length);
report.stats = await page.$$eval("#stats .stat", (r) => r.length);
report.statText = await page.$$eval("#stats .stat", (r) => r.map((x) => x.innerText.replace(/\n/g, " | ")));
await page.screenshot({ path: `${SHOTS}/swing-default.png`, fullPage: true });

// every pivot length redraws without throwing
for (const p of [3, 5, 20, 10]) {
  await page.click(`#plPills button[data-v="${p}"]`);
  await page.waitForTimeout(250);
  report[`pl${p}`] = {
    main: (await ink(page, "main")).litPct,
    zoom: (await ink(page, "zoom")).litPct,
    rows: await page.$$eval("#anchTable tbody tr", (r) => r.length),
    verdict: (await page.textContent("#verdict")).slice(0, 60).replace(/\s+/g, " "),
  };
}

// gallery: five session canvases with ink, headers change with the knobs
report.gallery = {
  heads: await page.$$eval("#gallery .mono", (r) => r.map((x) => x.textContent)),
  ink: [],
};
for (let k = 0; k < 5; k++) report.gallery.ink.push((await ink(page, "gcv" + k)).litPct);
await page.locator("#gallery").screenshot({ path: `${SHOTS}/gallery-swing.png` });

// POC-touch mode: every re-arm setting redraws without throwing
await page.click('#modePills button[data-m="poc"]');
await page.waitForTimeout(250);
report.pocDefault = {
  main: (await ink(page, "main")).litPct,
  zoom: (await ink(page, "zoom")).litPct,
  rows: await page.$$eval("#anchTable tbody tr", (r) => r.length),
  heading: (await page.textContent("#detH2")).trim(),
  stats: await page.$$eval("#stats .stat", (r) => r.map((x) => x.innerText.replace(/\n/g, " | "))),
  plCtlHidden: await page.$eval("#plCtl", (el) => el.style.display === "none"),
  armCtlShown: await page.$eval("#armCtl", (el) => el.style.display !== "none"),
};
await page.screenshot({ path: `${SHOTS}/poc-noarm.png`, fullPage: true });
for (const a of [10, 25, 50]) {
  await page.click(`#armPills button[data-v="${a}"]`);
  await page.waitForTimeout(200);
  report[`arm${a}`] = {
    main: (await ink(page, "main")).litPct,
    rows: await page.$$eval("#anchTable tbody tr", (r) => r.length),
    verdict: (await page.textContent("#verdict")).slice(0, 70).replace(/\s+/g, " "),
  };
}
await page.screenshot({ path: `${SHOTS}/poc-arm50.png`, fullPage: true });
report.galleryPoc = {
  heads: await page.$$eval("#gallery .mono", (r) => r.map((x) => x.textContent)),
};
await page.locator("#gallery").screenshot({ path: `${SHOTS}/gallery-poc.png` });
report.weeklyPoc = {
  heads: await page.$$eval("#weekly .mono", (r) => r.map((x) => x.textContent)),
  ink: [(await ink(page, "wcv0")).litPct, (await ink(page, "wcv1")).litPct],
};
await page.locator("#weekly").screenshot({ path: `${SHOTS}/weekly-poc.png` });
// the migration-manufactured touch: scrub to just after bar 90 with re-arm 25
await page.click('#armPills button[data-v="25"]');
await page.$eval("#scrub", (el) => { el.value = "90"; el.dispatchEvent(new Event("input", { bubbles: true })); });
await page.waitForTimeout(250);
report.pocMigration = {
  clock: await page.textContent("#clock"),
  verdict: (await page.textContent("#verdict")).slice(0, 240).replace(/\s+/g, " "),
};
await page.screenshot({ path: `${SHOTS}/poc-migration.png`, fullPage: true });
// back to swing for the remaining swing-mode checks
await page.click('#modePills button[data-m="swing"]');
await page.$eval("#scrub", (el) => { el.value = "149"; el.dispatchEvent(new Event("input", { bubbles: true })); });
await page.waitForTimeout(200);
report.backToSwing = {
  rows: await page.$$eval("#anchTable tbody tr", (r) => r.length),
  tShowShown: await page.$eval("#tShowLbl", (el) => el.style.display !== "none"),
};

// scrub back: the mid-window state is the one the page is really about
await page.$eval("#scrub", (el) => {
  el.value = "62";
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
await page.waitForTimeout(250);
report.scrubbed = {
  clock: await page.textContent("#clock"),
  main: (await ink(page, "main")).litPct,
  verdict: (await page.textContent("#verdict")).slice(0, 80).replace(/\s+/g, " "),
};
await page.screenshot({ path: `${SHOTS}/swing-scrubbed.png`, fullPage: true });

// play/pause advances the head
await page.click("#playBtn");
await page.waitForTimeout(900);
report.playedTo = await page.textContent("#clock");
await page.click("#playBtn");

// the toggles
for (const id of ["tShow", "tGhost", "tBands"]) {
  await page.click(`#${id}`);
  await page.waitForTimeout(120);
}
report.togglesOff = (await ink(page, "main")).litPct;
await page.screenshot({ path: `${SHOTS}/swing-bare.png`, fullPage: true });

// narrow viewport
await page.setViewportSize({ width: 420, height: 900 });
await page.waitForTimeout(400);
report.narrow = { main: await ink(page, "main"), scrollX: await page.evaluate(() => document.documentElement.scrollWidth) };
await page.screenshot({ path: `${SHOTS}/swing-narrow.png`, fullPage: true });

console.log(JSON.stringify(report, null, 2));
console.log("errors:", errors.length ? errors : "none");
await browser.close();
