// The CVD Divergence Oscillator — a port of "Cumulative Volume Delta Divergence
// [TradingFinder] Periodic EMA" (TFlab, MPL-2.0), with one deliberate departure.
//
// What it is, and why it is not the CVD layer we already have. The existing
// `cvd` pane accumulates signed aggressor volume from the session anchor to the
// right edge: one monotone-ish line whose *level* is the read. That line drifts.
// Six hours in, a swing's delta sits on top of six hours of accumulation, and a
// divergence measured against it is measured against a number that is mostly
// history. This oscillator throws the anchor away and keeps a *window* — the sum
// of the last `period` bars' delta, or their EMA — so every value is a fact
// about recent flow and zero means "the window balanced", not "we are back where
// the session opened". That is the whole reason the indicator exists, and why it
// is a second pane rather than a setting on the first.
//
// The departure: the Pine estimates per-bar delta from bar geometry —
// `V·(close−low)/(high−low) − V·(high−close)/(high−low)` — because TradingView
// has no tape. We have the tape, tagged with an aggressor side, and already bin
// it per bar (api/session_chart._per_bar_delta; the replay steps the same
// quantity off its own tick buffer). So `deltas` here is *real* signed
// aggressor volume, and the geometry proxy is not implemented at all. Everything
// downstream — the window, the fractals, the trend gate, the pairing rules — is
// the script's. Plots will not match TradingView bar-for-bar; they are not meant
// to.
//
// Unvalidated. Nothing in the sim or the strategy engine reads this: it is a
// reading surface, like the community catalogue, and a divergence indicator is
// exactly the shape of thing that produces a convincing fake edge. Read
// docs/research before betting on it.

/** How the window accumulates. The script's `CumMode`. */
export type CvdOscMode = "periodic" | "ema";

export const CVD_OSC_MODE_OPTIONS: { value: CvdOscMode; label: string }[] = [
  { value: "periodic", label: "Periodic — rolling sum" },
  { value: "ema", label: "EMA — smoothed" },
];

/** The script's three inputs, and nothing else. `trendLen`, and the two 30-bar
 *  limits below, are hardcoded in the original; they stay constants here rather
 *  than becoming knobs nobody asked for. */
export interface CvdOscParams {
  mode: CvdOscMode;
  /** `CVD Period` — bars in the window. */
  period: number;
  /** `Divergence Fractal Periods` — bars either side of a pivot. A fractal is
   *  therefore confirmed `n` bars late, which is a property of the indicator and
   *  not a lag to be tuned away: the pivot is not a pivot until the bars to its
   *  right exist. */
  fractalN: number;
}

export const DEFAULT_CVD_OSC: CvdOscParams = { mode: "periodic", period: 21, fractalN: 2 };

export const CVD_OSC_PERIOD_OPTIONS = [9, 14, 21, 34, 55];
export const CVD_OSC_FRACTAL_OPTIONS = [1, 2, 3, 4, 5];

/** The trend gate's EMA length. A fractal high only counts when the pivot bar
 *  closed *above* it, a fractal low only when it closed below — the script's way
 *  of refusing to read a bearish divergence out of a downtrend's noise. */
const TREND_LEN = 50;

/** How far apart the two pivots may sit, and how stale the newer one may be
 *  before the pair stops being drawn. Both 30 bars in the original. The second is
 *  why a divergence appears at confirmation and never later. */
const MAX_PIVOT_GAP = 30;
const MAX_PIVOT_AGE = 30;

/** One confirmed divergence, carrying *both* coordinate systems: the oscillator
 *  values at the two pivots and the prices at those same bars. The script draws
 *  the segment twice — once on its own pane, once over the candles — and one
 *  object holding both is what lets a single primitive serve either pane without
 *  re-deriving where the other one was measured. */
export interface CvdOscDivergence {
  kind: "bear" | "bull";
  /** Bar time of the older pivot (A) and the newer one (B). */
  t1: number;
  t2: number;
  /** Oscillator value at A and B — the pane's own units. */
  h1: number;
  h2: number;
  /** Pivot price at A and B — the high for a bear, the low for a bull. */
  p1: number;
  p2: number;
  /** Consecutive confirmations on this side: 1 Normal, 2 Good, ≥3 Strong. The
   *  script computes this and then never prints it; it is the one genuinely
   *  useful thing in there that the drawing drops, so it rides on the segment
   *  and the legend reports it. */
  strength: number;
}

