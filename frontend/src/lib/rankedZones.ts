// Ranked Support & Resistance Zones [Zeiierman], ported to the chart.
//
// Ported from the published Pine v6 source (TradingView jTQU8WBS, CC BY-NC-SA
// 4.0, © Zeiierman), pulled with tools/pine-fetch and transcribed line by line —
// the same provenance and the same licence as lib/dynamicSwingVwap, which is the
// other Zeiierman port here. Non-commercial, share-alike: fine for a private
// journal, a constraint the day any of this is distributed.
//
// What it is: pivots become fixed-width zones, zones absorb their neighbours,
// and every zone carries a score that decays. The score is the whole point —
// this is not another "draw a box at every swing" layer, it is a *ranking*, and
// only the top `visibleLimit` are drawn. Six terms feed it (width, volume at the
// pivot, trend agreement, how cleanly the swing stood out, how often price came
// back, and age) and two subtract (mitigation, age again).
//
// Read it the way the rest of this chart's borrowed layers are read — as a
// reading surface, unvalidated, nothing in the sim or the strategy engine sees
// it. Worth saying twice for this one, because it *looks* like an edge: a
// ranked list with percentages on it invites exactly the trust that
// docs/research has repeatedly failed to find in market structure and stable
// S/R (see the structure and level studies — forward-null at every scale). The
// ranking is a way of sorting what is on screen, not evidence that the top one
// holds.
//
// Three deliberate divergences from the source, all noted where they happen:
//
//   * **Pivots are strict on both sides.** The script calls `ta.pivothigh`; the
//     runtime our community studies ride implements that strictly (measured),
//     and lib/modernVwap's port already spells the same rule out by hand. Only
//     exact-equal plateaus differ, which continuous futures prices essentially
//     never produce.
//   * **No alerts.** The source ends in eleven `alertcondition`s. This chart has
//     its own alert surface and wiring a second one through a layer would be a
//     second personality; the events are all derivable from the returned zones.
//   * **No dashboard, no event labels.** Both are described in the source's
//     tooltips and neither is in the source — the table was cut before
//     publication and `eventLabel` is declared, deleted, and never created.

import type { Bar } from "./chartTypes";

/** Direction: the Pine's `dir`, kept as its numbers so the scoring transcribes
 *  unchanged. -1 is a zone built from a pivot *low* — support. */
export type ZoneDir = 1 | -1;

export type ZoneFilter = "all" | "support" | "resistance";

/** Which number the ranking sorts on: the author's score, or the tape-derived
 *  flow score. Not a blend: the two rank the same zones independently. */
export type ZoneRankBy = "score" | "flow";

/**
 * The prints behind the bars: what the flow score reads. Optional, because the
 * port stands without it; a chart with no tape just has no flow ranking.
 *
 * `span(b)` is bar `b`'s inclusive tick-index range into the arrays (the replay
 * engine's `i0`/`i1`), or null for a bar with nothing behind it.
 */
export interface ZoneTape {
  level: Int32Array;
  size: Int32Array;
  /** Aggressor, as `Tape.side`: 1 = sell (hit the bid), 2 = buy (lifted the offer). */
  side: Uint8Array;
  tickSize: number;
  span(bar: number): [number, number] | null;
}

export interface RankedZonesParams {
  /** How many of the highest-ranked zones are drawn. */
  visibleLimit: number;
  /** How many are kept alive internally before the lowest-ranked are dropped. */
  storedLimit: number;
  /** Pivot confirmation length: the centre bar must beat `pivotSpan` neighbours
   *  on each side, so a pivot confirms `pivotSpan` bars after it happened. */
  pivotSpan: number;
  /** Minimum pivot displacement, in ATR, for a swing to become a zone. */
  minSwingAtr: number;
  /** Zones whose mids are within this many ATR merge instead of stacking. */
  absorbAtr: number;
  /** Zone thickness, in ATR. */
  zoneAtrWidth: number;
  volLen: number;
  trendLen: number;
  /** How far past a zone price must *close* before it counts as broken. */
  breakAtr: number;
  /** How many broken zones stay on the chart, newest first. */
  keepBrokenCount: number;
  direction: ZoneFilter;
  /** What the ranking, the stored cap and the draw cap all sort on. */
  rankBy: ZoneRankBy;
  strengthBars: boolean;
  zoneText: boolean;
}

