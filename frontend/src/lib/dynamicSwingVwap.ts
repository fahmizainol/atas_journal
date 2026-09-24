import { parseTimeframeId, TIMEFRAMES } from "./timeframes";
import { barMoments } from "./vwap";

// Dynamic Swing Anchored VWAP [Zeiierman], ported to the chart.
//
// Ported from the published Pine v6 source (TradingView SxgyrEde, CC BY-NC-SA
// 4.0, © Zeiierman) rather than from its description — the two disagree in
// several places and the source wins. It is the second swing-anchored VWAP on
// this chart and it is *not* the one in lib/modernVwap; they differ in every part
// that matters:
//
//   * **Where it anchors.** Modern VWAP re-anchors at every confirmed centred
//     pivot. This one anchors only when the swing *direction flips*, and its
//     notion of a swing is a **latch**, not a rolling window: `phL` moves to the
//     current bar only on a bar that makes a new `swingPeriod`-bar high, and
//     otherwise holds — so the remembered high can be far older than the window
//     itself. Direction is simply whichever latch fired more recently. A rolling
//     argmax would re-flip every time an old extreme aged out of the window and
//     finds several times as many anchors; this does not.
//   * **How it averages.** Not a cumulative sum. Both accumulators decay each
//     bar, so recent price×volume outweighs older and the line tracks price
//     instead of drifting away the way a session VWAP does by lunchtime.
//   * **How fast it tracks.** Optionally the decay's half-life moves with
//     volatility — ATR against its own average, raised to `volBias`.
//
// `weighting` turns the second of those off. On `cumulative` the accumulators
// stop decaying, stop being seeded with the pivot's wick, and fold each bar's
// *tick* moments rather than its hlc3 — the three things that make a segment
// differ from a plain anchored VWAP. A segment then equals, to the last decimal,
// what the ⚓ tool draws anchored on the same candle: `Σ(p·v)/Σv` over the tape
// from the pivot bar forward, with the σ rings its volume-weighted spread. That
// last part is the one worth naming, because it is not a rounding difference:
// accumulating trade prices and accumulating bar typical prices are different
// statistics, and near an anchor the sigmas differ by tens of times. See
// lib/vwap. Everything else is untouched — the latch still decides where the
// anchors land, the segments still freeze on a flip, and the leg you are in is
// still the one being averaged. It is there for exactly one question — *is this
// leg's line telling me something an ordinary aVWAP off the same swing would
// not?* — and with it on, `apt`, `adaptApt` and `volBias` are not read at all.
//
// Note what that means for the σ rings: under `cumulative` they do **not** open
// pinched, because the anchor bar already carries its own ticks' spread. The
// pinch below is a fact about the decayed path only.
//
// **The σ bands are ours, not his.** The script draws the line and the swing
// labels and nothing else. The envelope offered here is the decayed second
// moment of the same accumulator — volume-weighted σ of hlc3 about the segment's
// own VWAP, under the same decay — so it answers "how stretched is price from
// *this leg's* mean", on a window that shortens and lengthens with `apt` exactly
// as the mid line does. Two things follow that a fixed-window band would not do,
// and both are honest rather than bugs: the envelope opens **pinched** at each
// anchor and flares out over the next `apt` bars or so, because a decayed
// variance over the anchor bar's two observations is nearly nothing (exactly
// `√(α(1−α))·|wick − hlc3|`, a fifth of the wick-to-typical distance at the
// default half-life); and it re-widens whenever the volatility adjustment
// shortens the half-life, since a shorter memory is a smaller effective sample.
// It is off by default, because the indicator as published does not have it.
//
// `bandScope` decides who gets an envelope. On `live` — the default — only the
// leg you are in, which is the leg the question is usually about. On `all` every
// frozen segment gets its rings too, which is a genuinely different picture: a
// hundred overlapping envelopes showing where each past leg's spread stood when
// it was cut. Dense, and meant for looking rather than reading. The ±1σ→±2σ wash
// is never drawn on the frozen ones — a hundred stacked fills is a smear, and
// the rings alone carry the shape.
//
// ## The three details that are easy to get wrong
//
// **A segment starts at the wick.** The running average is over `hlc3`, but the
// anchor bar is seeded with the *pivot's extreme* — `p := y * volume[barsback]`,
// where `y` is the swing low under a bullish leg and the swing high over a
// bearish one — and then the accumulation loop immediately folds that same bar in
// at `hlc3`. So the segment's first value is `decay·wick + α·hlc3`, which at the
// default half-life is about 97% the wick. The line leaves the wick and decays in
// toward the volume-weighted mean from there. (Dropped under `cumulative`: an
// unweighted sum never forgets the seed, so the wick would sit in the average for
// the whole leg rather than fading out of it over the first few bars.)
//
// **Superseded segments are frozen, not handed over.** On a flip the script does
// `polyline.new(vwap.points, …)` *without assigning the result* — the old segment
// is orphaned, left on the chart exactly as it stood, and the points array is
// cleared for the new one. The old polyline ends on the bar *before* the flip;
// the new one starts back at the pivot, which is earlier. So the two overlap, and
// what you see is the new leg's average peeling away from the old one. They
// disappear only when Pine's `max_polylines_count = 100` evicts the oldest, which
// is why `MAX_SEGMENTS` below is 100 and not a number we chose.
//
// **The line therefore repaints, and that is the construct.** A flip is found on
// bar *i* but its segment is drawn from pivot bar *a* ≤ *i*. The cost is the one
// every repainting indicator has, and it is worth naming plainly because the rest
// of this chart is built to avoid it: **you did not see that segment at those
// prices while those bars were printing.** Never read a backtest off it. The
// *detection* is still causal — the latch rule needs no bars to the right, so no
// pivot ever appears that a live reader would not have found on the same bar —
// and the repaint never reaches back further than the pivot itself.
//
// One deliberate departure: **the colours are not his.** In the published source
// the live segment is drawn `dir > 0 ? R : S` and the frozen one `dir < 0 ? R : S`
// against the *new* direction, which works out to bullish legs in the "downtrend"
// colour and bearish legs in the "uptrend" one — consistently inverted, and
// contradicted by his own input tooltips. Read as a bug and not copied: a bullish
// leg is drawn in this app's up colour. Expect the hues to look swapped against
// TradingView.
//
// Everything is computed from the *drawn bars*, at whatever resolution the chart
// is showing — every window counts bars, not minutes, so a 50-bar swing period is
// 50 minutes on a 1m chart and 250 on a 5m one.
//
// ## `anchorTf`: the structure on somebody else's bars
//
// That last sentence is the whole reason this knob exists. Two panes on two
// bucketings run two different swing hunts, so they anchor their legs in
// different places and cannot be read against each other — which is the one thing
// a second pane is for. `anchorTf` moves the *structure* — the latches, the
// direction, the flips and the pivot bars — onto a chosen wall-clock bucketing,
// so every pane finds the same swings at the same prices whatever it is drawn on.
//
// Only the structure. Each leg still accumulates over the pane's own bars, so the
// line keeps the pane's resolution and its decay still counts pane bars: the
// panes agree on *where a leg starts*, not on every decimal of where it has got
// to. Making them agree on that too would mean drawing the anchor timeframe's
// line on every pane, held flat between its bars, and a staircase is a worse
// answer on the fast pane than a line that is merely its own.
//
// The grid is laid from the first session bar — the bell, on every pane of the
// replay, because its time buckets are measured off it — so a coarser grid falls
// on boundaries the pane's own bars already respect. Bars are then grouped by it,
// and the group's high and low are what the latches see. Two consequences:
//
//   * **It is inert below the pane's own bar.** A grid finer than the bars merges
//     nothing — every group holds one bar — so the structure is the pane's own
//     again. Nothing is gated on that; it simply falls out, and `anchored` on the
//     output says which happened so a legend need not claim otherwise.
//   * **It repaints one anchor bar further.** A flip lands on the *first* drawn
//     bar of the anchor bar that produced it, which is where the pane drawn at
//     that bucketing shows it — but on a finer pane that bar had not yet made the
//     high the flip was found on. The line already repaints back to its pivot;
//     this widens that stretch by up to one anchor bar, and the flag's stem draws
//     it as it always has.