export interface CvdOscResult {
  /** The histogram, aligned to the bars it was given. `NaN` through the seeding
   *  window, where the indicator genuinely has no value yet. */
  hist: number[];
  divergences: CvdOscDivergence[];
}

/** Bars as this needs them — a structural subset of `Bar`, so both chart hosts
 *  can pass their own arrays straight in. */
export interface CvdOscBar {
  time: number;
  high: number;
  low: number;
  close: number;
}

/** `ta.ema` — seeded with the SMA of the first `len` values, as Pine seeds it,
 *  and `NaN` before that. Seeding matters here: an EMA started from the first
 *  value alone takes most of the window to shed it, and on delta (which swings
 *  through zero every few bars) that shows up as a sign error near the open. */
function ema(src: number[], len: number): number[] {
  const out = new Array<number>(src.length).fill(NaN);
  if (src.length < len || len < 1) return out;
  const k = 2 / (len + 1);
  let seed = 0;
  for (let i = 0; i < len; i++) seed += src[i];
  let prev = seed / len;
  out[len - 1] = prev;
  for (let i = len; i < src.length; i++) {
    prev = prev + k * (src[i] - prev);
    out[i] = prev;
  }
  return out;
}

/** `math.sum(src, len)` — the trailing window, `NaN` until it is full. */
function rollingSum(src: number[], len: number): number[] {
  const out = new Array<number>(src.length).fill(NaN);
  let run = 0;
  for (let i = 0; i < src.length; i++) {
    run += src[i];
    if (i >= len) run -= src[i - len];
    if (i >= len - 1) out[i] = run;
  }
  return out;
}

/** The windowed delta itself, without the divergence machinery — the histogram
 *  and nothing else. Exported because the replay recomputes this every frame as
 *  its forming bar restates, and the pivots behind it cannot move until that bar
 *  closes. */
export function cvdOscHist(deltas: number[], p: CvdOscParams): number[] {
  const len = Math.max(1, Math.round(p.period));
  return p.mode === "ema" ? ema(deltas, len) : rollingSum(deltas, len);
}

/**
 * The indicator, over one session's bars and their per-bar delta.
 *
 * `deltas` is signed aggressor volume per bar, aligned 1:1 with `bars` — the
 * backend's `delta` rows on the journal charts, the replay's own tape scan on
 * the workspace. A short or missing array is treated as zeros rather than
 * refused: a tape with no aggressor tag draws a flat histogram and no
 * divergences, which is the honest picture, and the host drops the pane anyway.
 */
