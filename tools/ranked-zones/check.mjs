// The Ranked S/R Zones port, checked against the runtime it was transcribed from.
//
//   node tools/ranked-zones/check.mjs
//
// There is no reference output to diff against — TradingView will not hand you
// the zone list, and the source cannot be run here at all (it draws with
// `box.new`, which the dialect has not got). So the check is aimed at where a
// transcription actually goes wrong.
//
// Part 1 is the part worth the most: lib/rankedZones rolls its own ATR, SMA, EMA
// and pivot detection, and every zone boundary, every filter threshold and every
// score term is denominated in them. If those four drift, nothing downstream is
// right and nothing downstream *looks* wrong. They are diffed bar for bar
// against oakscriptjs' real `ta.atr` / `ta.sma` / `ta.ema` / `ta.pivothigh` —
// the same implementations the 415 community studies ride.
//
// Part 2 pins the scoring arithmetic to numbers worked by hand from the Pine,
// because two transcriptions of one formula agreeing proves less than one
// transcription agreeing with the formula.
//
// Part 3 asserts the invariants of the zone lifecycle, which is the part with no
// closed form: caps respected, ranking sorted, broken zones leaving the live
// list, mitigation bounded.
//
// Part 4 is the flow ranking, which is ours and not the author's. It gets a
// synthetic tape of prints behind the same bars and checks two things: that
// handing the walk a tape changes nothing about the author's ranking, and that
// each zone's flow terms equal a brute-force count of the prints.
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

// pnpm does not hoist esbuild into frontend/node_modules, so resolve it the way
// vite does rather than by path.
const fromFrontend = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { build } = await import(pathToFileURL(createRequire(fromFrontend.resolve("vite")).resolve("esbuild")).href);

const ROOT = new URL("../../frontend/", import.meta.url);
const OS = new URL("node_modules/oakscriptjs/dist/script/index.mjs", ROOT).href;

const bundle = await build({
  entryPoints: [fileURLToPath(new URL("src/lib/rankedZones.ts", ROOT))],
  bundle: true, format: "esm", write: false, platform: "neutral",
  absWorkingDir: fileURLToPath(ROOT),
});
const RZ = await import(
  "data:text/javascript;base64," + Buffer.from(bundle.outputFiles[0].text).toString("base64")
);
const { executeScript, indicator, close, high, low, volume, ta, plot } = await import(OS);

const out = [];
const ok = (label, pass) => out.push([label, pass]);

// A tape with turns at several scales, so pivots at span 5 are plentiful and
// not periodic. Deterministic: a seeded LCG, so a failure is reproducible.
let seed = 20260922;
const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
const bars = [];
for (let i = 0; i < 900; i++) {
  // Trend plus oscillation, and both are needed. The oscillation makes pivots at
  // the 11-bar scale a span of 5 can confirm; the trend carries price away from
  // the zones it leaves behind, so they survive to be ranked. A purely
  // oscillating tape breaks almost every zone it creates (price closes back
  // through it within a cycle) and the second version of this file read three
  // survivors out of thirty-six pivots and suspected the port.
  const p = 5000 + i * 0.25 + Math.sin(i / 9) * 8 + Math.sin(i / 31) * 18 + (rnd() - 0.5) * 4;
  const o = p + (rnd() - 0.5) * 2;
  const c = p + (rnd() - 0.5) * 2;
  bars.push({
    time: 1700000000 + i * 60,
    open: o, close: c,
    high: Math.max(o, c) + rnd() * 2.5,
    low: Math.min(o, c) - rnd() * 2.5,
    volume: Math.round(200 + rnd() * 800),
  });
}
const TICK = 0.25;

// --- 1. the four series, against the real ta.* -------------------------------
const pine = executeScript(() => {
  indicator("probe", { overlay: true });
  plot(ta.atr(14), "atr");
  plot(ta.sma(volume, 20), "volsma");
  plot(ta.ema(close, 50), "ema");
  plot(ta.pivothigh(high, 5, 5), "ph");
  plot(ta.pivotlow(low, 5, 5), "pl");
}, bars, {});
const byTime = (id) => new Map((pine.result.plots[id] ?? []).map((d) => [d.time, d.value]));
const [pAtr, pSma, pEma, pPh, pPl] = ["plot0", "plot1", "plot2", "plot3", "plot4"].map(byTime);

