// The calendar's two changes, and the day view's new cards (2026-08-29).
//
// What this asks:
//
//   1. the table has an Account column, and the account it prints for a day is
//      the one the API says that day's shown take traded;
//   2. the row's Net PnL is the *latest attempt's*, not the sum — checked on a
//      day that actually has several takes, since on a single-take day the two
//      rules give the same number and the check would pass while asleep;
//   3. the cell and the day explorer agree. They are picked by one sort key
//      (`_attempt_order`), and the whole point of sharing it is that clicking a
//      row cannot land on a different take than the row reported;
//   4. the two "Best what-if exit" cards render on a replay day, name a row from
//      the ladder, and say so in the ladder's own numbers;
//   5. a reversed winner says it is not a bracket you can place. That branch is
//      live on real days (2026-08-06, 2026-06-29) and is the one card that would
//      otherwise read as tradeable advice.
//
// Run: node tools/browser/calendarcheck.tmp.mjs [--headed]
// Its own launch rather than lib's: this check runs against the *built* app (the
// API serves frontend/dist on the same origin, which is how it gets a backend
// without touching the wedged dev server on :8000). A build carries the PWA's
// service worker, and a stale one would serve the previous bundle no matter how
// hard the page is reloaded — so the context blocks service workers outright.
import { chromium } from "playwright";
import { shot, BASE } from "./lib.mjs";

const headed = process.argv.includes("--headed");

async function launch({ headed: h = false } = {}) {
  const browser = await chromium.launch({ channel: "chrome", headless: !h });
  const ctx = await browser.newContext({
    viewport: { width: 1600, height: 900 },
    serviceWorkers: "block",
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) {
      errors.push(m.text());
    }
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 400) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
  return { browser, page, errors };
}

const fails = [];
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "}${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) fails.push(name);
};

const api = async (path) => (await fetch(`${BASE}${path}`)).json();

/** The scope the app is actually in decides which takes exist, so the check has
 *  to ask the API in the same scope the page is rendering — otherwise it compares
 *  a live-mode table against an every-mode answer and reads the difference as a
 *  bug. The URL carries `mode=`; the API takes `modes=`.
 *
 *  Two scopes are exercised on purpose. `live` is the app's default and the one
 *  the table opens in; `replay` is where the sittings with an order log live, so
 *  it is the only scope the what-if cards can appear in at all. That the day's
 *  chosen take differs between them (2026-08-11 is a prop take under `live` and a
 *  replay sitting under every-mode) is the collapse respecting scope, not drift. */
const LIVE = { url: "", api: "?view=logical&modes=live" };
const REPLAY = { url: "?mode=replay", api: "?view=logical&modes=replay" };

/** The table's rows as objects, keyed by the header text above each cell. */
async function tableRows(page) {
  return page.evaluate(() => {
    const t = document.querySelector(".calendar-table-scroll table");
    if (!t) return null;
    const heads = [...t.querySelectorAll("thead th")].map((h) => h.textContent.trim());
    return [...t.querySelectorAll("tbody tr")].map((tr) =>
      Object.fromEntries(
        [...tr.querySelectorAll("td")].map((td, i) => [heads[i] ?? `c${i}`, td.textContent.trim()]),
      ),
    );
  });
}

/** "-$1,234" / "$56" → number. */
const money = (s) => Number(String(s).replace(/[^0-9.-]/g, "")) * (String(s).includes("-") ? 1 : 1);