// Shortlists rather than free numbers, the way every other knob on this chart
// works — these are the source's own defaults with a spread either side, not a
// measured range, and the settings panel says as much.
export const RZ_VISIBLE_OPTIONS = [3, 5, 8, 12, 20, 30] as const;
export const RZ_STORED_OPTIONS = [20, 40, 60, 100, 160, 250] as const;
export const RZ_SPAN_OPTIONS = [3, 5, 8, 12, 20, 30] as const;
export const RZ_MIN_SWING_OPTIONS = [0, 0.1, 0.15, 0.25, 0.5, 1] as const;
export const RZ_ABSORB_OPTIONS = [0, 0.25, 0.55, 0.9, 1.5, 3] as const;
export const RZ_WIDTH_OPTIONS = [0.1, 0.25, 0.4, 0.6, 1, 2] as const;
export const RZ_VOL_LEN_OPTIONS = [10, 20, 50, 100] as const;
export const RZ_TREND_LEN_OPTIONS = [20, 50, 100, 200] as const;
export const RZ_BREAK_OPTIONS = [0, 0.06, 0.12, 0.25, 0.5, 1] as const;
export const RZ_KEEP_BROKEN_OPTIONS = [0, 2, 4, 8, 16, 30] as const;
export const RZ_RANK_OPTIONS = [
  { value: "score", label: "Author's score" },
  { value: "flow", label: "Order flow" },
] as const;
export const RZ_FILTER_OPTIONS = [
  { value: "all", label: "Both sides" },
  { value: "support", label: "Support only" },
  { value: "resistance", label: "Resistance only" },
] as const;

export const DEFAULT_RANKED_ZONES: RankedZonesParams = {
  visibleLimit: 8,
  storedLimit: 60,
  pivotSpan: 5,
  minSwingAtr: 0.15,
  absorbAtr: 0.55,
  zoneAtrWidth: 0.4,
  volLen: 20,
  trendLen: 50,
  breakAtr: 0.12,
  keepBrokenCount: 4,
  direction: "all",
  rankBy: "score",
  strengthBars: true,
  zoneText: true,
};

/** A zone's age ceiling, in bars — it is deleted here, and the same number
 *  normalises the age penalty. The source hard-codes 450 in both places. */
const MAX_AGE = 450;

/** The mitigation share at which a zone reads as "Mitigated" rather than by
 *  strength. The source's threshold, used for the label and nothing else. */
const MITIGATED_AT = 0.75;

export interface RankedZone {
  /** Stable across recomputes: the pivot that created it. Two zones cannot share
   *  one, because a second pivot at the same bar on the same side absorbs. */
  id: string;
  dir: ZoneDir;
  top: number;
  bottom: number;
  mid: number;
  width: number;
  /** Bar index and bar time of the pivot this grew from — where the box starts. */
  bornBar: number;
  leftTime: number;
  /** 0-100. What the ranking sorts on. */
  score: number;
  /** 0-1: how deeply price has eaten into the zone on its deepest visit. */
  mitigation: number;
  touchCount: number;
  volScore: number;
  trendScore: number;
  swingScore: number;
  /** 0-100, the two halves of the strength bar. */
  bullStrength: number;
  bearStrength: number;
  broken: boolean;
  /** Bar time the break happened — where a broken zone's box stops. */
  brokenTime: number | null;
  /** Position in the ranking, 0 = best. */
  rank: number;
  /** Passed the direction filter *and* made the top `visibleLimit`. */
  visible: boolean;
  /** "Strong Support", "Mitigated Resistance" — the source's own wording. */
  label: string;
  /** The tape's terms, none of them in the source. All null without a tape.
   *
   *  flowVol    volume that printed *inside the zone* across the pivot window,
   *             over the bar-volume baseline. The bar version (`volScore`)
   *             counts a whole bar's volume, most of which traded nowhere near
   *             the swing.
   *  against    share of that window's in-zone aggression that came *at* the
   *             zone: sells into support, buys into resistance. The pivot held
   *             by construction, so a high share is aggression that went
   *             nowhere: absorption.
   *  defended   aggression into the zone on later touches that did not break
   *             it, summed, over the baseline. A break ends the zone, so this
   *             only ever counts pressure that failed.
   *  flowScore  0-100, what `rankBy: "flow"` sorts on. */
  flowVol: number | null;
  against: number | null;
  defended: number | null;
  flowScore: number | null;
}