/**
 * The bar this indicator needs, and nothing else.
 *
 * Structural rather than either chart's own `Bar`: the replay's carries tick
 * indices and the journal's does not, and nothing in here has ever read one.
 * Same shape lib/modernVwap asks for, and for the same reason.
 */
export interface DsvBar {
  /** ET wall-clock epoch seconds on the chart's gap-collapsing clock. */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  /** This bar's own tick VWAP and variance, read only under `cumulative` — see
   *  lib/vwap. The decayed path never touches them: it is his indicator, and his
   *  indicator averages hlc3. */
  tvwap?: number;
  tvar?: number;
}

// --- parameters -------------------------------------------------------------

/** What is drawn at each anchor. With a repainting line these are the only
 *  evidence a flip happened: every segment is drawn as though it always ran from
 *  its pivot, so nothing else on the pane records that it did not. */
export type DsvFlagMode = "labels" | "marks" | "off";

/** Whose σ envelope is drawn: the live leg alone, or every frozen segment too. */
export type DsvBandScope = "live" | "all";

/** How a segment averages: his decayed accumulator, or the ordinary cumulative
 *  one that makes each segment a plain anchored VWAP off its pivot. */
export type DsvWeighting = "decay" | "cumulative";

/** His four inputs, and nothing else. The style inputs (four colours and a line
 *  width) are the app's to decide, and the ATR lengths are constants in the
 *  source rather than inputs. */