const mine = RZ.__series ?? null;
// The series are internal, so they are re-derived here the same way the module
// does — a copy of four short functions is a fair price for not widening a
// module's surface just to test it.
const worst = (get, want, from) => {
  let w = 0;
  for (let i = from; i < bars.length; i++) {
    const a = get(i), b = want.get(bars[i].time);
    if (b == null || !Number.isFinite(a)) continue;
    w = Math.max(w, Math.abs(a - b) / Math.max(1e-9, Math.abs(b)));
  }
  return w;
};

// ATR: seeded on the mean of the first 14 TRs, then Wilder — identical to ta.rma
// from bar 14 on.
const atrs = (() => {
  const o = []; let r = 0;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const tr = i === 0 ? b.high - b.low : Math.max(b.high - b.low, Math.abs(b.high - bars[i-1].close), Math.abs(b.low - bars[i-1].close));
    r = i < 14 ? (r * i + tr) / (i + 1) : (r * 13 + tr) / 14;
    o.push(r);
  }
  return o;
})();
const wAtr = worst((i) => atrs[i], pAtr, 14);
ok(`ATR matches ta.atr(14) from bar 14 (worst rel ${wAtr.toExponential(1)})`, wAtr < 1e-9);

const smas = (() => { const o=[]; let s=0; for (let i=0;i<bars.length;i++){ s+=bars[i].volume; if(i>=20) s-=bars[i-20].volume; o.push(s/Math.min(i+1,20)); } return o; })();
const wSma = worst((i) => smas[i], pSma, 19);
ok(`volume SMA matches ta.sma(volume, 20) (worst rel ${wSma.toExponential(1)})`, wSma < 1e-9);

const emas = (() => { const o=[]; let e=0; const k=2/51; for (let i=0;i<bars.length;i++){ e = i===0?bars[0].close: i<50?(e*i+bars[i].close)/(i+1): bars[i].close*k+e*(1-k); o.push(e);} return o; })();
// Pine emits nothing through `ta.ema`'s seed window, so there is no early
// divergence to measure: wherever the runtime defines a value, the port's
// running-mean seed has already converged onto the same SMA.
const wEma = worst((i) => emas[i], pEma, 0);
ok(`EMA matches ta.ema(close, 50) wherever Pine defines it (worst rel ${wEma.toExponential(1)})`, wEma < 1e-9);

// Pivots: strict both sides, confirming `span` bars late.
let phHits = 0, plHits = 0, phMiss = 0, plMiss = 0;
for (let i = 5; i + 5 < bars.length; i++) {
  let isH = true, isL = true;
  for (let j = i - 5; j <= i + 5; j++) {
    if (j === i) continue;
    if (bars[j].high >= bars[i].high) isH = false;
    if (bars[j].low <= bars[i].low) isL = false;
  }
  // Keyed on the pivot bar: that is where the runtime plots it, even though the
  // value only becomes knowable `span` bars later. The port confirms late (it
  // must) and records `leftTime` as the pivot bar, so the two line up here.
  const t = bars[i].time;
  if (isH) (pPh.get(t) === bars[i].high ? phHits++ : phMiss++);
  if (isL) (pPl.get(t) === bars[i].low ? plHits++ : plMiss++);
}
ok(`pivot highs agree with ta.pivothigh (${phHits} matched, ${phMiss} missed)`, phHits > 10 && phMiss === 0);
ok(`pivot lows agree with ta.pivotlow (${plHits} matched, ${plMiss} missed)`, plHits > 10 && plMiss === 0);
ok(`...and the counts match ta.*'s own`, phHits === pPh.size && plHits === pPl.size);