/** A zone mid-walk: the bar index touches start counting from, so bars already
 *  read as a birth window are not counted again as a defence. Stripped before
 *  the zones are returned. */
type WalkZone = RankedZone & { flowFrom: number };

export interface RankedZonesData {
  /** Live zones, best first. */
  zones: RankedZone[];
  /** Broken ones still on the chart, oldest first — the source's FIFO. */
  broken: RankedZone[];
  /** Nearest zone mid on each side of the last close, over every live zone
   *  rather than only the drawn ones. */
  nearestSupport: number | null;
  nearestResistance: number | null;
  /** Whether a tape was behind the walk, i.e. whether the flow terms exist.
   *  With `rankBy: "flow"` and no tape the walk falls back to the score. */
  hasFlow: boolean;
  /** The ATR the last bar's zones were sized in — the legend quotes it, because
   *  every distance knob on this layer is in ATR and the number is otherwise
   *  invisible. */
  atr: number;
}

const EMPTY: RankedZonesData = {
  zones: [],
  broken: [],
  nearestSupport: null,
  nearestResistance: null,
  hasFlow: false,
  atr: 0,
};

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Wilder's ATR, which is what `ta.atr` is: an RMA of true range. */
function atrSeries(bars: Bar[], len: number): Float64Array {
  const n = bars.length;
  const out = new Float64Array(n);
  if (!n) return out;
  let rma = 0;
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    const tr =
      i === 0
        ? b.high - b.low
        : Math.max(
            b.high - b.low,
            Math.abs(b.high - bars[i - 1].close),
            Math.abs(b.low - bars[i - 1].close),
          );
    // RMA seeds on the simple mean of the first `len` values, then decays at
    // 1/len — the seeding half is why this is not just an EMA with α=1/len.
    if (i < len) {
      rma = (rma * i + tr) / (i + 1);
    } else {
      rma = (rma * (len - 1) + tr) / len;
    }
    out[i] = rma;
  }
  return out;
}

function smaSeries(vals: Float64Array, len: number): Float64Array {
  const n = vals.length;
  const out = new Float64Array(n);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum += vals[i];
    if (i >= len) sum -= vals[i - len];
    out[i] = sum / Math.min(i + 1, len);
  }
  return out;
}

function emaSeries(bars: Bar[], len: number): Float64Array {
  const n = bars.length;
  const out = new Float64Array(n);
  const k = 2 / (len + 1);
  let e = 0;
  for (let i = 0; i < n; i++) {
    // Pine's `ta.ema` seeds on an SMA of the first `len` bars and is `na` before
    // that. The running mean here lands on exactly that SMA at bar `len - 1` and
    // decays identically after, so the two agree to float noise everywhere Pine
    // defines a value — and this one is additionally defined during the seed
    // window, where a zone born early would otherwise have no trend to score
    // against.
    e = i === 0 ? bars[0].close : i < len ? (e * i + bars[i].close) / (i + 1) : bars[i].close * k + e * (1 - k);
    out[i] = e;
  }
  return out;
}

/**
 * How cleanly a pivot stood out from the bars either side of it, in ATR.
 *
 * The source reads `high[pivotSpan - 1]` and `high[pivotSpan + 1]` at the
 * confirmation bar, which are the pivot's two immediate neighbours — so this is
 * deliberately a *local* measure over three bars, not the whole pivot window.
 * A pivot that beat twenty bars by a hair scores near zero here, and that is the
 * intent: `minSwingAtr` is a displacement filter, not a prominence one.
 */
function swingQuality(bars: Bar[], centre: number, dir: ZoneDir, atr: number): number {
  const price = dir === 1 ? bars[centre].high : bars[centre].low;
  const before = bars[centre - 1];
  const after = bars[centre + 1];
  if (!before || !after || atr <= 0) return 0;
  return dir === 1
    ? Math.max((price - Math.max(before.high, after.high)) / atr, 0)
    : Math.max((Math.min(before.low, after.low) - price) / atr, 0);
}

/** The ranking. Six terms up, two down, clamped to 0-100. Transcribed as-is:
 *  the weights are the author's and there is nothing to be gained by tidying
 *  them into something that no longer matches the source. */