export interface DsvParams {
  /** `prd` — bars used to detect swing highs and lows. A bar latches the high
   *  when it is the highest of the last `swingPeriod` bars, and the latch then
   *  holds until another bar beats it. */
  swingPeriod: number;
  /** Ours. Which bars the swing hunt runs on: a `lib/timeframes` id, or `""` for
   *  the pane's own. Set it and `swingPeriod` counts *those* bars, so two panes
   *  on two bucketings anchor their legs at the same swings — see the note above
   *  for what it does not equalise, and why a grid at or below the pane's own bar
   *  does nothing at all. */
  anchorTf: string;
  /** Ours. `decay` is his; `cumulative` drops both the decay and the wick seed,
   *  which is exactly what separates a segment from an ordinary anchored VWAP —
   *  so each leg then matches, value for value, an aVWAP anchored at the same
   *  candle. The three knobs below are unread under `cumulative`. */
  weighting: DsvWeighting;
  /** `baseAPT` — the decay's half-life in bars: `α = 1 − 2^(−1/apt)`. Lower
   *  tracks price more tightly, higher smooths. */
  apt: number;
  /** `useAdapt` — move the half-life with volatility. Off in his defaults. */
  adaptApt: boolean;
  /** `volBias` — the exponent on the ATR ratio. His default is 10, which is far
   *  stronger than it looks: the ratio is a smoothed ATR over its own smoothed
   *  average, so it sits near 1, and the tenth power is what turns a few percent
   *  of drift into a real change in tracking speed. Read only when `adaptApt`. */
  volBias: number;
  /** How many σ rings are drawn. 0 is off, and is the default: this is our
   *  addition, not part of the published indicator. */
  bands: 0 | 1 | 2 | 3;
  /** Which segments get those rings. Read only when `bands` is non-zero. */
  bandScope: DsvBandScope;
  /** How strongly the frozen segments behind the live one are drawn — the alpha
   *  their mid lines take, with their σ rings held at the same proportion of it.
   *  Ours. The default keeps them as context, half-lit under the live leg; the
   *  higher settings are for reading the past legs themselves, which is the one
   *  thing the faint default makes hard on a crowded pane. */
  pastAlpha: number;
  /** Draw the segment that *would* appear if the structure flipped on the next
   *  bar — see `shadow` on the output. Ours, not his; off by default. */
  shadow: boolean;
  flags: DsvFlagMode;
}

// There is deliberately no "draw it in one flat hue" knob, the way Modern VWAP
// has. Direction is what this line says besides where it sits, and a monochrome
// version of it is a slower anchored VWAP and nothing else; the only hue left
// for one would be the grey the Globex anchor already owns. The σ rings take the
// same direction hue for the same reason.

export const DSV_SWING_OPTIONS = [10, 20, 30, 50, 80, 120] as const;
/** The bucketings the swing hunt can be moved onto: the built-in *time* ones, and
 *  the pane's own. Tick bars are not offered — a grid has to be shared between
 *  panes to be worth anything, and a count of prints is not a clock. Custom
 *  bucketings are not offered either: this list is built outside React, and a
 *  hand-typed `7m` is a bar you drew once rather than a grid to hold panes to. */
export const DSV_ANCHOR_OPTIONS = [
  { value: "", label: "the pane's own bars" },
  ...TIMEFRAMES.filter((t) => t.kind === "time").map((t) => ({ value: t.id, label: t.label })),
] as const;
/** Spanning the clamp below; anything outside it would be silently pinned. */
export const DSV_APT_OPTIONS = [5, 10, 20, 35, 50, 100, 200, 300] as const;
export const DSV_BIAS_OPTIONS = [0.5, 1, 2, 5, 10, 20] as const;
export const DSV_BAND_OPTIONS = [0, 1, 2, 3] as const;
export const DSV_WEIGHTING_OPTIONS = [
  { value: "decay", label: "decayed (his)" },
  { value: "cumulative", label: "plain anchored VWAP" },
] as const;
export const DSV_BAND_SCOPE_OPTIONS = [
  { value: "live", label: "live leg only" },
  { value: "all", label: "every segment" },
] as const;
/** The alpha the frozen mid lines take. 0.5 is what they have always been drawn
 *  at; the rest step up to fully lit, where a past leg reads exactly as solidly
 *  as the live one and only its weight-free σ rings stay behind it. */
export const DSV_PAST_ALPHA_OPTIONS = [0.35, 0.5, 0.7, 1] as const;
export const DSV_FLAG_OPTIONS = [
  { value: "labels", label: "flags + HH/HL/LH/LL" },
  { value: "marks", label: "flags only" },
  { value: "off", label: "off" },
] as const;