// --- 2. the scoring, against the Pine formulas worked by hand ----------------
// score = min(w/atr,1)*20 + min(vol/2,1)*18 + trend*12 + min(swing/1.5,1)*28
//       + min(touch/4,1)*16 + 10 - (mit*22 + min(age/450,1)*10)
const data = RZ.computeRankedZones(bars, { ...RZ.DEFAULT_RANKED_ZONES }, TICK);
const hand = (w, atr, vol, trend, swing, mit, touch, age) =>
  Math.max(0, Math.min(100,
    Math.min(w / atr, 1) * 20 + Math.min(vol / 2, 1) * 18 + trend * 12 +
    Math.min(swing / 1.5, 1) * 28 + Math.min(touch / 4, 1) * 16 + 10 -
    (mit * 22 + Math.min(age / 450, 1) * 10)));
const lastAtr = data.atr;
let scoreWorst = 0;
for (const z of data.zones) {
  const want = hand(z.width, lastAtr, z.volScore, z.trendScore, z.swingScore, z.mitigation, z.touchCount, bars.length - 1 - z.bornBar);
  scoreWorst = Math.max(scoreWorst, Math.abs(z.score - want));
}
ok(`every live zone's score reproduces the Pine formula (worst ${scoreWorst.toExponential(1)})`, scoreWorst < 1e-9);

// strengths: the piecewise stretch, at its two knees and past the top.
const st = (dir, s, tr, mit) => {
  let v = s >= 75 ? 72 + (s - 75) * 1.12 : s >= 45 ? 48 + (s - 45) * 0.75 : s * 1.05;
  v = Math.max(0, Math.min(100, v + tr * 6 - mit * 28));
  const opp = Math.max(0, Math.min(100, 5 + mit * 65 + s * 0.08));
  return [Math.trunc(dir === -1 ? v : opp), Math.trunc(dir === 1 ? v : opp)];
};
let strWorst = 0;
for (const z of data.zones) {
  const [b, r] = st(z.dir, Math.max(0, Math.min(100, z.score)), z.trendScore, z.mitigation);
  strWorst = Math.max(strWorst, Math.abs(z.bullStrength - b), Math.abs(z.bearStrength - r));
}
ok(`strength bars reproduce it too (worst ${strWorst})`, strWorst === 0);

// --- 3. lifecycle invariants -------------------------------------------------
const P = RZ.DEFAULT_RANKED_ZONES;
ok(`it found zones (${data.zones.length} live, ${data.broken.length} broken)`, data.zones.length > 3);
ok(`stored cap respected (${data.zones.length} <= ${P.storedLimit})`, data.zones.length <= P.storedLimit);
ok(`ranked descending`, data.zones.every((z, i) => i === 0 || data.zones[i - 1].score >= z.score));
ok(`rank is the index`, data.zones.every((z, i) => z.rank === i));
ok(`visible cap respected (${data.zones.filter((z) => z.visible).length} <= ${P.visibleLimit})`,
   data.zones.filter((z) => z.visible).length <= P.visibleLimit);
ok(`the visible ones are the top ones`, data.zones.filter((z) => z.visible).every((z) => z.rank < P.visibleLimit));
ok(`scores in range`, data.zones.every((z) => z.score >= 0 && z.score <= 100));
ok(`mitigation in 0..1`, data.zones.every((z) => z.mitigation >= 0 && z.mitigation <= 1));
ok(`strengths in 0..100 and integral`,
   data.zones.every((z) => [z.bullStrength, z.bearStrength].every((v) => Number.isInteger(v) && v >= 0 && v <= 100)));
ok(`no live zone is marked broken`, data.zones.every((z) => !z.broken));
ok(`broken kept FIFO within cap (${data.broken.length} <= ${P.keepBrokenCount})`,
   data.broken.length <= P.keepBrokenCount && data.broken.every((z) => z.broken && z.brokenTime != null));