function scoreOf(
  width: number,
  volScore: number,
  trendScore: number,
  swingScore: number,
  mitigation: number,
  touches: number,
  age: number,
  atr: number,
): number {
  const sizeNorm = Math.min(width / (atr || 1), 1);
  const volNorm = Math.min(volScore / 2, 1);
  const swingNorm = Math.min(swingScore / 1.5, 1);
  const touchNorm = Math.min(touches / 4, 1);
  const ageNorm = Math.min(age / MAX_AGE, 1);
  const raw = sizeNorm * 20 + volNorm * 18 + trendScore * 12 + swingNorm * 28 + touchNorm * 16 + 10;
  const penalty = mitigation * 22 + ageNorm * 10;
  return clamp(raw - penalty, 0, 100);
}

/** The two halves of the strength bar. The zone's own side gets a piecewise
 *  stretch of its score — 75+ maps to 72-100, 45-75 to 48-70, below that
 *  roughly linear — and the opposite side is driven almost entirely by how far
 *  price has already eaten in. */
function strengthsOf(
  dir: ZoneDir,
  score: number,
  trendScore: number,
  mitigation: number,
): [number, number] {
  const s = clamp(score, 0, 100);
  let side = s >= 75 ? 72 + (s - 75) * 1.12 : s >= 45 ? 48 + (s - 45) * 0.75 : s * 1.05;
  side += trendScore * 6;
  side -= mitigation * 28;
  side = clamp(side, 0, 100);
  const opposite = clamp(5 + mitigation * 65 + s * 0.08, 0, 100);
  // `int()` in Pine truncates toward zero.
  const bull = Math.trunc(dir === -1 ? side : opposite);
  const bear = Math.trunc(dir === 1 ? side : opposite);
  return [bull, bear];
}

/** A share of in-zone aggression reads as absorption from here up. An even
 *  split is 0.5, so this is "clearly more came at it than left it". */
const ABSORBED_AT = 0.6;

/**
 * The flow ranking, 0-100. Three terms up, age down, nothing from the author's
 * score, so the two rankings can disagree. The weights are guesses, the same
 * status as the author's: nothing here has been tested against whether a zone
 * holds, and every order-flow study in docs/research (participation floor,
 * big-trade flow, loser flow, structure x flow) came back null. Another way to
 * sort the screen, not a better one.
 */
function flowScoreOf(flowVol: number, against: number, defended: number, age: number): number {
  const volNorm = Math.min(flowVol / 2, 1);
  // An even split scores nothing, 0.8 scores the term out.
  const absorbNorm = clamp((against - 0.5) / 0.3, 0, 1);
  const defendNorm = Math.min(defended / 2, 1);
  const ageNorm = Math.min(age / MAX_AGE, 1);
  return clamp(volNorm * 30 + absorbNorm * 30 + defendNorm * 25 + 15 - ageNorm * 15, 0, 100);
}

/** Aggression inside [bottom, top] across bars b0..b1: all of it, the part that
 *  came at the zone, and the part that went the other way. */
function inZoneFlow(
  tape: ZoneTape,
  b0: number,
  b1: number,
  bottom: number,
  top: number,
  dir: ZoneDir,
): { vol: number; at: number; away: number } {
  const lo = Math.ceil(bottom / tape.tickSize - 1e-9);
  const hi = Math.floor(top / tape.tickSize + 1e-9);
  // Support (-1) is hit by sellers; resistance by buyers.
  const atSide = dir === -1 ? 1 : 2;
  const awaySide = dir === -1 ? 2 : 1;
  let vol = 0;
  let at = 0;
  let away = 0;
  for (let b = Math.max(0, b0); b <= b1; b++) {
    const r = tape.span(b);
    if (!r) continue;
    for (let t = r[0]; t <= r[1]; t++) {
      const lv = tape.level[t];
      if (lv < lo || lv > hi) continue;
      const q = tape.size[t];
      vol += q;
      const sd = tape.side[t];
      if (sd === atSide) at += q;
      else if (sd === awaySide) away += q;
    }
  }
  return { vol, at, away };
}