/** His defaults, exactly: `prd` 50, `baseAPT` 20, `useAdapt` false, `volBias` 10. */
export const DEFAULT_DYNAMIC_SWING_VWAP: DsvParams = {
  swingPeriod: 50,
  // The pane's own bars: his indicator has no such knob, and one pane has nothing
  // to agree with.
  anchorTf: "",
  // His: the decay *is* the indicator, so an untouched chart draws it.
  weighting: "decay",
  apt: 20,
  adaptApt: false,
  volBias: 10,
  // Off: his indicator has no envelope, so an untouched chart draws his.
  bands: 0,
  bandScope: "live",
  // What the frozen segments have always been drawn at, so an untouched chart
  // looks the same as it did before the knob existed.
  pastAlpha: 0.5,
  shadow: false,
  flags: "labels",
};

/** `atrLen` in the source, used for both the ATR and the average it is measured
 *  against — `ta.rma(ta.atr(50), 50)`, so a smoothed value over its own smoothed
 *  mean. Not an input there, so not a knob here. */
const ATR_LEN = 50;
/** `math.max(5.0, math.min(300.0, aptRaw))`, and then `math.round`. The rounding
 *  is his and is kept: it makes the tracking speed a step function of
 *  volatility, which is visible on the line as occasional small kinks. */
const APT_MIN = 5;
const APT_MAX = 300;
/** `max_polylines_count = 100` in his `indicator()` call. Past segments beyond
 *  this are evicted oldest-first — on TradingView by the runtime, here by us, so
 *  that a long chart shows the same thing his does rather than more. */
const MAX_SEGMENTS = 100;

// --- output -----------------------------------------------------------------

/** Which side of the structure a segment is anchored on: 1 bullish (anchored at
 *  a swing low), -1 bearish (at a swing high). There is no neutral state — his
 *  `dir` is `phL > plL ? 1 : -1`, which on the very first bar resolves bearish. */
export type DsvDir = 1 | -1;

export interface DsvPoint {
  time: number;
  /** The decayed anchored VWAP. NaN until volume has traded under the anchor. */
  value: number;
  /** Volume-weighted σ of hlc3 about `value`, under the same decay — one number
   *  rather than six, so the caller multiplies it out to whichever rings it
   *  draws. 0 at an anchor (one observation has no spread) and NaN wherever
   *  `value` is. */
  sd: number;
}

/** One anchored VWAP: the live one, or one of the frozen ones behind it. Drawn
 *  as its own polyline, because during the overlap several of them cover the
 *  same bars and a line series holds one value per bar. */
export interface DsvSegment {
  dir: DsvDir;
  points: DsvPoint[];
}

/** HH/HL against the previous swing *low*, LH/LL against the previous *high* —
 *  each pivot compared with the last one of its own side, which is what makes
 *  the four labels a structure read rather than four names for two events. */
export type DsvPivotKind = "HH" | "HL" | "LH" | "LL";

export interface DsvPivot {
  /** The pivot bar — where the segment is anchored, not where the flip was seen. */
  time: number;
  price: number;
  /** The regime the flip opened: 1 means this is a swing low, -1 a swing high. */
  dir: DsvDir;
  /** Null where his `txt` comes out '' — the first pivot of a side, or an exact
   *  tie with the previous one. */
  kind: DsvPivotKind | null;
  /** The bar the flip was detected on. Always ≥ `time`; the gap between them is
   *  the stretch the line repaints, and the flags draw it as a stem. */
  seenAt: number;
}

export interface DynamicSwingVwapData {
  /** The segment currently accumulating. Null only on an empty tape. */
  live: DsvSegment | null;
  /** Frozen segments, oldest first, already capped at `MAX_SEGMENTS`. */
  past: DsvSegment[];
  /** The segment that would replace `live` if the structure flipped on the next
   *  bar — accumulated from the pending anchor exactly as a real flip would, so
   *  it is the line you would actually get and not a sketch of one. Null when
   *  `shadow` is off. See the note at the top on why this costs nothing. */
  shadow: DsvSegment | null;
  /** Where that shadow is anchored, and what the flip would be labelled. Null
   *  alongside `shadow`. */
  pending: DsvPivot | null;
  pivots: DsvPivot[];
  /** How many older segments the cap dropped — his chart drops them silently,
   *  the legend row says so. */
  dropped: number;
  /** Effective half-life at the last bar, in bars (an integer: he rounds). */
  aptNow: number;
  /** Whether `anchorTf` actually moved the structure off the drawn bars. False
   *  with the knob unset, and false when the grid it names is at or below the
   *  pane's own bar — where it merges nothing and the structure is the pane's. */
  anchored: boolean;
  /** Share of drawn (non-history) bars the structure reads bullish on. */
  bullPct: number;
}

const EMPTY: DynamicSwingVwapData = {
  live: null,
  past: [],
  shadow: null,
  pending: null,
  pivots: [],
  dropped: 0,
  aptNow: 0,
  anchored: false,
  bullPct: 0,
};

