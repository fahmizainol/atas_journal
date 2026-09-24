// Throwaway: the three things the calendar day's replay just gained — it stays
// pinned while the day scrolls, it carries Modern VWAP, and you can trade it.
//
// What this asks:
//
//   1. the panel comes up already open on a fresh load, off the stored flag,
//      and stays open across a day switch;
//   2. with the Modern VWAP row turned on, the legend inside the panel names it;
//   3. the panel stays at the top of the viewport once the page is scrolled to
//      the journal form below it;
//   4. BUY opens a position, the chip prices it, and Close takes it off —
//      leaving a practice trade mark behind;
//   5. the ▶ beside a trade arms that trade: the position appears with the
//      journal's own side and size, and comes off by the journal's exit;
//   6. none of that wrote anything: no request left the page while trading.
//
// Run: node tools/browser/daypracticecheck.tmp.mjs [--headed]
import { launch, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const DAY = process.env.JOURNAL_DAY ?? "2026-06-30";
const OTHER = process.env.JOURNAL_DAY_2 ?? "2026-06-22";
const ROOT = "[data-day-replay]";

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const probe = (page) =>
  page.evaluate((sel) => {
    const root = document.querySelector(sel);
    if (!root) return { error: "no panel" };
    const c = [...root.querySelectorAll("canvas")].sort(
      (a, b) => b.width * b.height - a.width * a.height,
    )[0];
    if (!c || !c.width || !c.height) return { error: "no sized canvas" };
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const tally = new Map();
    for (let i = 0; i < d.length; i += 16 * 4) {
      const k = `${d[i]},${d[i + 1]},${d[i + 2]}`;
      tally.set(k, (tally.get(k) ?? 0) + 1);
    }
    const bg = [...tally.entries()].sort((a, b) => b[1] - a[1])[0][0].split(",").map(Number);
    let ink = 0;
    for (let i = 0; i < d.length; i += 16 * 4) {
      if (
        Math.abs(d[i] - bg[0]) >= 12 ||
        Math.abs(d[i + 1] - bg[1]) >= 12 ||
        Math.abs(d[i + 2] - bg[2]) >= 12
      )
        ink++;
    }
    return { w: c.width, h: c.height, ink };
  }, ROOT);

async function waitForInk(page, timeout = 60000) {
  const until = Date.now() + timeout;
  for (;;) {
    const p = await probe(page);
    if (!p.error && p.ink > 500) return p;
    if (Date.now() > until) return p;
    await page.waitForTimeout(500);
  }
}

const { browser, page, errors } = await launch({ headed });

// Panel open by default, Modern VWAP's row on. Both are stored preferences, so
// they are seeded the way the app itself would have left them.
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.evaluate(() => {
  localStorage.setItem("day.replay.open", "1");
  const vis = JSON.parse(localStorage.getItem("chart.indicatorVisibility") || "{}");
  vis.modernVwap = true;
  localStorage.setItem("chart.indicatorVisibility", JSON.stringify(vis));
});

await page.goto(`${BASE}/calendar/${DAY}?mode=replay`, { waitUntil: "domcontentloaded" });
const p1 = await waitForInk(page);
ok("the panel comes up open", !p1.error, p1.error ?? `${p1.w}x${p1.h}, ink ${p1.ink}`);

const mv = page.locator(`${ROOT} .chart-legend >> text=/Modern VWAP/`).first();
ok("the legend names Modern VWAP", (await mv.count()) > 0);

// Sticky: scroll the day to its foot and read where the pinned wrapper sits.
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(400);
const rect = await page.evaluate(() => {
  const r = document.querySelector(".day-replay-sticky").getBoundingClientRect();
  return { top: r.top, bottom: r.bottom, vh: window.innerHeight, y: window.scrollY };
});
ok("the page actually scrolled", rect.y > 200, `scrollY ${Math.round(rect.y)}`);
ok(
  "the panel is pinned to the top",
  Math.abs(rect.top) < 4 && rect.bottom > 0,
  `top ${Math.round(rect.top)} of ${rect.vh}`,
);
await shot(page, "day-practice-sticky");
await page.evaluate(() => window.scrollTo(0, 0));

// Trading it. Everything the page sends is recorded first — a practice fill is
// supposed to leave no trace, and "no trace" is a claim about the wire.
const sent = [];
page.on("request", (r) => {
  if (["POST", "PATCH", "PUT", "DELETE"].includes(r.method())) sent.push(`${r.method()} ${r.url()}`);
});

// Wait for the panel to be able to take an order at all: the button is disabled
// until the engine has a tape and a mark.
await page.waitForSelector(`${ROOT} .sim-quick-btn.buy:not([disabled])`, { timeout: 60000 });
const buy = page.locator(`${ROOT} .sim-quick-btn.buy`);
ok("the order window is there", (await buy.count()) > 0);

const play = page.locator(".sim-transport button").first();
const clockText = () => page.locator(".sim-clock").first().innerText();
const chipCount = () => page.locator(`${ROOT} .sim-quick-pos`).count();

// The order is in flight for as long as the fill model says a gesture takes, so
// the tape has to move before it can be matched — a market order placed on a
// stopped clock fills once the clock runs. Play in short bursts and stop at the
// first sample that answers: the drop is the cash open, where a bracket can be
// reached inside a couple of seconds of tape.
async function playUntil(want, budgetMs = 12000) {
  const until = Date.now() + budgetMs;
  for (;;) {
    await play.click();
    await page.waitForTimeout(250);
    await play.click();
    await page.waitForTimeout(150);
    if (await want()) return true;
    if (Date.now() > until) return false;
  }
}

const t0 = await clockText();
await buy.click();
const opened = await playUntil(async () => (await chipCount()) > 0);
const chip = page.locator(`${ROOT} .sim-quick-pos`);
const chipText = opened ? (await chip.first().innerText()).replace(/\s+/g, " ") : "";
console.log(`       clock ${t0} -> ${await clockText()}`);
ok("BUY opened a position", /LONG ×\d/.test(chipText), chipText || "no chip");
const withPos = await probe(page);
ok("the position drew", withPos.ink > 500, `ink ${withPos.ink}`);

if (opened) {
  await page.locator(`${ROOT} .sim-quick-btn.flat`).click();
  // Same story as the entry: a flatten is a gesture, and it lands when the tape
  // reaches it. A bracket can also get there first — either way the position is
  // off, which is what this asks.
  const closed = await playUntil(async () => (await chipCount()) === 0);
  ok("Close took it off", closed);
}
await shot(page, "day-practice-traded");

// The ▶ in the table arms the day's own trade. It rewinds to just before the
// entry and plays, so this only has to watch: the position should turn up with
// the row's side and size, and be gone again by the row's exit — either at the
// exit itself or earlier, if the ticket's bracket got there first.
const row = page.locator("table tbody tr").first();
const rowText = (await row.innerText()).replace(/\s+/g, " ").trim();
const wantSide = /\bShort\b/i.test(rowText) ? "SHORT" : "LONG";
const clocks = rowText.match(/\d\d:\d\d:\d\d/g) ?? [];
const entryClock = clocks[0] ?? null;
const exitClock = clocks[1] ?? null;
await row.locator("button", { hasText: "▶" }).first().click();

const armed = { text: "", filledAt: "", offClock: "" };
for (let i = 0; i < 90; i++) {
  const s = await page.evaluate((sel) => {
    const root = document.querySelector(sel);
    return {
      pos: root?.querySelector(".sim-quick-pos")?.innerText?.replace(/\s+/g, " ") ?? "",
      clock: document.querySelector(".sim-clock")?.innerText ?? "",
    };
  }, ROOT);
  if (s.pos && !armed.text) {
    armed.text = s.pos;
    armed.filledAt = s.clock;
  }
  if (armed.text && !s.pos) {
    armed.offClock = s.clock;
    break;
  }
  await page.waitForTimeout(400);
}
ok(
  "the ▶ armed the journal's trade",
  armed.text.startsWith(`${wantSide} ×`),
  armed.text || "no position",
);
// Sampled at 100ms the fill lands on the entry second exactly; this loop samples
// more slowly than that and can read the clock a render behind the chip, so it
// allows a second either side rather than demanding the strings match.
const secs = (t) => {
  const [h, m, s] = t.split(":").map(Number);
  return h * 3600 + m * 60 + s;
};
ok(
  "it entered at the journal's entry",
  entryClock != null && armed.filledAt !== "" && Math.abs(secs(armed.filledAt) - secs(entryClock)) <= 1,
  `filled ${armed.filledAt || "never"}, row entry ${entryClock ?? "?"}`,
);
ok(
  "it came off by the journal's exit",
  armed.offClock !== "" && (!exitClock || secs(armed.offClock) <= secs(exitClock) + 1),
  `off ${armed.offClock || "still open"}, row exit ${exitClock ?? "?"}`,
);
await shot(page, "day-practice-armed");

ok("nothing was written", sent.length === 0, sent.join(", ") || "no writes");

// The open state survives a day switch, and so does the panel.
await page.goto(`${BASE}/calendar/${OTHER}?mode=replay`, { waitUntil: "domcontentloaded" });
const p2 = await waitForInk(page);
ok("the next day comes up open too", !p2.error, p2.error ?? `${p2.w}x${p2.h}, ink ${p2.ink}`);

ok("nothing threw", errors.length === 0, errors.slice(0, 3).join(" | "));
await browser.close();
console.log(fails.length ? `\n${fails.length} failed: ${fails.join(", ")}` : "\nall ok");
process.exit(fails.length ? 1 : 0);