function labelOf(z: RankedZone, rankBy: ZoneRankBy): string {
  const noun = z.dir === -1 ? "Support" : "Resistance";
  if (rankBy === "flow" && z.flowScore != null) {
    const kind = (z.against ?? 0) >= ABSORBED_AT ? `Absorbed ${noun}` : noun;
    return `${kind} · flow ${Math.round(z.flowScore)}`;
  }
  if (z.mitigation >= MITIGATED_AT) return `Mitigated ${noun}`;
  const s = z.dir === -1 ? z.bullStrength : z.bearStrength;
  return s >= 70 ? `Strong ${noun}` : s >= 45 ? noun : `Weak ${noun}`;
}

/** Does a new zone belong to one that already exists? Same side, and either the
 *  mids are within `absorbAtr` or the two overlap by more than a third of the
 *  narrower one. */
function overlaps(
  z: RankedZone,
  newTop: number,
  newBottom: number,
  newDir: ZoneDir,
  absorbAtr: number,
  atr: number,
  tick: number,
): boolean {
  if (z.dir !== newDir) return false;
  const overlap = Math.max(Math.min(z.top, newTop) - Math.max(z.bottom, newBottom), 0);
  const smaller = Math.min(Math.max(z.top - z.bottom, tick), Math.max(newTop - newBottom, tick));
  const closeMid = Math.abs(z.mid - (newTop + newBottom) / 2) <= absorbAtr * atr;
  return closeMid || overlap / smaller >= 0.35;
}

/**
 * Every zone the tape has produced, ranked, as of the last bar.
 *
 * One pass, bar by bar, because the whole model is path-dependent: a zone's
 * score depends on how many times price came back to it and how deep it got,
 * and a zone that broke is gone. There is no way to evaluate the last bar
 * without having walked the ones before it — which also means this is not a
 * per-tick computation. Snapshot and bar close, like every other layer whose
 * values are facts about closed bars.
 *
 * @param tick The instrument's tick size, standing in for the source's
 *             `syminfo.mintick`: a floor on zone width so a zero-width zone
 *             cannot divide by zero.
 */