// --- pieces -----------------------------------------------------------------

/**
 * Pine's `ta.rma`: Wilder smoothing, seeded with the SMA of the first `n` finite
 * values and NaN before that.
 *
 * The seeding is worth matching here rather than shrugged off the way
 * lib/modernVwap can afford to. This is applied twice — `ta.rma(ta.atr(50), 50)`
 * — so the warm-up is a couple of hundred bars deep, and a first-value seed
 * takes visibly longer to agree.
 */
function rmaSeries(src: Float64Array, n: number): Float64Array {
  const out = new Float64Array(src.length).fill(NaN);
  let sum = 0;
  let seen = 0;
  let rma = NaN;
  for (let i = 0; i < src.length; i++) {
    if (!Number.isFinite(src[i])) continue;
    if (seen < n) {
      sum += src[i];
      seen++;
      if (seen === n) out[i] = rma = sum / n;
      continue;
    }
    out[i] = rma = (rma * (n - 1) + src[i]) / n;
  }
  return out;
}

/** True range, with the first bar's high−low as Pine has it. */
function trSeries(bars: DsvBar[]): Float64Array {
  const out = new Float64Array(bars.length);
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    out[i] =
      i === 0
        ? b.high - b.low
        : Math.max(
            b.high - b.low,
            Math.abs(b.high - bars[i - 1].close),
            Math.abs(b.low - bars[i - 1].close),
          );
  }
  return out;
}

/**
 * The two latches, bar by bar: the index of the last bar that was the highest of
 * its trailing `win`, and the last that was the lowest.
 *
 * Over highs and lows rather than over bars, because with `anchorTf` set the bars
 * it runs on are the anchor groups and not the drawn ones — and a group is a high
 * and a low, nothing else the latch reads.
 *
 * This is `ta.highestbars(high, prd) == 0 ? b : phL` — a persistent variable that
 * only moves when the current bar *is* the extreme. Note what that means: once
 * latched, `phL` can sit far outside the `win`-bar window, because nothing has
 * beaten it since. That is the difference between this and a rolling argmax, and
 * it is the whole character of the indicator — a rolling argmax re-flips every
 * time an old extreme ages out and finds several times as many anchors.
 *
 * Monotonic deques for the "is the current bar the extreme of its window" test,
 * so this is one pass rather than a window scan per bar. `<=` pops earlier
 * equals, so a tie resolves to the current bar, matching `highestbars`'s
 * most-recent tie-break.
 */
function latches(
  high: Float64Array,
  low: Float64Array,
  win: number,
): { phL: Int32Array; plL: Int32Array } {
  const n = high.length;
  const phL = new Int32Array(n);
  const plL = new Int32Array(n);
  const dqH: number[] = [];
  const dqL: number[] = [];
  let curH = 0;
  let curL = 0;
  for (let i = 0; i < n; i++) {
    while (dqH.length && high[dqH[dqH.length - 1]] <= high[i]) dqH.pop();
    dqH.push(i);
    while (dqL.length && low[dqL[dqL.length - 1]] >= low[i]) dqL.pop();
    dqL.push(i);
    const floor = i - win + 1;
    while (dqH[0] < floor) dqH.shift();
    while (dqL[0] < floor) dqL.shift();
    if (dqH[0] === i) curH = i;
    if (dqL[0] === i) curL = i;
    phL[i] = curH;
    plL[i] = curL;
  }
  return { phL, plL };
}

/** The drawn bars regrouped onto a coarser wall-clock grid: what the latches see
 *  when `anchorTf` is set. */
interface AnchorGrid {
  /** Which group each drawn bar sits in. */
  of: Int32Array;
  /** Each group's extremes, and the *drawn* bar that made each — which is where
   *  a pivot on that group is anchored, so the anchor lands on a bar the pane
   *  actually has and at the price every pane agrees the swing was. */
  high: Float64Array;
  low: Float64Array;
  highAt: Int32Array;
  lowAt: Int32Array;
}

/**
 * Group the drawn bars onto the grid `anchorTf` names, laid from the first
 * session bar.
 *
 * That origin is the point: the replay measures its own time buckets off the RTH
 * bell (lib/replayEngine.timeBucket), so the bell is a boundary on every pane and
 * `bars[histCount]` *is* the bell there — the same instant whatever the pane is
 * drawn on. A grid from that point therefore falls on boundaries every pane's
 * bars already respect, backwards through the context days as well as forwards,
 * and two panes group the same tape the same way. On a chart that stamps its bars
 * some other way (the journal's are stamped at each bucket's last tick) the grid
 * is offset by less than one bar, which shifts nothing that is read against
 * another pane, because those charts draw one.
 *
 * Null when there is no grid to lay — the knob unset, or naming a tick bucketing,
 * which is a count of prints and not a clock two panes can share — and null when
 * the grid merges nothing, every group one bar, i.e. it is at or below the pane's
 * own bucketing. The caller then reads the structure off the drawn bars, which is
 * exactly what that grid asked for.
 */