export function computeCvdOsc(
  bars: CvdOscBar[],
  deltas: number[],
  params: CvdOscParams = DEFAULT_CVD_OSC,
): CvdOscResult {
  const n = bars.length;
  const p = {
    mode: params.mode,
    period: Math.max(1, Math.round(params.period)),
    fractalN: Math.max(1, Math.round(params.fractalN)),
  };
  const d = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    const v = deltas[i];
    d[i] = Number.isFinite(v) ? v : 0;
  }

  const hist = cvdOscHist(d, p);
  if (n === 0) return { hist, divergences: [] };

  const closes = new Array<number>(n);
  for (let i = 0; i < n; i++) closes[i] = bars[i].close;
  const trend = ema(closes, TREND_LEN);

  const k = p.fractalN;
  const divergences: CvdOscDivergence[] = [];

  // The two pivots of each kind the script's `valuewhen(…, 0)` and `(…, 1)`
  // resolve to: the newest confirmed fractal and the one before it.
  type Pivot = { i: number; price: number; h: number };
  let lastH: Pivot | null = null;
  let prevH: Pivot | null = null;
  let lastL: Pivot | null = null;
  let prevL: Pivot | null = null;
  // The consecutive-confirmation counters. They increment only when the newest
  // pivot *changed* on this bar and the pair still reads as a divergence, and
  // reset the moment it doesn't — so a run is broken by the pair ageing out, not
  // merely by bars passing.
  let bearRun = 0;
  let bullRun = 0;

  for (let i = 0; i < n; i++) {
    // A fractal at bar `c` is confirmed at bar `c + k`, once the bars to its
    // right exist. Strict on both sides, as `ta.pivothigh`/`ta.pivotlow` are: a
    // flat top is not a pivot.
    const c = i - k;
    let newHigh = false;
    let newLow = false;
    if (c >= k) {
      let isHigh = true;
      let isLow = true;
      for (let j = c - k; j <= c + k; j++) {
        if (j === c) continue;
        if (bars[j].high >= bars[c].high) isHigh = false;
        if (bars[j].low <= bars[c].low) isLow = false;
      }
      // The trend gate, exactly as written: the *pivot* bar's close against the
      // EMA as it stands at the *confirmation* bar. Mixing the two is the
      // original's choice, not a transcription slip — a pivot is judged against
      // the trend that has since developed around it.
      const t = trend[i];
      if (isHigh && Number.isFinite(t) && bars[c].close > t) {
        prevH = lastH;
        lastH = { i: c, price: bars[c].high, h: hist[c] };
        newHigh = prevH !== null && lastH.price !== prevH.price;
      }
      if (isLow && Number.isFinite(t) && bars[c].close < t) {
        prevL = lastL;
        lastL = { i: c, price: bars[c].low, h: hist[c] };
        newLow = prevL !== null && lastL.price !== prevL.price;
      }
    }

    // Bearish: price made a higher high, the window's delta made a lower one —
    // the push was not backed by aggressive buying. Both readings must sit above
    // zero, or "lower high" is being read off a window that was net selling
    // through both pivots and the comparison means nothing.
    const bear =
      lastH !== null &&
      prevH !== null &&
      Number.isFinite(lastH.h) &&
      Number.isFinite(prevH.h) &&
      lastH.h > 0 &&
      prevH.h > 0 &&
      lastH.i - prevH.i < MAX_PIVOT_GAP &&
      lastH.i + MAX_PIVOT_AGE > i &&
      lastH.price > prevH.price &&
      lastH.h < prevH.h;

    const bull =
      lastL !== null &&
      prevL !== null &&
      Number.isFinite(lastL.h) &&
      Number.isFinite(prevL.h) &&
      lastL.h < 0 &&
      prevL.h < 0 &&
      lastL.i - prevL.i < MAX_PIVOT_GAP &&
      lastL.i + MAX_PIVOT_AGE > i &&
      lastL.price < prevL.price &&
      lastL.h > prevL.h;

    if (bear && newHigh) bearRun++;
    else if (!bear) bearRun = 0;
    if (bull && newLow) bullRun++;
    else if (!bull) bullRun = 0;

    // One segment per pair. The original re-draws the same line on every bar the
    // condition holds — a stack of identical overlapping lines that reads as one
    // and costs a `line.new` per bar — so it is emitted here at the bar the pair
    // becomes true, which is where it first appears on their chart too.
    if (bear && newHigh && lastH && prevH) {
      divergences.push({
        kind: "bear",
        t1: bars[prevH.i].time,
        t2: bars[lastH.i].time,
        h1: prevH.h,
        h2: lastH.h,
        p1: prevH.price,
        p2: lastH.price,
        strength: bearRun,
      });
    }
    if (bull && newLow && lastL && prevL) {
      divergences.push({
        kind: "bull",
        t1: bars[prevL.i].time,
        t2: bars[lastL.i].time,
        h1: prevL.h,
        h2: lastL.h,
        p1: prevL.price,
        p2: lastL.price,
        strength: bullRun,
      });
    }
  }

  return { hist, divergences };
}

/** `1 → Normal`, `2 → Good`, `≥3 → Strong` — the script's own scoring, for the
 *  legend row. */
export function cvdOscStrengthLabel(strength: number): string {
  if (strength >= 3) return "Strong";
  if (strength === 2) return "Good";
  return "Normal";
}