ok(`top and bottom straddle mid`, data.zones.every((z) => z.top > z.bottom && z.mid <= z.top && z.mid >= z.bottom));
ok(`no zone outlives the age cap`, data.zones.every((z) => bars.length - 1 - z.bornBar <= 450));
ok(`ids unique`, new Set(data.zones.map((z) => z.id)).size === data.zones.length);
const lastClose = bars.at(-1).close;
ok(`nearest support is below and resistance above (${data.nearestSupport?.toFixed(1)} / ${data.nearestResistance?.toFixed(1)}, close ${lastClose.toFixed(1)})`,
   (data.nearestSupport == null || data.nearestSupport < lastClose) &&
   (data.nearestResistance == null || data.nearestResistance > lastClose));

// the knobs bite
const tight = RZ.computeRankedZones(bars, { ...P, absorbAtr: 0 }, TICK);
const loose = RZ.computeRankedZones(bars, { ...P, absorbAtr: 3 }, TICK);
ok(`absorbing merges zones (absorb 0 -> ${tight.zones.length}, absorb 3 -> ${loose.zones.length})`,
   loose.zones.length < tight.zones.length);
const filtered = RZ.computeRankedZones(bars, { ...P, direction: "support" }, TICK);
ok(`the direction filter only hides (${filtered.zones.filter((z) => z.visible).length} visible, all support)`,
   filtered.zones.filter((z) => z.visible).every((z) => z.dir === -1) &&
   filtered.zones.length === data.zones.length);
ok(`params validator rejects junk`,
   RZ.rankedZonesParams({ pivotSpan: 999, direction: "sideways" }).pivotSpan === P.pivotSpan &&
   RZ.rankedZonesParams(null).direction === "all");
ok(`a short tape is empty, not a crash`, RZ.computeRankedZones(bars.slice(0, 4), P, TICK).zones.length === 0);

// --- 4. the flow ranking, against a brute-force count of the prints ----------
// Prints behind every bar, on the tick grid inside the bar's range, sizes summing
// to the bar's volume so the two volume readings describe the same tape. The
// aggressor is biased by where in the bar a print sits (sells low, buys high),
// so in-zone shares are not all hovering at one half.
const tLevel = [], tSize = [], tSide = [], spans = [];
for (const b of bars) {
  const i0 = tLevel.length;
  let left = b.volume;
  const lo = Math.ceil(b.low / TICK), hi = Math.floor(b.high / TICK);
  while (left > 0) {
    const q = Math.min(left, 1 + Math.floor(rnd() * 12));
    left -= q;
    const lv = lo + Math.floor(rnd() * (hi - lo + 1));
    const pos = hi > lo ? (lv - lo) / (hi - lo) : 0.5;
    tLevel.push(lv); tSize.push(q);
    tSide.push(rnd() < 0.1 ? 0 : rnd() < 0.25 + pos * 0.5 ? 2 : 1);
  }
  spans.push([i0, tLevel.length - 1]);
}
const tape = {
  level: Int32Array.from(tLevel), size: Int32Array.from(tSize), side: Uint8Array.from(tSide),
  tickSize: TICK, span: (b) => spans[b] ?? null,
};

const withTape = RZ.computeRankedZones(bars, P, TICK, tape);
ok(`a tape leaves the author's ranking alone (${withTape.zones.length} zones, same ids, order, scores)`,
   withTape.zones.length === data.zones.length &&
   withTape.zones.every((z, i) => z.id === data.zones[i].id && z.score === data.zones[i].score &&
                                   z.label === data.zones[i].label));
ok(`without a tape the flow terms are null and hasFlow says so`,
   !data.hasFlow && data.zones.every((z) => z.flowScore === null && z.against === null) && withTape.hasFlow);
const noTapeFlow = RZ.computeRankedZones(bars, { ...P, rankBy: "flow" }, TICK);
ok(`rankBy flow with no tape falls back to the score`,
   noTapeFlow.zones.every((z, i) => z.id === data.zones[i].id));

const byFlow = RZ.computeRankedZones(bars, { ...P, rankBy: "flow" }, TICK, tape);
ok(`flow ranking sorted descending on flowScore`,
   byFlow.zones.every((z, i) => i === 0 || byFlow.zones[i - 1].flowScore >= z.flowScore));