function anchorGrid(bars: DsvBar[], histCount: number, anchorTf: string): AnchorGrid | null {
  const tf = anchorTf ? parseTimeframeId(anchorTf) : null;
  if (!tf || tf.kind !== "time") return null;
  const stepSec = tf.ms / 1000;
  const n = bars.length;
  const origin = bars[Math.min(Math.max(histCount, 0), n - 1)].time;
  const of = new Int32Array(n);
  const high = new Float64Array(n);
  const low = new Float64Array(n);
  const highAt = new Int32Array(n);
  const lowAt = new Int32Array(n);
  let count = 0;
  let cur = 0;
  for (let i = 0; i < n; i++) {
    const b = Math.floor((bars[i].time - origin) / stepSec);
    if (count === 0 || b !== cur) {
      cur = b;
      const k = count++;
      high[k] = bars[i].high;
      low[k] = bars[i].low;
      highAt[k] = i;
      lowAt[k] = i;
    } else {
      const k = count - 1;
      // Strict, so a tie keeps the earlier bar — the swing was made when it was
      // first reached, and the pane drawn at this bucketing has no later bar to
      // offer either.
      if (bars[i].high > high[k]) {
        high[k] = bars[i].high;
        highAt[k] = i;
      }
      if (bars[i].low < low[k]) {
        low[k] = bars[i].low;
        lowAt[k] = i;
      }
    }
    of[i] = count - 1;
  }
  if (count >= n) return null;
  return {
    of,
    high: high.subarray(0, count),
    low: low.subarray(0, count),
    highAt: highAt.subarray(0, count),
    lowAt: lowAt.subarray(0, count),
  };
}

// --- the whole thing --------------------------------------------------------

/**
 * Run the indicator over the drawn bars.
 *
 * `histCount` is where the context days end and the session begins: everything is
 * computed across the whole array so the ATR baseline and the latches are warm at
 * the first session bar, and only `bullPct` is reported over the session alone.
 */
