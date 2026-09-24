// Throwaway: render a static research page in both themes and report layout facts.
//   node tools/browser/pageshot.tmp.mjs <abs-path-to-html>
import { chromium } from "playwright";

const file = process.argv[2];
const shots = process.argv[3] || "/tmp/pageshot";
const b = await chromium.launch({ channel: "chrome", headless: true });

for (const theme of ["dark", "light"]) {
  const p = await b.newPage({ viewport: { width: 1280, height: 1500 }, colorScheme: theme });
  const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  p.on("console", (m) => m.type() === "error" && errs.push(m.text()));
  await p.goto("file://" + file, { waitUntil: "load" });
  await p.waitForTimeout(800);

  const head = (await p.textContent("#head").catch(() => "")) || "";
  const rows = await p.$$eval("#table tbody tr", (r) => r.length).catch(() => -1);
  const dots = await p.$$eval("#chart circle", (c) => c.length).catch(() => -1);
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  const ctl = await p.$$eval("#controls .ctl", (c) => c.length).catch(() => -1);
  // does anything actually differ between the two accounts?
  const gaps = await p.$$eval("#table tbody tr", (rs) =>
    rs.slice(0, 5).map((r) => r.children[4].textContent.trim())).catch(() => []);
  console.log(
    `[${theme}] controls=${ctl} tableRows=${rows} chartDots=${dots} ` +
    `overflowX=${overflow} errors=${errs.length}${errs.length ? " :: " + errs[0].slice(0, 90) : ""}`);
  console.log(`         head: ${head.replace(/\s+/g, " ").trim().slice(0, 150)}`);
  console.log(`         top gaps: ${gaps.join(", ")}`);
  await p.screenshot({ path: `${shots}-${theme}.png` });
  await p.close();
}
await b.close();
