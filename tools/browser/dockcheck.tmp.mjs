// Measures where the order pad's buttons actually land, at a phone viewport with
// a coarse pointer, against the real index.css. The question is only about box
// geometry, so a static page carrying the real markup and the real stylesheet
// answers it without standing the app up.
import { chromium } from "playwright";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const HERE = "/tmp/claude-1000/-home-afahmi-repos-atas-journal/d2c18de2-e1f0-4072-ada9-3cace317e117/scratchpad";
const CSS = "/home/afahmi/repos/atas_journal/frontend/src/index.css";

const server = http.createServer((req, res) => {
  const url = req.url.split("?")[0];
  const file = url === "/index.css" ? CSS : path.join(HERE, url === "/" ? "dock.html" : url);
  try {
    const body = fs.readFileSync(file);
    res.writeHead(200, { "content-type": url.endsWith(".css") ? "text/css" : "text/html" });
    res.end(body);
  } catch {
    res.writeHead(404).end("no");
  }
});
await new Promise((r) => server.listen(0, r));
const port = server.address().port;

const browser = await chromium.launch({ channel: "chrome" });
const rows = [];
for (const [name, viewport, hasTouch] of [
  ["phone   375x812 coarse", { width: 375, height: 812 }, true],
  ["desktop 1440x900 fine ", { width: 1440, height: 900 }, false],
]) {
  const ctx = await browser.newContext({ viewport, hasTouch, isMobile: hasTouch, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.waitForTimeout(200);
  const out = await page.evaluate(() => {
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: Math.round(r.top), left: Math.round(r.left), w: Math.round(r.width), h: Math.round(r.height) };
    };
    return {
      coarse: matchMedia("(pointer: coarse)").matches,
      body: box(".sim-quick-body"),
      chip: box('[data-t="chip"]'),
      close: box('[data-t="close"]'),
      sell: box('[data-t="sell"]'),
      buy: box('[data-t="buy"]'),
    };
  });
  await page.screenshot({ path: `${HERE}/dock-${hasTouch ? "phone" : "desktop"}.png`, clip: { x: 0, y: viewport.height - 220, width: Math.min(viewport.width, 420), height: 220 } });
  rows.push([name, out]);
  await ctx.close();
}
await browser.close();
server.close();

let bad = 0;
const ok = (cond, msg) => { if (!cond) bad++; console.log(`  ${cond ? "ok  " : "FAIL"} ${msg}`); };
for (const [name, o] of rows) {
  console.log(`\n${name}   pointer:coarse=${o.coarse}`);
  console.log(`  body ${o.body.w}px   chip y=${o.chip.top}  sell y=${o.sell.top}  buy y=${o.buy.top}  close y=${o.close.top}`);
  ok(o.sell.top === o.buy.top, "SELL and BUY share a row");
  ok(o.sell.left < o.buy.left, "SELL is left of BUY");
  ok(o.close.top > o.sell.top, "Close is on a row BELOW the pair");
  ok(o.chip.top < o.sell.top, "the position chip is on a row ABOVE the pair");
  ok(Math.abs(o.close.w - (o.body.w - 12)) <= 2, `Close is full-width (${o.close.w} of ${o.body.w - 12} available)`);
  ok(o.close.h < o.sell.h, `Close is shorter than the pair (${o.close.h} vs ${o.sell.h})`);
}
console.log(bad ? `\n${bad} failed` : "\nall pass");
process.exit(bad ? 1 : 0);