export function computeDynamicSwingVwap(
  bars: DsvBar[],
  histCount: number,
  p: DsvParams,
): DynamicSwingVwapData {
  const n = bars.length;
  if (n === 0) return EMPTY;

  const tp = new Float64Array(n);
  const vol = new Float64Array(n);
  // Kept as arrays because the latches take them that way — with `anchorTf` set
  // it is the groups' extremes that go in instead.
  const hi = new Float64Array(n);
  const lo = new Float64Array(n);
  // What a bar contributes under `cumulative`: its tick moments where the chart
  // has them, hlc3 where it does not (lib/vwap.barMoments). The decayed path
  // keeps using `tp`/`vol` directly — it is a port of a Pine indicator that only
  // ever saw bars, and hlc3 is what it averages.
  const mpv = new Float64Array(n);
  const mp2v = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    tp[i] = (b.high + b.low + b.close) / 3;
    hi[i] = b.high;
    lo[i] = b.low;
    // A bar with no volume contributes nothing but must not zero the average;
    // both accumulators simply decay on it.
    vol[i] = b.volume > 0 ? b.volume : 0;
    const m = barMoments(b);
    mpv[i] = m.pv;
    mp2v[i] = m.p2v;
  }

  // --- the tracking speed, per bar. `ratio = atr / rma(atr)`, and where that
  // isn't warm yet his `atrAvg > 0` guard is na-false and the ratio falls back
  // to 1 — so the half-life is the nominal one until both smoothers have filled.
  const atr = rmaSeries(trSeries(bars), ATR_LEN);
  const atrAvg = rmaSeries(atr, ATR_LEN);
  const halfLife = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const ratio = Number.isFinite(atrAvg[i]) && atrAvg[i] > 0 ? atr[i] / atrAvg[i] : 1;
    const raw = p.adaptApt ? p.apt / Math.pow(ratio, p.volBias) : p.apt;
    halfLife[i] = Math.round(Math.min(APT_MAX, Math.max(APT_MIN, raw)));
  }
  /** `alphaFromAPT`: `1 − exp(−ln2 / apt)`, i.e. a half-life of `apt` bars. */
  const alpha = (i: number) => 1 - Math.exp(-Math.LN2 / Math.max(1, halfLife[i]));

  // --- the structure, and the flips it turns into anchors.
  //
  // Read on the anchor grid when there is one, and on the drawn bars when there
  // is not — the same two latches either way, over whichever highs and lows. What
  // comes back out is per *drawn* bar regardless: the direction a bar is under,
  // and the drawn bars its two latched extremes were made at. With no grid the
  // groups are the bars and every line below is what it always was.
  const win = Math.max(2, p.swingPeriod);
  const grid = anchorGrid(bars, histCount, p.anchorTf);
  const { phL, plL } = latches(grid ? grid.high : hi, grid ? grid.low : lo, win);
  const dir = new Int8Array(n);
  /** Where the latched swing high and low sit, as drawn-bar indices. */
  const phAt = new Int32Array(n);
  const plAt = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    const k = grid ? grid.of[i] : i;
    dir[i] = phL[k] > plL[k] ? 1 : -1;
    phAt[i] = grid ? grid.highAt[phL[k]] : phL[k];
    plAt[i] = grid ? grid.lowAt[plL[k]] : plL[k];
  }

  // --- the accumulator.
  //
  // `p := (1 − α)·p + α·pxv` and the same for volume — his form, not the
  // equivalent-ratio shorthand. The α on the incoming term matters here in a way
  // it would not with a zero seed: the anchor bar is seeded with the pivot's wick
  // *before* the loop folds that bar in at hlc3, so the seed's weight against the
  // first observation is exactly what puts the line's first point on the wick.
  //
  // Under `cumulative` the two weights are both 1, which is the same expression
  // with nothing forgotten: `pv += tp·vol`. That, plus a zeroed seed below, is
  // the whole of the plain-aVWAP mode.
  interface Acc {
    pv: number;
    v: number;
    /** The second moment, decayed alongside the first. Ours, not his — see the
     *  note at the top on what the envelope is and is not. */
    pv2: number;
  }
  const decayed = p.weighting === "decay";
  const step = (a: Acc, j: number): DsvPoint => {
    if (!decayed) {
      // A plain anchored VWAP, and in this app that means tick moments — the
      // whole point of the mode is to equal the ⚓ tool anchored on the same
      // candle, and the ⚓ tool accumulates ticks.
      a.pv += mpv[j];
      a.v += vol[j];
      a.pv2 += mp2v[j];
      if (!(a.v > 0)) return { time: bars[j].time, value: NaN, sd: NaN };
      const m = a.pv / a.v;
      return { time: bars[j].time, value: m, sd: Math.sqrt(Math.max(a.pv2 / a.v - m * m, 0)) };
    }
    const al = alpha(j);
    const keep = 1 - al;
    a.pv = keep * a.pv + al * tp[j] * vol[j];
    a.v = keep * a.v + al * vol[j];
    a.pv2 = keep * a.pv2 + al * tp[j] * tp[j] * vol[j];
    if (!(a.v > 0)) return { time: bars[j].time, value: NaN, sd: NaN };
    const mean = a.pv / a.v;
    // Clamped at zero: the two moments are accumulated independently, so
    // rounding can put E[x²] a hair under E[x]² on a segment that has barely
    // moved, and a negative variance would come back NaN from the sqrt.
    return { time: bars[j].time, value: mean, sd: Math.sqrt(Math.max(a.pv2 / a.v - mean * mean, 0)) };
  };

  const pivots: DsvPivot[] = [];
  const past: DsvSegment[] = [];
  let dropped = 0;
  const retire = (seg: DsvSegment) => {
    if (seg.points.length < 1) return;
    past.push(seg);
    if (past.length > MAX_SEGMENTS) {
      past.shift();
      dropped++;
    }
  };

  // `var p = hlc3 * volume` / `var vol = volume` — the first segment is seeded on
  // bar 0 from bar 0, and then bar 0 is stepped as well (his else branch runs on
  // it), which leaves the ratio at hlc3[0]. A sum that never forgets would fold
  // bar 0 in twice instead, so the cumulative seed is empty.
  const seed = (price: number, at: number): Acc =>
    decayed
      ? { pv: price * vol[at], v: vol[at], pv2: price * price * vol[at] }
      : { pv: 0, v: 0, pv2: 0 };
  let acc: Acc = seed(tp[0], 0);
  let live: DsvSegment = { dir: dir[0] as DsvDir, points: [] };
  /** `prev`, his rolling comparand for the HH/HL/LH/LL text. */
  let prev = NaN;

  for (let i = 0; i < n; i++) {
    if (i > 0 && dir[i] !== dir[i - 1]) {
      const d = dir[i] as DsvDir;
      const at = d === 1 ? plAt[i] : phAt[i];
      const price = d === 1 ? bars[at].low : bars[at].high;

      // The label, against the last pivot of the same side. His `prev` holds the
      // *opposite* latch as of the previous bar, which lands on exactly that:
      // by the time a bullish flip reads it, it is the swing low from before the
      // one being anchored at now.
      const kind: DsvPivotKind | null =
        d === 1
          ? price < prev
            ? "LL"
            : price > prev
              ? "HL"
              : null
          : price < prev
            ? "LH"
            : price > prev
              ? "HH"
              : null;
      prev = d === 1 ? bars[phAt[i - 1]].high : bars[plAt[i - 1]].low;
      pivots.push({ time: bars[at].time, price, dir: d, kind, seenAt: bars[i].time });

      // Freeze the outgoing segment where it stands — it ends on the bar before
      // the flip, because that is the last point his else branch pushed.
      retire(live);

      // And open the new one at the pivot: seed with the wick, then replay from
      // the pivot bar through this one, that first bar folding in at hlc3.
      acc = seed(price, at);
      live = { dir: d, points: [] };
      for (let j = at; j < i; j++) live.points.push(step(acc, j));
    }
    live.points.push(step(acc, i));
  }

  // The structure read, over the session rather than the context days behind it.
  let bull = 0;
  const sess = Math.max(0, n - histCount);
  for (let i = histCount; i < n; i++) if (dir[i] === 1) bull++;

  // --- the shadow: the segment a flip on the next bar would produce.
  //
  // Free, because the two latches are already exactly the two anchors that
  // matter. `dir` is whichever latched more recently, and the live segment is
  // anchored at the *other* one — so the latch that set the direction is, by
  // construction, where the opposite leg would start. On a bullish leg that is
  // the recent swing high; the shadow therefore runs from a recent bar to now
  // and is short, while the live segment is the long one.
  //
  // Strictly causal: it uses the latches as they stand at the last bar and no
  // future information. It is a *hypothesis* rather than a forecast — it says
  // "if the structure flipped here, this is the line you would get", and the
  // anchor it is drawn from can still move forward before any flip arrives.
  let shadow: DsvSegment | null = null;
  let pending: DsvPivot | null = null;
  if (p.shadow) {
    const last = n - 1;
    const d = -dir[last] as DsvDir;
    const at = d === 1 ? plAt[last] : phAt[last];
    const price = d === 1 ? bars[at].low : bars[at].high;
    // The label it would carry, against the same `prev` the real flip would read
    // — which is where the loop above left it.
    const kind: DsvPivotKind | null =
      d === 1
        ? price < prev
          ? "LL"
          : price > prev
            ? "HL"
            : null
        : price < prev
          ? "LH"
          : price > prev
            ? "HH"
            : null;
    // `seenAt` is now: a pending flip has not been detected, and the span from
    // the anchor to here is exactly the stretch the line would repaint.
    pending = { time: bars[at].time, price, dir: d, kind, seenAt: bars[last].time };
    const sh: Acc = seed(price, at);
    const points: DsvPoint[] = [];
    for (let j = at; j <= last; j++) points.push(step(sh, j));
    shadow = { dir: d, points };
  }

  return {
    live,
    past,
    shadow,
    pending,
    pivots,
    dropped,
    // 0 under `cumulative`: there is no half-life, and reporting the knob's
    // number would say the decay is running when nothing is decaying.
    aptNow: decayed ? halfLife[n - 1] : 0,
    anchored: grid !== null,
    bullPct: sess ? (100 * bull) / sess : 0,
  };
}