export function computeRankedZones(
  bars: Bar[],
  p: RankedZonesParams,
  tick: number,
  tape: ZoneTape | null = null,
): RankedZonesData {
  const n = bars.length;
  const span = Math.max(1, Math.round(p.pivotSpan));
  if (n < 2 * span + 2) return EMPTY;

  const atrs = atrSeries(bars, 14);
  const vols = new Float64Array(n);
  for (let i = 0; i < n; i++) vols[i] = bars[i].volume;
  const volBase = smaSeries(vols, Math.max(1, Math.round(p.volLen)));
  const trendBase = emaSeries(bars, Math.max(1, Math.round(p.trendLen)));
  const floor = tick > 0 ? tick : 0.01;
  const byFlow = p.rankBy === "flow" && tape != null;
  const key = byFlow ? (z: RankedZone) => z.flowScore ?? 0 : (z: RankedZone) => z.score;

  let live: WalkZone[] = [];
  let broken: WalkZone[] = [];

  for (let i = 0; i < n; i++) {
    const bar = bars[i];
    // The source falls back to ten ticks when ATR is unavailable, which is the
    // first fourteen bars of any tape.
    const atr = atrs[i] > 0 ? atrs[i] : floor * 10;

    // --- a pivot confirms, `span` bars after it happened ---------------------
    const c = i - span;
    if (c - span >= 0) {
      let isHigh = true;
      let isLow = true;
      for (let j = c - span; j <= c + span && (isHigh || isLow); j++) {
        if (j === c) continue;
        if (bars[j].high >= bars[c].high) isHigh = false;
        if (bars[j].low <= bars[c].low) isLow = false;
      }
      // Both can fire on the same bar — one outside bar is two pivots, as there.
      if (isHigh) addZone(bars[c].high, 1, c);
      if (isLow) addZone(bars[c].low, -1, c);
    }

    function addZone(price: number, dir: ZoneDir, centre: number) {
      const swingScore = swingQuality(bars, centre, dir, atr);
      if (swingScore < p.minSwingAtr) return;
      const half = p.zoneAtrWidth * atr * 0.5;
      const top = price + half;
      const bottom = price - half;
      const width = Math.max(top - bottom, floor);
      const volScore = volBase[i] !== 0 ? bars[centre].volume / volBase[i] : 1;
      const trendScore =
        (dir === -1 && price > trendBase[i]) || (dir === 1 && price < trendBase[i]) ? 1 : 0;
      const age = i - centre;
      const score = scoreOf(width, volScore, trendScore, swingScore, 0, 0, age, atr);
      const [bull, bear] = strengthsOf(dir, score, trendScore, 0);

      // The pivot window, both sides of the centre, read off the prints. Only
      // what traded inside the zone counts.
      let flowVol: number | null = null;
      let against: number | null = null;
      if (tape) {
        const f = inZoneFlow(tape, centre - span, i, bottom, top, dir);
        flowVol = volBase[i] > 0 ? f.vol / volBase[i] : 0;
        const aggr = f.at + f.away;
        against = aggr > 0 ? f.at / aggr : 0.5;
      }

      const host = live.find((z) => overlaps(z, top, bottom, dir, p.absorbAtr, atr, floor));
      if (host) {
        // Absorbed: the survivor widens to cover both and keeps the better of
        // every score term. Note the mitigation reset — price coming back to
        // make a fresh pivot at a level is treated as the level being *renewed*,
        // not as another visit eating into it.
        host.top = Math.max(host.top, top);
        host.bottom = Math.min(host.bottom, bottom);
        host.mid = (host.top + host.bottom) / 2;
        host.width = Math.max(host.top - host.bottom, floor);
        host.bornBar = Math.min(host.bornBar, centre);
        host.leftTime = Math.min(host.leftTime, bars[centre].time);
        host.volScore = Math.max(host.volScore, volScore);
        host.trendScore = Math.max(host.trendScore, trendScore);
        host.swingScore = Math.max(host.swingScore, swingScore);
        host.mitigation = 0;
        host.touchCount += 1;
        host.score = Math.max(host.score, score) + 3;
        host.bullStrength = Math.max(host.bullStrength, bull);
        host.bearStrength = Math.max(host.bearStrength, bear);
        // The better of each birth term, as the author does with theirs.
        // `defended` is kept: those visits happened and failed. Touches count
        // from here on, since this window has just been read as a birth.
        if (flowVol != null && against != null) {
          host.flowVol = Math.max(host.flowVol ?? 0, flowVol);
          host.against = Math.max(host.against ?? 0, against);
        }
        host.flowFrom = i;
        return;
      }
      live.push({
        id: `${bars[centre].time}:${dir}`,
        dir,
        top,
        bottom,
        mid: price,
        width,
        bornBar: centre,
        leftTime: bars[centre].time,
        score,
        mitigation: 0,
        touchCount: 0,
        volScore,
        trendScore,
        swingScore,
        bullStrength: bull,
        bearStrength: bear,
        broken: false,
        brokenTime: null,
        rank: 0,
        visible: false,
        label: "",
        flowVol,
        against,
        defended: tape ? 0 : null,
        flowScore: tape ? flowScoreOf(flowVol ?? 0, against ?? 0.5, 0, age) : null,
        flowFrom: i,
      });
    }

    // --- then every live zone is re-read against this bar --------------------
    for (let k = live.length - 1; k >= 0; k--) {
      const z = live[k];
      const age = i - z.bornBar;
      const touched = bar.high >= z.bottom && bar.low <= z.top;
      const brokeSupport = z.dir === -1 && bar.close < z.bottom - p.breakAtr * atr;
      const brokeResistance = z.dir === 1 && bar.close > z.top + p.breakAtr * atr;

      if (touched && !brokeSupport && !brokeResistance) {
        z.touchCount += 0.2;
        // Assigned, not accumulated — a later shallow visit *lowers* mitigation
        // again. Faithful to the source, and defensible: it reads "how far in is
        // price right now", not "how much of this zone has ever been eaten".
        const fill = z.dir === -1 ? z.top - bar.low : bar.high - z.bottom;
        z.mitigation = clamp(fill / Math.max(z.width, floor), 0, 1);
        // Pressure that came at the zone and failed to break it.
        if (tape && i > z.flowFrom && volBase[i] > 0) {
          const f = inZoneFlow(tape, i, i, z.bottom, z.top, z.dir);
          z.defended = (z.defended ?? 0) + f.at / volBase[i];
        }
      }
      if (tape) z.flowScore = flowScoreOf(z.flowVol ?? 0, z.against ?? 0.5, z.defended ?? 0, age);

      z.score = scoreOf(z.width, z.volScore, z.trendScore, z.swingScore, z.mitigation, z.touchCount, age, atr);
      const [bull, bear] = strengthsOf(z.dir, z.score, z.trendScore, z.mitigation);
      z.bullStrength = bull;
      z.bearStrength = bear;

      if (age > MAX_AGE) {
        live.splice(k, 1);
      } else if (brokeSupport || brokeResistance) {
        z.broken = true;
        z.brokenTime = bar.time;
        live.splice(k, 1);
        if (p.keepBrokenCount > 0) {
          broken.push(z);
          while (broken.length > p.keepBrokenCount) broken.shift();
        }
      }
    }

    // Ranked every bar, because the cap below cuts from the bottom of it.
    live.sort((a, b) => key(b) - key(a));
    if (live.length > p.storedLimit) live = live.slice(0, p.storedLimit);
  }

  // --- what is drawn, and what price is between ------------------------------
  const close = bars[n - 1].close;
  let shown = 0;
  let nearestSupport: number | null = null;
  let nearestResistance: number | null = null;
  live.forEach((z, idx) => {
    const passes =
      p.direction === "all" ||
      (p.direction === "support" && z.dir === -1) ||
      (p.direction === "resistance" && z.dir === 1);
    z.rank = idx;
    z.visible = passes && shown < p.visibleLimit;
    if (z.visible) shown++;
    z.label = labelOf(z, byFlow ? "flow" : "score");
    // Nearest is read off every live zone, not only the drawn ones: it answers
    // "what is price between", and a level does not stop being there because it
    // ranked ninth.
    if (z.dir === -1 && z.mid < close)
      nearestSupport = nearestSupport == null || z.mid > nearestSupport ? z.mid : nearestSupport;
    if (z.dir === 1 && z.mid > close)
      nearestResistance =
        nearestResistance == null || z.mid < nearestResistance ? z.mid : nearestResistance;
  });
  for (const z of broken) z.label = labelOf(z, byFlow ? "flow" : "score");
  const strip = ({ flowFrom: _, ...z }: WalkZone): RankedZone => z;

  return {
    zones: live.map(strip),
    broken: broken.map(strip),
    nearestSupport,
    nearestResistance,
    hasFlow: tape != null,
    atr: atrs[n - 1] > 0 ? atrs[n - 1] : floor * 10,
  };
}

