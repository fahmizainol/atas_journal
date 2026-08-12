// The checks themselves.
//
//   node smoke.mjs              all checks
//   node smoke.mjs appearance   one by name
//   node smoke.mjs --headed     watch it happen
//
// Needs the dev servers up (`pnpm dev` at the repo root). Screenshots land in
// tools/browser/shots/ whatever the outcome — on a failure the picture is
// usually the answer.
import { launch, openChart, probeChart, probeVolumeBand, openAppearance, shot, BASE } from "./lib.mjs";

const REPLAY = "/charts/replay";

const checks = {
  /** The Simulator draws at all, and quietly. */
  async replay({ page, errors }) {
    await openChart(page, REPLAY);
    const p = await probeChart(page);
    await shot(page, "replay");
    return [
      [`canvas ${p.w}x${p.h}`, p.w > 400 && p.h > 200],
      [`drew something (${p.ink} ink samples)`, p.ink > 500],
      [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
    ];
  },

  /**
   * The chart's hand tools and transport additions: the ━ price-line tool, the
   * crosshair OHLC readout, the bar-close countdown, step-back, the k
   * play/pause key — and that a drawn line survives a reload via the
   * chart.drawings store.
   *
   * The line itself is canvas, so the persistence assertion reads the store
   * (which is what a reload restores from) and the on-screen assertion reads
   * the DOM the tool leaves behind: the selected line's Delete button.
   */
  async tools({ page, errors }) {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => localStorage.removeItem("chart.drawings"));
    await openChart(page, REPLAY);

    const hlineBtn = page.locator("button[data-tip^='Horizontal line']");
    const stepBackBtn = page.locator("button[title^='One bar back']");
    const countdown = await page.locator(".sim-transport .sim-countdown").count();

    // Hover a bar: the readout appears and reads like OHLC.
    const chart = await page.locator(".sim-chart canvas").first().boundingBox();
    await page.mouse.move(chart.x + chart.width * 0.5, chart.y + chart.height * 0.35);
    await page.waitForTimeout(300);
    const ohlc = await page.evaluate(() => {
      const el = document.querySelector(".chart-ohlc");
      return el ? { shown: getComputedStyle(el).display !== "none", text: el.textContent } : null;
    });

    // Arm ━, click a price: a line lands, selected, and the store remembers it.
    await hlineBtn.click();
    await page.mouse.click(chart.x + chart.width * 0.5, chart.y + chart.height * 0.4);
    await page.waitForTimeout(200);
    const delBtn = await page.locator("button[data-tip^='Remove this price line']").count();
    const stored = await page.evaluate(() => {
      try {
        const store = JSON.parse(localStorage.getItem("chart.drawings") ?? "{}");
        const days = Object.values(store);
        return days.length === 1 ? days[0].hlines : null;
      } catch {
        return null;
      }
    });
    await shot(page, "tools-1-hline");

    // k toggles the transport.
    await page.keyboard.press("k");
    await page.waitForTimeout(200);
    const playing = (await page.locator(".sim-transport button", { hasText: "Pause" }).count()) === 1;
    await page.keyboard.press("k");
    await page.waitForTimeout(200);
    const paused = (await page.locator(".sim-transport button", { hasText: "Play" }).count()) === 1;

    // The line outlives the page. (The picker opens a random day per visit, so
    // pin the reload to the same session via the store's own key check instead
    // of the canvas: restore is `setTape`'s job and keyed by session.)
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForSelector(".chart-legend", { timeout: 60000 });
    await page.waitForTimeout(1500);
    const survived = await page.evaluate(() => {
      try {
        const store = JSON.parse(localStorage.getItem("chart.drawings") ?? "{}");
        return Object.values(store).some((d) => d.hlines?.length === 1);
      } catch {
        return false;
      }
    });
    await page.evaluate(() => localStorage.removeItem("chart.drawings"));

    return [
      ["━ tool on the rail", (await hlineBtn.count()) === 1],
      ["⏮ step-back on the transport", (await stepBackBtn.count()) === 1],
      [`bar-close countdown on the clock (${countdown})`, countdown === 1],
      [
        `OHLC readout on hover (${ohlc?.text?.slice(0, 24) ?? "absent"}…)`,
        !!ohlc?.shown && /O\s/.test(ohlc.text ?? ""),
      ],
      ["placing a line selects it (Delete offered)", delBtn === 1],
      [
        `line saved to chart.drawings (${stored?.length ?? 0} line)`,
        Array.isArray(stored) && stored.length === 1 && Number.isFinite(stored[0].price),
      ],
      ["k plays", playing],
      ["k pauses again", paused],
      ["drawings survive a reload", survived],
      [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
    ];
  },

  /**
   * The ◎ in the chart's top-right corner: back to the price.
   *
   * Two ways to lose the tape and one button that undoes both, so both are made
   * to happen here. Panning is the obvious one and the DOM can answer it (the
   * button lights). The scale is the quiet one — autoscale holds every layer at
   * once, so a weekly VWAP far under the session leaves the day as a ribbon —
   * and nothing in the DOM knows about it. The assertion for that half reads the
   * canvas: how many pixel rows of the pane carry a candle colour, before the
   * press and after. Rows rather than pixels, because a row with one wick in it
   * is a row the tape reached.
   */
  async jump({ page, errors }) {
    await openChart(page, REPLAY);
    const btn = page.locator(".chart-jump");
    const chart = await page.locator(".sim-chart").boundingBox();
    const canvas = await page.locator(".sim-chart canvas").first().boundingBox();
    const lit = () => btn.evaluate((e) => e.classList.contains("on"));
    // Classic green/red, which is the scheme the harness leaves the app in.
    const tapeRows = () =>
      page.evaluate(() => {
        const c = [...document.querySelectorAll("canvas")].sort(
          (a, b) => b.width * b.height - a.width * a.height,
        )[0];
        const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
        let rows = 0;
        for (let y = 0; y < c.height; y++) {
          for (let x = 0; x < c.width; x++) {
            const i = (y * c.width + x) * 4;
            const [r, g, b] = [d[i], d[i + 1], d[i + 2]];
            if ((g > 150 && r < 90 && b > 90 && b < 160) || (r > 200 && g < 110 && b > 70 && b < 130)) {
              rows++;
              break;
            }
          }
        }
        return rows / c.height;
      });

    // The button is inside the price axis, not over it — the axis is a drag
    // target of its own.
    const box = await btn.boundingBox();
    const gap = chart.x + chart.width - (box.x + box.width);
    const axisW = await page.evaluate(() =>
      parseFloat(getComputedStyle(document.querySelector(".chart-jump").parentElement)
        .getPropertyValue("--axis-w")),
    );

    // Zoom the scale out by hand: the tape becomes a band, price still on screen.
    const cy = canvas.y + canvas.height * 0.5;
    await page.mouse.move(chart.x + chart.width - 20, cy);
    await page.mouse.down();
    await page.mouse.move(chart.x + chart.width - 20, cy + 240, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(400);
    const squashed = await tapeRows();
    await shot(page, "jump-1-squashed");

    await btn.click();
    await page.waitForTimeout(400);
    const framed = await tapeRows();
    await shot(page, "jump-2-framed");

    // Pan hard back through the morning: the live edge goes off to the right.
    await page.mouse.move(canvas.x + canvas.width * 0.3, cy);
    await page.mouse.down();
    for (let i = 1; i <= 10; i++) await page.mouse.move(canvas.x + canvas.width * 0.3 + i * 60, cy);
    await page.mouse.up();
    await page.waitForTimeout(400);
    const litWhenPanned = await lit();
    await shot(page, "jump-3-panned");

    await btn.click();
    await page.waitForTimeout(400);
    const litAfterPress = await lit();

    return [
      ["◎ on the chart", (await btn.count()) === 1],
      [`sits inside the price axis (${Math.round(gap)}px gap, axis ${axisW}px)`,
        Math.abs(gap - (axisW + 8)) <= 1],
      ["lights up when the live edge is panned off", litWhenPanned],
      ["...and goes out once pressed", !litAfterPress],
      [`the press frames the tape (${(squashed * 100) | 0}% → ${(framed * 100) | 0}% of the pane)`,
        framed > squashed + 0.2],
      [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
    ];
  },

  /**
   * The Live chart, driven by the simulated feed — the only way to see this
   * page with a tape under it. What's asserted is exactly what Live lacked
   * before it caught up with Replay: the ⚓ actually *draws* (the legend row
   * appeared even when the band silently didn't — ink on the canvas is the
   * honest assertion), the event rows and the legend "…" knobs exist, and the
   * bar-close countdown rides the top-bar clock on a time bar.
   */
  async live({ page, errors }) {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    // One context day: enough to give the composite row something to exist
    // over, without fetching a week of tapes.
    await page.evaluate(() => localStorage.setItem("live.historyDays", "1"));
    await page.goto(`${BASE}/charts/live`, { waitUntil: "networkidle", timeout: 60000 });
    const startBtn = page.locator("button", { hasText: "Start simulated feed" });
    if (await startBtn.count()) {
      await startBtn.click();
      await page.waitForTimeout(1000);
    }
    await openChart(page, "/charts/live", { timeout: 90000 });

    // A time bar, so the countdown has something to count.
    await page.locator(".chart-topbar .radio-group button", { hasText: /^1m$/ }).click();
    await page.waitForTimeout(800);
    const countdown = await page.locator(".chart-topbar .sim-countdown").count();

    const eventRow = await page
      .locator(".chart-legend-row", { hasText: "Sweep bursts" })
      .count();
    const bigKnob = await page
      .locator(".chart-legend-item[data-ind-item='bigTrades'] .chart-legend-dots")
      .count();

    // The ⚓, placed mid-tape: the band and its σ lines must land on the canvas.
    // Counted as *teal* pixels (vwapPalette.anchored, which nothing else on the
    // chart wears) rather than raw ink — the band widens the autoscale range,
    // so total ink can go down while the band very much drew.
    const tealCount = () =>
      page.evaluate(() => {
        const c = [...document.querySelectorAll("canvas")].sort(
          (a, b) => b.width * b.height - a.width * a.height,
        )[0];
        const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
        let n = 0;
        for (let i = 0; i < d.length; i += 4) {
          const [r, g, b] = [d[i], d[i + 1], d[i + 2]];
          if (r < 100 && g > 170 && b > 150 && b < g + 10) n++;
        }
        return n;
      });
    const tealBefore = await tealCount();
    await page.locator("button[data-tip^='Anchored VWAP']").click();
    const chart = await page.locator(".sim-chart canvas").first().boundingBox();
    await page.mouse.click(chart.x + chart.width * 0.35, chart.y + chart.height * 0.5);
    await page.waitForTimeout(800);
    const tealAfter = await tealCount();
    const anchoredRow = await page
      .locator(".chart-legend-row", { hasText: "VWAP · Anchored" })
      .count();
    await shot(page, "live-anchored");

    // Leave the app as found.
    await page.locator(".live-status button", { hasText: "Stop" }).click();
    await page.evaluate(() => localStorage.removeItem("live.historyDays"));

    return [
      [`bar-close countdown on the top bar (${countdown})`, countdown === 1],
      ["event rows offered on Live", eventRow === 1],
      ["big-trades row carries its knob", bigKnob === 1],
      ["⚓ places (legend row appears)", anchoredRow === 1],
      [`⚓ draws (${tealBefore} → ${tealAfter} anchored-teal pixels)`, tealAfter > tealBefore + 200],
      [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
    ];
  },

  /**
   * CandlestickChart — the other of the two chart components — via the
   * Interactions Lab's session drill-in.
   *
   * A route on its own doesn't get you there: every CandlestickChart in the app
   * hangs off a selection (a session row here, a trade on a strategy page, a
   * draft). The Lab's table is the shortest path to one, and it takes whichever
   * session sorts first rather than naming a date, which would rot as the run
   * range moves.
   *
   * Also where the "sticky across every chart" claim gets tested: the surface is
   * set on the Simulator (ReplayChart), and this asserts it arrived here.
   */
  async journal({ page, errors }) {
    await page.goto(`${BASE}/charts/replay`, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForSelector(".chart-legend", { timeout: 60000 });
    const sel = await openAppearance(page);
    await sel.nth(0).selectOption("slate");
    await page.waitForTimeout(300);

    await page.goto(`${BASE}/interactions`, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForSelector("tbody tr");
    await page.locator("tbody tr").first().click();
    await page.waitForSelector(".chart-legend", { timeout: 60000 });
    await page.waitForTimeout(3000);
    const p = await probeChart(page);
    await shot(page, "journal");

    // Put it back, so a run doesn't leave the app in a colour nobody chose.
    const sel2 = await openAppearance(page);
    await sel2.nth(0).selectOption("charcoal");

    const hex = (c) => `#${c.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
    return [
      [`journal chart drew (${p.ink} ink samples)`, p.ink > 500],
      [`surface carried over from the Simulator (${hex(p.bg)})`, hex(p.bg) === "#131722"],
      [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
    ];
  },

  /**
   * The chart-appearance preference.
   *
   * The load-bearing assertion is the silhouette one. Recolouring goes through
   * applyOptions precisely so it does NOT re-run the build effect — a rebuild
   * would reset the visible range, and losing the range you spent a minute
   * framing because you changed the background is the failure this guards.
   */
  async appearance({ page }) {
    await openChart(page, REPLAY);
    const before = await probeChart(page);
    const volBefore = await probeVolumeBand(page);
    await shot(page, "appearance-1-before");

    const sel = await openAppearance(page);
    await sel.nth(0).selectOption("black");
    await sel.nth(1).selectOption("cb");
    await page.waitForTimeout(600);
    const after = await probeChart(page);
    const volAfter = await probeVolumeBand(page);
    await shot(page, "appearance-2-black-cb");

    // Back to the defaults: the chart should land exactly where it started.
    await sel.nth(0).selectOption("charcoal");
    await sel.nth(1).selectOption("classic");
    await page.waitForTimeout(600);
    const round = await probeChart(page);
    await shot(page, "appearance-3-roundtrip");

    // The preference is meant to outlive the page.
    await page.reload({ waitUntil: "networkidle" });
    await openChart(page, REPLAY);
    const sel2 = await openAppearance(page);
    const persisted = await sel2.nth(0).inputValue();

    const hex = (c) => `#${c.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
    const volChanged = JSON.stringify(volBefore) !== JSON.stringify(volAfter);
    return [
      [`surface starts charcoal (${hex(before.bg)})`, hex(before.bg) === "#0e1117"],
      [`surface becomes black (${hex(after.bg)})`, hex(after.bg) === "#000000"],
      ["visible range survives the recolour", before.silhouette === after.silhouette],
      ["round-trip returns to the original", before.silhouette === round.silhouette],
      [`round-trip restores the surface (${hex(round.bg)})`, hex(round.bg) === "#0e1117"],
      ["volume bars follow the candle scheme", volChanged],
      [`choice survives a reload (${persisted})`, persisted === "charcoal"],
    ];
  },

  /**
   * Every preset in theme.ts, walked, with a screenshot of each.
   *
   * Table-driven off the selects rather than a list of expected hexes — that
   * list would be theme.ts written twice, and the copy would rot. A preset added
   * to theme.ts is covered here without touching this file.
   *
   * Distinctness is the assertion that earns its place: two presets sharing a
   * background means a copy-paste, and nothing about the dropdown would look
   * wrong. The screenshots are the rest of the point — this is the contact sheet
   * you actually pick colours from.
   */
  async presets({ page, errors }) {
    await openChart(page, REPLAY);
    const sel = await openAppearance(page);
    const hex = (c) => `#${c.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
    const values = (n) => sel.nth(n).locator("option").evaluateAll((o) => o.map((x) => x.value));
    const surfaces = await values(0);
    const schemes = await values(1);

    const bg = new Map();
    for (const s of surfaces) {
      await sel.nth(0).selectOption(s);
      await page.waitForTimeout(400);
      bg.set(s, hex((await probeChart(page)).bg));
      await shot(page, `preset-surface-${s}`);
    }
    await sel.nth(0).selectOption("charcoal");

    // Candle schemes are read off the volume gutter: it carries the same up/down
    // pair as flat fills, so it is the cheapest place to see a scheme change.
    const vol = new Map();
    for (const c of schemes) {
      await sel.nth(1).selectOption(c);
      await page.waitForTimeout(400);
      vol.set(c, JSON.stringify(await probeVolumeBand(page)));
      await shot(page, `preset-candles-${c}`);
    }
    await sel.nth(1).selectOption("classic");

    const uniqBg = new Set(bg.values()).size;
    const uniqVol = new Set(vol.values()).size;
    return [
      [`${surfaces.length} surfaces · ${schemes.length} candle schemes`, surfaces.length > 1],
      [`surfaces all distinct (${uniqBg}/${surfaces.length})`, uniqBg === surfaces.length],
      [`surfaces match their names (${[...bg.entries()].map(([k, v]) => `${k}=${v}`).join(" ")})`, true],
      [`candle schemes all distinct (${uniqVol}/${schemes.length})`, uniqVol === schemes.length],
      [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
    ];
  },

  /**
   * The market-order window (components/charts/QuickDock) — parked, dragged,
   * remembered, clamped, and not firing orders by accident.
   *
   * Not a canvas check like the others, but it is the same reason for existing:
   * a drag is geometry under a real pointer, and neither tsc nor reading the
   * component tells you whether the box lands where the cursor put it. The
   * load-bearing assertions are the first-frame one (parked, the window is
   * centred by a transform — measuring the wrong rect makes the first drag jump)
   * and the button one (a press that starts on BUY must not become a drag, and a
   * drag must never become an order).
   */
  async dock({ page, errors }) {
    // The geometry of the window against the chart it floats over, plus what the
    // page is publishing as the floor and what the browser has remembered.
    const geom = () =>
      page.evaluate(() => {
        const el = document.querySelector(".sim-quick");
        const parent = el.offsetParent;
        const e = el.getBoundingClientRect();
        const p = parent.getBoundingClientRect();
        return {
          x: Math.round(e.left - p.left),
          y: Math.round(e.top - p.top),
          w: Math.round(e.width),
          h: Math.round(e.height),
          pw: Math.round(p.width),
          ph: Math.round(p.height),
          moved: el.classList.contains("moved"),
          floor: parseFloat(getComputedStyle(parent).getPropertyValue("--chart-floor")) || 0,
          saved: localStorage.getItem("chart.quickDockPos"),
        };
      });
    const dragFrom = async (sel, to) => {
      const box = await page.locator(sel).boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(to.x, to.y, { steps: 12 });
      await page.mouse.up();
      await page.waitForTimeout(150);
    };

    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => localStorage.removeItem("chart.quickDockPos"));
    await openChart(page, REPLAY);
    await page.waitForSelector(".sim-quick");

    const parked = await geom();
    const chart = await page.locator(".sim-chart").boundingBox();

    // Grab the grip and drop it somewhere the window has never been. What lands
    // on the cursor is the *grip*, not the box's centre — the grab offset is
    // held for the whole drag, which is what stops the window jumping its own
    // corner under the pointer on the first frame.
    const grip = await page.locator(".sim-quick-grip").boundingBox();
    const hold = { x: grip.x + grip.width / 2 - (chart.x + parked.x), y: grip.y + grip.height / 2 - (chart.y + parked.y) };
    const target = { x: chart.x + 260, y: chart.y + 180 };
    await dragFrom(".sim-quick-grip", target);
    const moved = await geom();
    await shot(page, "dock-1-moved");

    // The spot outlives the page — and the same key feeds the Live chart.
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForSelector(".sim-quick");
    await page.waitForTimeout(400);
    const reloaded = await geom();

    // Shoved past the bottom-right corner it stays wholly inside the chart,
    // which is what keeps a spot saved on a wide monitor reachable on a laptop.
    await dragFrom(".sim-quick-grip", { x: chart.x + chart.width + 400, y: chart.y + chart.height + 400 });
    const clamped = await geom();

    // A press that starts on BUY is a button press, not a drag: it must not move
    // the window, and releasing off the button means no order is sent either.
    const before = await geom();
    const buy = await page.locator(".sim-quick-btn.buy").boundingBox();
    await page.mouse.move(buy.x + buy.width / 2, buy.y + buy.height / 2);
    await page.mouse.down();
    await page.mouse.move(buy.x - 220, buy.y - 160, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(150);
    const afterPress = await geom();
    const flat = (await page.locator(".sim-quick-btn.flat").count()) === 0;

    // Double-click the chrome and it goes back to the foot of the tape — which
    // is also how this check leaves the app as it found it.
    await page.locator(".sim-quick-grip").dblclick();
    await page.waitForTimeout(200);
    const reset = await geom();
    await shot(page, "dock-2-reset");

    const centred = Math.abs(parked.x + parked.w / 2 - parked.pw / 2) <= 2;
    return [
      [`parks bottom-centred (x ${parked.x}, ${parked.ph - parked.y - parked.h}px off the foot)`,
        centred && parked.ph - parked.y - parked.h < 20 && !parked.moved],
      [`parked window claims the floor (${parked.floor}px vs ${parked.h}px tall)`,
        parked.floor > 0 && Math.abs(parked.floor - parked.h) <= 1],
      [`the grip stays under the cursor (${moved.x},${moved.y} + ${Math.round(hold.x)},${Math.round(hold.y)})`,
        moved.moved && Math.abs(moved.x + hold.x - 260) <= 3 && Math.abs(moved.y + hold.y - 180) <= 3],
      [`floated window claims no floor (${moved.floor}px)`, moved.floor === 0],
      [`position survives a reload (${reloaded.x},${reloaded.y})`,
        reloaded.moved && Math.abs(reloaded.x - moved.x) <= 2 && Math.abs(reloaded.y - moved.y) <= 2],
      [`clamped inside the chart (${clamped.x},${clamped.y} in ${clamped.pw}x${clamped.ph})`,
        clamped.x + clamped.w <= clamped.pw && clamped.y + clamped.h <= clamped.ph],
      [`a press on BUY doesn't drag the window (${afterPress.x},${afterPress.y})`,
        afterPress.x === before.x && afterPress.y === before.y],
      ["...and sends no order", flat],
      [`double-click puts it back (moved=${reset.moved}, saved=${reset.saved})`,
        !reset.moved && reset.saved === null],
      [`no console errors${errors.length ? `: ${errors[0]}` : ""}`, errors.length === 0],
    ];
  },
};

const args = process.argv.slice(2);
const headed = args.includes("--headed");
const want = args.filter((a) => !a.startsWith("--"));
const run = want.length ? want : Object.keys(checks);

let failed = 0;
for (const name of run) {
  if (!checks[name]) {
    console.log(`? ${name} — no such check (have: ${Object.keys(checks).join(", ")})`);
    failed++;
    continue;
  }
  const { browser, page, errors } = await launch({ headed });
  try {
    const results = await checks[name]({ page, errors });
    console.log(`\n${name}`);
    for (const [label, ok] of results) {
      console.log(`  ${ok ? "ok  " : "FAIL"} ${label}`);
      if (!ok) failed++;
    }
  } catch (e) {
    console.log(`\n${name}\n  FAIL threw: ${e.message}`);
    await shot(page, `${name}-threw`).catch(() => {});
    failed++;
  } finally {
    await browser.close();
  }
}
console.log(`\nshots in tools/browser/shots/`);
process.exit(failed ? 1 : 0);