// --- prefs ------------------------------------------------------------------

const pickNum = (v: unknown, opts: readonly number[], d: number): number =>
  typeof v === "number" && opts.includes(v) ? v : d;

/** Validate a stored blob against the offered shortlists — the sim.prefs rule: a
 *  hand-edited value that isn't on a list would leave its picker blank. */
export function dynamicSwingVwapParams(raw: unknown): DsvParams {
  const d = DEFAULT_DYNAMIC_SWING_VWAP;
  if (!raw || typeof raw !== "object") return { ...d };
  const s = raw as Partial<Record<keyof DsvParams, unknown>>;
  return {
    swingPeriod: pickNum(s.swingPeriod, DSV_SWING_OPTIONS, d.swingPeriod),
    anchorTf: DSV_ANCHOR_OPTIONS.some((o) => o.value === s.anchorTf)
      ? (s.anchorTf as string)
      : d.anchorTf,
    weighting: DSV_WEIGHTING_OPTIONS.some((o) => o.value === s.weighting)
      ? (s.weighting as DsvWeighting)
      : d.weighting,
    apt: pickNum(s.apt, DSV_APT_OPTIONS, d.apt),
    adaptApt: typeof s.adaptApt === "boolean" ? s.adaptApt : d.adaptApt,
    volBias: pickNum(s.volBias, DSV_BIAS_OPTIONS, d.volBias),
    bands: pickNum(s.bands, DSV_BAND_OPTIONS, d.bands) as 0 | 1 | 2 | 3,
    bandScope: DSV_BAND_SCOPE_OPTIONS.some((o) => o.value === s.bandScope)
      ? (s.bandScope as DsvBandScope)
      : d.bandScope,
    pastAlpha: pickNum(s.pastAlpha, DSV_PAST_ALPHA_OPTIONS, d.pastAlpha),
    shadow: typeof s.shadow === "boolean" ? s.shadow : d.shadow,
    flags: DSV_FLAG_OPTIONS.some((o) => o.value === s.flags) ? (s.flags as DsvFlagMode) : d.flags,
  };
}