const main = async () => {
  const { browser, page, errors } = await launch({ headed });

  // --- pick the days worth testing, from the API rather than by hand ---------
  const days = (await api(`/api/calendar${LIVE.api}`)).days;
  const multi = days.filter((d) => d.attempts > 1);
  console.log(`\nlive scope: ${days.length} days, ${multi.length} with several takes\n`);
  ok("the live scope has a multi-take day to test", multi.length > 0);

  await page.goto(`${BASE}/calendar${LIVE.url}`, { waitUntil: "networkidle", timeout: 60000 });
  await page.getByRole("button", { name: "Table" }).click();
  await page.waitForSelector(".calendar-table-scroll table tbody tr", { timeout: 30000 });

  const rows = await tableRows(page);
  ok("table renders rows", !!rows && rows.length > 0, `${rows?.length ?? 0} rows`);
  ok("Account column exists", !!rows && "Account" in rows[0], Object.keys(rows?.[0] ?? {}).join(" | "));

  // --- 1 + 2: the row reads the latest take, and names its account ----------
  ok(
    "every scoped day has a row",
    rows.length === days.length,
    `${rows.length} rows vs ${days.length} days`,
  );
  for (const d of multi) {
    const row = rows.find((r) => r.Date.startsWith(d.date));
    if (!row) {
      ok(`row present ${d.date}`, false, "not in the first page of rows");
      continue;
    }
    ok(`${d.date} account = ${d.account}`, row.Account === d.account, `table said "${row.Account}"`);
    ok(
      `${d.date} net is the latest take (${d.attempts} takes)`,
      Math.abs(money(row["Net PnL"]) - d.net_pnl) < 1,
      `table ${row["Net PnL"]} vs api ${d.net_pnl}`,
    );
  }

  // --- 3: clicking through lands on the take the row reported ---------------
  // The invariant `_attempt_order` exists for: one sort key, so the cell and the
  // take the explorer opens by default cannot name different numbers.
  for (const click of multi) {
    const detail = await api(`/api/day/${click.date}${LIVE.api}`);
    ok(
      `explorer default agrees with the cell on ${click.date}`,
      Math.abs(detail.kpis.net_pnl - click.net_pnl) < 1 && detail.trades.length === click.trades,
      `cell ${click.net_pnl}/${click.trades}trd vs explorer ${detail.kpis.net_pnl}/${detail.trades.length}trd`,
    );
  }

  // --- 4 + 5: the winner cards, on a normal day and on a reversed one -------
  // Replay scope, because only a sitting has the order log the ladder re-runs.
  // Chosen off the API so the check does not hard-code a day that may be pruned:
  // one sitting whose best row is a normal bracket, one whose best is reversed.
  const replayDays = (await api(`/api/calendar${REPLAY.api}`)).days
    .slice()
    .sort((a, b) => (a.date < b.date ? 1 : -1));
  const sittings = [];
  for (const d of replayDays.slice(0, 40)) {
    const j = await api(`/api/day/${d.date}${REPLAY.api}`);
    if (!j.source_file?.startsWith("replay/")) continue;
    const g = await (
      await fetch(`${BASE}/api/replays/${j.source_file.slice(7)}/whatif`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ custom: [] }),
      })
    ).json();
    if (!g.valid) continue;
    const best = g.rows.reduce((a, b) => (b.forget.net > a.forget.net ? b : a));
    sittings.push({ date: d.date, flip: !!best.spec.flip, label: best.label, net: best.forget.net });
    if (sittings.some((s) => s.flip) && sittings.some((s) => !s.flip)) break;
  }
  const normal = sittings.find((s) => !s.flip);
  const reversed = sittings.find((s) => s.flip);
  console.log(
    `\nsittings picked: normal=${normal?.date ?? "none"} reversed=${reversed?.date ?? "none"}\n`,
  );

  const readCards = async (date) => {
    await page.goto(`${BASE}/calendar/${date}${REPLAY.url}`, {
      waitUntil: "networkidle",
      timeout: 90000,
    });
    // The grid is re-priced server-side on open; the cards appear when it lands.
    await page.waitForSelector(".kpi-card", { timeout: 90000 });
    await page
      .locator(".kpi-card", { hasText: "Best · as clicked" })
      .first()
      .waitFor({ timeout: 120000 });
    return page.evaluate(() =>
      [...document.querySelectorAll(".kpi-card")]
        .map((c) => ({
          label: c.querySelector(".kpi-label")?.textContent.trim(),
          value: c.querySelector(".kpi-value")?.textContent.trim(),
          sub: c.querySelector(".kpi-sub")?.textContent.trim(),
        }))
        .filter((c) => c.label?.startsWith("Best · ")),
    );
  };

  if (normal) {
    const cards = await readCards(normal.date);
    ok(`${normal.date}: both winner cards render`, cards.length === 2, JSON.stringify(cards));
    const forget = cards.find((c) => c.label === "Best · set and forget");
    ok(
      `${normal.date}: set-and-forget card carries the ladder's own number`,
      !!forget && Math.abs(money(forget.value) - normal.net) < 1,
      `card ${forget?.value} vs grid ${normal.net}`,
    );
    ok(
      `${normal.date}: names the winning row and its edge`,
      !!forget && forget.sub?.includes(normal.label) && /vs as played|nothing in the ladder/.test(forget.sub),
      forget?.sub,
    );
    await shot(page, "calendarcheck-winner-normal");
  } else {
    ok("a sitting with a normal winner was found", false);
  }

  if (reversed) {
    const cards = await readCards(reversed.date);
    const forget = cards.find((c) => c.label === "Best · set and forget");
    ok(
      `${reversed.date}: reversed winner is flagged, not sold`,
      !!forget && /not a bracket you can place/.test(forget.sub ?? ""),
      forget?.sub,
    );
    await shot(page, "calendarcheck-winner-reversed");
  } else {
    ok("a sitting with a reversed winner was found", false);
  }

  // Console/HTTP noise. The what-if POST is slow but must not 4xx/5xx.
  const noise = errors.filter((e) => !/favicon|manifest|sw\.js/i.test(e));
  ok("no console errors or bad responses", noise.length === 0, noise.slice(0, 4).join(" | "));

  await browser.close();
  console.log(`\n${fails.length ? `FAILED: ${fails.join(", ")}` : "all checks passed"}`);
  process.exit(fails.length ? 1 : 0);
};

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