const pick = <T,>(v: unknown, options: readonly T[], fallback: T): T =>
  options.includes(v as T) ? (v as T) : fallback;

/** A persisted settings blob, made safe. Same contract as the other ports':
 *  anything unrecognised falls back to the default rather than throwing, so a
 *  saved workspace survives this file changing its mind about an option list. */
export function rankedZonesParams(raw: unknown): RankedZonesParams {
  const d = DEFAULT_RANKED_ZONES;
  const s = (raw ?? {}) as Partial<RankedZonesParams>;
  return {
    visibleLimit: pick(s.visibleLimit, RZ_VISIBLE_OPTIONS, d.visibleLimit),
    storedLimit: pick(s.storedLimit, RZ_STORED_OPTIONS, d.storedLimit),
    pivotSpan: pick(s.pivotSpan, RZ_SPAN_OPTIONS, d.pivotSpan),
    minSwingAtr: pick(s.minSwingAtr, RZ_MIN_SWING_OPTIONS, d.minSwingAtr),
    absorbAtr: pick(s.absorbAtr, RZ_ABSORB_OPTIONS, d.absorbAtr),
    zoneAtrWidth: pick(s.zoneAtrWidth, RZ_WIDTH_OPTIONS, d.zoneAtrWidth),
    volLen: pick(s.volLen, RZ_VOL_LEN_OPTIONS, d.volLen),
    trendLen: pick(s.trendLen, RZ_TREND_LEN_OPTIONS, d.trendLen),
    breakAtr: pick(s.breakAtr, RZ_BREAK_OPTIONS, d.breakAtr),
    keepBrokenCount: pick(s.keepBrokenCount, RZ_KEEP_BROKEN_OPTIONS, d.keepBrokenCount),
    direction: pick(s.direction, RZ_FILTER_OPTIONS.map((o) => o.value), d.direction),
    rankBy: pick(s.rankBy, RZ_RANK_OPTIONS.map((o) => o.value), d.rankBy),
    strengthBars: typeof s.strengthBars === "boolean" ? s.strengthBars : d.strengthBars,
    zoneText: typeof s.zoneText === "boolean" ? s.zoneText : d.zoneText,
  };
}