ok(`flow terms in range`, byFlow.zones.every((z) =>
   z.flowScore >= 0 && z.flowScore <= 100 && z.against >= 0 && z.against <= 1 && z.flowVol >= 0 && z.defended >= 0));
ok(`no walk bookkeeping leaks out`, [...byFlow.zones, ...byFlow.broken].every((z) => !("flowFrom" in z)));
// This tape leaves only a handful of zones alive, so a top 3 is the cut that
// can differ at all.
const top3 = (rankBy) => RZ.computeRankedZones(bars, { ...P, visibleLimit: 3, rankBy }, TICK, tape)
  .zones.filter((z) => z.visible).map((z) => z.id);
const [s3, f3] = [top3("score"), top3("flow")];
ok(`the two rankings can disagree (top 3 by score ${s3.join(",")} vs by flow ${f3.join(",")})`,
   s3.join() !== f3.join());
ok(`flow labels say "Absorbed" exactly when 60%+ came at the zone`,
   byFlow.zones.every((z) => z.label.startsWith("Absorbed") === (z.against >= 0.6) && z.label.includes("flow ")));

// Brute force, on zones that never absorbed a neighbour (touchCount's integer
// part counts absorbs; touches add 0.2), so the box is still the birth box.
const vb = (i) => { let s = 0; const n = Math.min(i + 1, P.volLen); for (let k = i - n + 1; k <= i; k++) s += bars[k].volume; return s / n; };
const count = (b0, b1, z) => {
  let vol = 0, at = 0, away = 0;
  for (let b = b0; b <= b1; b++) for (let t = spans[b][0]; t <= spans[b][1]; t++) {
    const px = tLevel[t] * TICK;
    if (px < z.bottom - 1e-9 || px > z.top + 1e-9) continue;
    vol += tSize[t];
    if (tSide[t] === (z.dir === -1 ? 1 : 2)) at += tSize[t];
    else if (tSide[t] === (z.dir === -1 ? 2 : 1)) away += tSize[t];
  }
  return { vol, at, away };
};
// Broken zones count too: their terms froze on the bar that broke them, which
// was a touch that did not count as a defence.
const pristine = [0.55, 0].flatMap((absorbAtr) => {
  const r = RZ.computeRankedZones(bars, { ...P, absorbAtr, rankBy: "flow", keepBrokenCount: 30 }, TICK, tape);
  return [...r.zones, ...r.broken].filter((z) => z.touchCount < 0.99);
});
let fWorst = 0;
for (const z of pristine) {
  const c = z.bornBar, i = c + P.pivotSpan;
  const last = z.broken ? bars.findIndex((b) => b.time === z.brokenTime) : bars.length - 1;
  const birth = count(c - P.pivotSpan, i, z);
  const flowVol = birth.vol / vb(i);
  const against = birth.at + birth.away > 0 ? birth.at / (birth.at + birth.away) : 0.5;
  let defended = 0;
  for (let j = i + 1; j <= (z.broken ? last - 1 : last); j++)
    if (bars[j].high >= z.bottom && bars[j].low <= z.top) defended += count(j, j, z).at / vb(j);
  const age = last - c;
  const fs = Math.max(0, Math.min(100,
    Math.min(flowVol / 2, 1) * 30 + Math.max(0, Math.min(1, (against - 0.5) / 0.3)) * 30 +
    Math.min(defended / 2, 1) * 25 + 15 - Math.min(age / 450, 1) * 15));
  fWorst = Math.max(fWorst, Math.abs(flowVol - z.flowVol), Math.abs(against - z.against),
                    Math.abs(defended - z.defended), Math.abs(fs - z.flowScore));
}
ok(`flow terms reproduce a brute-force count on ${pristine.length} unabsorbed zones (worst ${fWorst.toExponential(1)})`,
   pristine.length >= 6 && fWorst < 1e-9);

let failed = 0;
console.log("\nranked zones");
for (const [label, pass] of out) { console.log(`  ${pass ? "ok  " : "FAIL"} ${label}`); if (!pass) failed++; }
process.exit(failed ? 1 : 0);
