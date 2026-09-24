// The per-trade replay arms the bracket the trade was *placed* with, not the
// ticket's current default.
//
// Trade #2483 is the case that exposed it: a short from 29737 that the journal
// books at +$558 on a 66-tick stop the tape never reached, replayed against the
// ticket's default 50 — which sits at 29749.5, and the tape printed 29750.25
// twenty-two seconds after the entry. The panel stopped it out and disagreed
// with the record it was drawn from.
//
// Only a browser can answer this. The arithmetic (which price comes first) is
// settled off the parquet; what is not is whether the arm *reaches* the
// recorded bracket — an attempt fetched through the query cache on an async
// path, matched to an order by clock, and handed over as optional tick
// distances. Any of those failing lands back on the ticket silently.
//
// So the assertion is a clock: the position must still be open past the moment
// the wrong stop would have taken it, and off by the time the right target
// does.
//
// Run: node tools/browser/armbracketcheck.tmp.mjs [--headed]
import { launch, shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");
const TRADE = process.env.TRADE_NO ?? "2483";
const ATTEMPT = "2026-08-11_NQU6_20260824T120426Z";

// Tape wall clocks, in seconds from midnight, read off the tick store:
//   09:43:07.8  entry, short 29737
//   09:43:30.3  the *default* 50t stop (29749.5) would fire here
//   09:44:18.1  the *placed* 97t target (29712.75) is reached here
//   09:44:23.1  the recorded exit
const hms = (s) => [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60];
const secs = (t) => {
  const m = /(\d{1,2}):(\d{2}):(\d{2})/.exec(t ?? "");
  return m ? +m[1] * 3600 + +m[2] * 60 + +m[3] : null;
};
const WRONG_STOP = secs("09:43:31");
const TARGET = secs("09:44:19");

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const { browser, page, errors } = await launch({ headed });
const fetched = [];
page.on("request", (r) => {
  const m = /\/replays\/([^/?]+)$/.exec(new URL(r.url()).pathname);
  if (m) fetched.push(decodeURIComponent(m[1]));
});

try {
  console.log(`trade #${TRADE} — ${BASE}/trades/${TRADE}?mode=replay`);
  await page.goto(`${BASE}/trades/${TRADE}?mode=replay`, { waitUntil: "networkidle" });
  await page.waitForSelector(".sim-clock", { timeout: 60000 });

  // Sample the clock and the dock's position line as it plays. The position
  // line is the only DOM readout of the practice fill — the bracket itself is
  // drawn on canvas. The clock sits in the transport bar *above* the chart box,
  // the dock inside it, so only the dock is scoped to `[data-day-replay]`.
  const samples = [];
  const deadline = Date.now() + 150_000;
  let sawOpen = false;
  while (Date.now() < deadline) {
    const s = await page.evaluate(() => ({
      clock: document.querySelector(".sim-clock")?.textContent ?? "",
      pos: document.querySelector("[data-day-replay] .sim-quick-pos")?.textContent ?? "",
    }));
    const t = secs(s.clock);
    if (t != null) samples.push({ t, pos: s.pos });
    if (s.pos) sawOpen = true;
    if (sawOpen && !s.pos && t != null && t > WRONG_STOP) break;
    await page.waitForTimeout(500);
  }

  const opened = samples.find((s) => s.pos);
  const lastOpen = [...samples].reverse().find((s) => s.pos);
  const closedAt = lastOpen ? samples.find((s) => s.t > lastOpen.t && !s.pos) : null;
  const at = (s) => (s ? hms(s.t).map((n) => String(n).padStart(2, "0")).join(":") : "never");

  ok("fetched the trade's own sitting", fetched.includes(ATTEMPT), fetched.join(", ") || "nothing");
  ok("a position opened", !!opened, opened ? opened.pos.replace(/\s+/g, " ") : "none");
  // Not the recorded 29737.00 to the tick, and it shouldn't be: the arm submits
  // at `entry − latency` and the sim fills it 250ms later off the tape, so the
  // practice fill lands wherever the tape had moved to. What must hold is the
  // side, the size, and a price inside the entry second's printed range
  // (29735.00–29740.25) — anything outside that is the wrong moment, not slip.
  const px = Number(/@\s*([\d.]+)/.exec(opened?.pos ?? "")?.[1]);
  ok("it is the trade's own fill", !!opened && /SHORT ×1/.test(opened.pos) && px >= 29735 && px <= 29740.25,
     opened ? opened.pos.replace(/\s+/g, " ") : "none");
  ok("survives the default 50t stop (09:43:30)", !!lastOpen && lastOpen.t > WRONG_STOP,
     `last seen open ${at(lastOpen)}`);
  ok("comes off at the placed 97t target (09:44:18)", !!closedAt && closedAt.t >= TARGET,
     `flat by ${at(closedAt)}`);
  // The whole complaint: it came off a winner, not a stop. The dock's open P&L
  // on the last sample before it went flat is the only readout of that.
  ok("it was in profit when it came off", !!lastOpen && !/-\$/.test(lastOpen.pos),
     lastOpen ? lastOpen.pos.replace(/\s+/g, " ") : "none");
  ok(`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0);

  await shot(page, "armbracket");
} finally {
  await browser.close();
}

console.log(fails.length ? `\n${fails.length} failed` : "\nall good");
process.exit(fails.length ? 1 : 0);
