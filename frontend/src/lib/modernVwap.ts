// Modern VWAP [GBB], ported from the demo to the chart.
//
// The Python side of this is demo/modern_vwap_demo.py, which is itself a port
// of the published Pine (data/research/modern-vwap/modern_vwap_gbb.pine). The
// study page it writes — docs/research/modern-vwap.html — is the thing to read
// before turning any of this on; the short version is that the *line* is an
// ordinary anchored VWAP and the only construct on it we have neither built nor
// falsified is the swing anchor, which re-anchors at every confirmed pivot
// instead of at a clock time.
//
// Two things worth knowing before reading a signal off it:
//
//   * **MR is our touch-bar close-back shape verbatim** — the one the
//     weekly-band context study resolved as an artifact of scoring the touch
//     bar's own close. It is drawn here because you cannot see what a gate is
//     doing by looking only at what survived it, not because it is an edge.
//   * **The ATR axis of the "two-axis regime" gates nothing.** The quadrant is
//     `2*(KER>median) + (ATR%>median)`, and the gate asks `quad >= 2`, which is
//     exactly `KER > median`. The ATR half only changes which of two pills a
//     bar gets. It is computed and configurable for fidelity with the Pine; it
//     is not load-bearing.
//
// Everything here is computed from the *drawn bars*, at whatever resolution the
// chart is showing — every window in the indicator counts bars, not minutes, so
// KER(20) is 20 minutes on a 1m chart and 100 on a 5m one. That is a property
// of the indicator, not of this port; the demo page makes the same point by
// rebuilding rather than resampling when its timeframe switch moves.

import type { Bar } from "./replayEngine";

// --- parameters -------------------------------------------------------------

export type MvAnchor = "swing" | "globex" | "rth" | "week";
export type MvSignalMode = "gated" | "all" | "none";

export interface ModernVwapParams {
  /** Where the accumulator resets. `swing` is his construct; the other three are
   *  clock anchors, and on those this indicator *is* the VWAP the chart already
   *  draws from ticks — see the note on `computeModernVwap`. */
  anchor: MvAnchor;
  /** Swing pivot length: the centre bar must beat all `pivot` neighbours on each
   *  side, so a pivot confirms `pivot` bars after it happened. */
  pivot: number;
  /** How many σ envelopes are drawn. The MR signal always tests ±2σ whatever
   *  this says — it is a drawing knob, not a rule knob. */
  bands: 1 | 2 | 3;
  /** Widen the bands in chop by `1 + kerWeight*(1-KER)`. Off in his own script. */
  adaptive: boolean;
  kerWeight: number;
  kerLen: number;
  atrLen: number;
  regimeLen: number;
  /** Colour the bands by the quadrant each bar landed in (his palette: purple
   *  trending, yellow ranging, grey undefined) rather than one flat hue. */
  regimeColor: boolean;
  signals: MvSignalMode;
  /** Trend-continuation occupancy: `occMin` of the last `occWindow` closes must
   *  be on one side of the line before a touch of it arms anything. */
  occWindow: number;
  occMin: number;
  /** How many bars a touch stays live waiting for the close back on side. */
  holdBars: number;
  /** Tick the bars where the anchor reset — the only way to see a swing anchor
   *  working without inferring it from the line's steps. */
  anchorMarks: boolean;
}

export const MV_ANCHOR_OPTIONS = [
  { value: "swing", label: "swing pivots" },
  { value: "globex", label: "globex 18:00" },
  { value: "rth", label: "NY 09:30" },
  { value: "week", label: "weekly" },
] as const;
export const MV_PIVOT_OPTIONS = [3, 5, 10, 20, 40] as const;
export const MV_BAND_OPTIONS = [1, 2, 3] as const;
export const MV_KER_WEIGHT_OPTIONS = [0.25, 0.5, 0.75, 1] as const;
export const MV_KER_LEN_OPTIONS = [10, 14, 20, 30, 50] as const;
export const MV_ATR_LEN_OPTIONS = [7, 14, 20, 30] as const;
export const MV_REGIME_LEN_OPTIONS = [50, 100, 200, 400] as const;
export const MV_OCC_WINDOW_OPTIONS = [5, 10, 15, 20] as const;
export const MV_HOLD_OPTIONS = [1, 2, 3, 5, 8] as const;
export const MV_SIGNAL_OPTIONS = [
  { value: "gated", label: "gate applied" },
  { value: "all", label: "all, blocked dimmed" },
  { value: "none", label: "none" },
] as const;

/** The occupancy floor offered for a given window. Absolute bar counts, but
 *  derived from the window so the pair can never be set to something the rule
 *  cannot satisfy (`occMin > occWindow` fires nothing, forever, silently). */
export function mvOccMinOptions(occWindow: number): number[] {
  const out = [0.6, 0.7, 0.8, 0.9, 1].map((f) => Math.max(1, Math.round(occWindow * f)));
  return [...new Set(out)].sort((a, b) => a - b);
}

/** His constants, unchanged — they are constants in the Pine too, not inputs. */
export const DEFAULT_MODERN_VWAP: ModernVwapParams = {
  anchor: "swing",
  pivot: 10,
  bands: 2,
  adaptive: false,
  kerWeight: 0.5,
  kerLen: 20,
  atrLen: 14,
  regimeLen: 200,
  regimeColor: true,
  signals: "gated",
  occWindow: 10,
  occMin: 8,
  holdBars: 3,
  anchorMarks: false,
};

// --- output -----------------------------------------------------------------

/** One drawn bar's worth. `regime` is the quadrant: -1 undefined, 0/1 the
 *  low-KER "ranging" half, 2/3 the high-KER "trending" half. */
export interface MvPoint {
  time: number;
  mid: number;
  u1: number;
  l1: number;
  u2: number;
  l2: number;
  u3: number;
  l3: number;
  regime: number;
  ker: number;
}

export interface MvSignal {
  time: number;
  kind: "MR" | "TC";
  side: "long" | "short";
  /** Whether the regime it fired in was the one it belongs to — MR wants
   *  ranging, TC wants trending. Recorded rather than applied, so the "all"
   *  display mode can draw the blocked ones dimmed. */
  gated: boolean;
  regime: number;
  price: number;
}

export interface ModernVwapData {
  points: MvPoint[];
  signals: MvSignal[];
  /** Bar times where the accumulator reset. */
  anchors: number[];
  /** Share of drawn (non-history) bars the gate calls trending, and the share it
   *  cannot call at all. The legend row quotes both — a gate that is undefined
   *  half the session is a fact about warm-up, not about the market. */
  trendPct: number;
  undefPct: number;
}

const EMPTY: ModernVwapData = {
  points: [],
  signals: [],
  anchors: [],
  trendPct: 0,
  undefPct: 0,
};

// --- pieces -----------------------------------------------------------------

/** Kaufman efficiency ratio: net travel / gross travel over `n` bars. 1 is a
 *  straight line, 0 is pure chop. NaN until it is warm. */
function kerSeries(close: Float64Array, n: number): Float64Array {
  const out = new Float64Array(close.length).fill(NaN);
  let path = 0;
  for (let i = 1; i < close.length; i++) {
    path += Math.abs(close[i] - close[i - 1]);
    if (i > n) path -= Math.abs(close[i - n] - close[i - n - 1]);
    if (i < n) continue;
    const net = Math.abs(close[i] - close[i - n]);
    out[i] = path === 0 ? 0 : net / path;
  }
  return out;
}

/** Wilder's ATR, first-TR seeded. The seeding differs from Pine's `ta.rma` (SMA
 *  seed there) by an amount that has decayed to nothing long before anything is
 *  drawn. */
function atrSeries(bars: Bar[], n: number): Float64Array {
  const out = new Float64Array(bars.length).fill(NaN);
  let atr = NaN;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const tr =
      i === 0
        ? b.high - b.low
        : Math.max(
            b.high - b.low,
            Math.abs(b.high - bars[i - 1].close),
            Math.abs(b.low - bars[i - 1].close),
          );
    atr = i === 0 ? tr : (atr * (n - 1) + tr) / n;
    out[i] = atr;
  }
  return out;
}

/**
 * Trailing median over `win`, matching pandas' `rolling(win).median()`: the mean
 * of the two middle order statistics on an even window, and NaN unless the whole
 * window is present and finite.
 *
 * A sorted window with binary insert/remove rather than a sort per bar — this
 * runs over every bar on the chart including the context days, and the naive
 * version is a sort of 200 elements a few thousand times for a number that has
 * to be ready before the next candle.
 */
function rollingMedian(src: Float64Array, win: number): Float64Array {
  const out = new Float64Array(src.length).fill(NaN);
  if (win < 1) return out;
  const sorted: number[] = [];
  let nans = 0;

  const at = (v: number) => {
    let lo = 0;
    let hi = sorted.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (sorted[m] < v) lo = m + 1;
      else hi = m;
    }
    return lo;
  };

  for (let i = 0; i < src.length; i++) {
    const add = src[i];
    if (Number.isFinite(add)) sorted.splice(at(add), 0, add);
    else nans++;
    if (i >= win) {
      const drop = src[i - win];
      if (Number.isFinite(drop)) sorted.splice(at(drop), 1);
      else nans--;
    }
    if (i < win - 1 || nans > 0) continue;
    const h = sorted.length >> 1;
    out[i] = sorted.length % 2 ? sorted[h] : (sorted[h - 1] + sorted[h]) / 2;
  }
  return out;
}

/**
 * Bars at which a swing pivot `pl` bars back becomes confirmed.
 *
 * Strict on both sides — the centre must beat all 2*pl neighbours outright,
 * which is what the Pine spells out by hand rather than using `ta.pivothigh`
 * (that one is not strict on the left). A simultaneous high and low is one
 * event, as there.
 */
function swingEvents(bars: Bar[], pl: number): Uint8Array {
  const n = bars.length;
  const ev = new Uint8Array(n);
  const w = 2 * pl + 1;
  if (n < w || pl < 1) return ev;
  for (let c = pl; c + pl < n; c++) {
    let isHigh = true;
    let isLow = true;
    for (let j = c - pl; j <= c + pl && (isHigh || isLow); j++) {
      if (j === c) continue;
      if (bars[j].high >= bars[c].high) isHigh = false;
      if (bars[j].low <= bars[c].low) isLow = false;
    }
    // The window covers c-pl..c+pl, so its centre confirms at c+pl.
    if (isHigh || isLow) ev[c + pl] = 1;
  }
  return ev;
}

/**
 * The epoch each bar belongs to, for the fixed-clock anchors.
 *
 * Bar times are ET wall-clock epoch seconds on the chart's gap-collapsing clock
 * (see replayEngine's Bar), so a day index is a plain division — the same
 * arithmetic lib/volRuler does to find 10:00 ET.
 */
function clockKey(bars: Bar[], mode: MvAnchor): Float64Array {
  const out = new Float64Array(bars.length);
  for (let i = 0; i < bars.length; i++) {
    const t = bars[i].time;
    if (mode === "rth") {
      // A bar before 09:30 belongs to the anchor that opened the morning before
      // it, so the line runs through the night rather than re-anchoring at
      // midnight.
      out[i] = Math.floor((t - 9.5 * 3600) / 86400);
    } else {
      const day = Math.floor((t - 18 * 3600) / 86400); // CME trading date
      // Epoch day 0 is a Thursday; +3 puts the week boundary on Monday.
      out[i] = mode === "week" ? Math.floor((day + 3) / 7) : day;
    }
  }
  return out;
}

// --- the whole thing --------------------------------------------------------

/**
 * Run the indicator over the drawn bars.
 *
 * `histCount` is where the context days end and the session begins — everything
 * is computed across the whole array so the medians, the KER and the anchors are
 * warm at the first session bar, and only the trending/undefined shares are
 * reported over the session alone.
 *
 * Note on the clock anchors: on `globex`/`rth` this computes a bar-weighted
 * VWAP, while the chart's own Globex and NY bands are accumulated tick by tick
 * in the engine. They will not agree to the tick, and the engine's is the better
 * number. These modes are here so the swing anchor has something to be compared
 * against inside its own indicator, not as a second copy of a band the chart
 * already draws properly.
 */
export function computeModernVwap(
  bars: Bar[],
  histCount: number,
  p: ModernVwapParams,
): ModernVwapData {
  const n = bars.length;
  if (n === 0) return EMPTY;

  const close = new Float64Array(n);
  const tp = new Float64Array(n);
  const vol = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    close[i] = b.close;
    tp[i] = (b.high + b.low + b.close) / 3;
    // A bar with no volume contributes nothing but must not zero the average;
    // the accumulator simply doesn't move on it.
    vol[i] = b.volume > 0 ? b.volume : 0;
  }

  // --- regime: two axes, each against its own trailing median.
  const ker = kerSeries(close, p.kerLen);
  const atr = atrSeries(bars, p.atrLen);
  const atrPct = new Float64Array(n);
  for (let i = 0; i < n; i++) atrPct[i] = close[i] ? atr[i] / close[i] : NaN;
  const medK = rollingMedian(ker, p.regimeLen);
  const medA = rollingMedian(atrPct, p.regimeLen);
  const quad = new Int8Array(n);
  for (let i = 0; i < n; i++) {
    quad[i] =
      !Number.isFinite(medK[i]) ||
      !Number.isFinite(medA[i]) ||
      !Number.isFinite(ker[i]) ||
      !Number.isFinite(atrPct[i])
        ? -1
        : // Ties count as low, as in the Pine.
          2 * (ker[i] > medK[i] ? 1 : 0) + (atrPct[i] > medA[i] ? 1 : 0);
  }

  // --- anchors.
  const pl = p.anchor === "swing" ? Math.max(1, p.pivot) : 0;
  let ev: Uint8Array;
  if (p.anchor === "swing") {
    ev = swingEvents(bars, pl);
  } else {
    const key = clockKey(bars, p.anchor);
    ev = new Uint8Array(n);
    for (let i = 1; i < n; i++) if (key[i] !== key[i - 1]) ev[i] = 1;
  }
  ev[0] = 1; // barstate.isfirst

  // --- the accumulator. On a swing anchor the pivot bar through the
  // confirmation bar is backfilled, so the new line starts at the swing rather
  // than at the moment we noticed it. That is why the line *steps* at a
  // confirmation — bars already drawn keep the old anchor's values, so what a
  // live reader sees is a step, not a rewritten past.
  const mid = new Float64Array(n).fill(NaN);
  const sd = new Float64Array(n).fill(NaN);
  const anchors: number[] = [];
  let sPv = 0;
  let sV = 0;
  let sP2v = 0;
  for (let i = 0; i < n; i++) {
    if (ev[i]) {
      sPv = sV = sP2v = 0;
      anchors.push(bars[i].time);
      if (pl && i >= pl) {
        for (let j = i - pl; j < i; j++) {
          sPv += tp[j] * vol[j];
          sV += vol[j];
          sP2v += tp[j] * tp[j] * vol[j];
        }
      }
    }
    sPv += tp[i] * vol[i];
    sV += vol[i];
    sP2v += tp[i] * tp[i] * vol[i];
    if (sV > 0) {
      const m = sPv / sV;
      mid[i] = m;
      sd[i] = Math.sqrt(Math.max(sP2v / sV - m * m, 0));
    }
  }

  // --- bands, and the ±2σ envelope the MR rule tests against. Always ±2σ
  // whatever `bands` draws: the rule is the rule, the knob is a drawing.
  const scale = (i: number) =>
    p.adaptive ? 1 + p.kerWeight * (1 - (Number.isFinite(ker[i]) ? ker[i] : 1)) : 1;
  const points: MvPoint[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const w = scale(i) * sd[i];
    points[i] = {
      time: bars[i].time,
      mid: mid[i],
      u1: mid[i] + w,
      l1: mid[i] - w,
      u2: mid[i] + 2 * w,
      l2: mid[i] - 2 * w,
      u3: mid[i] + 3 * w,
      l3: mid[i] - 3 * w,
      regime: quad[i],
      ker: ker[i],
    };
  }

  // --- signals. Every raw trigger is kept and `gated` records whether the
  // regime it fired in was its own; the display mode decides what is drawn.
  const signals: MvSignal[] = [];
  if (p.signals !== "none") {
    const occWin = Math.max(1, p.occWindow);
    const occMin = Math.min(Math.max(1, p.occMin), occWin);
    let anchorBar = 0;
    let longDl = -1;
    let shortDl = -1;
    for (let i = 0; i < n; i++) {
      if (ev[i]) anchorBar = i;
      const q = quad[i];
      const ranging = q === 0 || q === 1;
      const trending = q >= 2;
      const u2 = points[i].u2;
      const l2 = points[i].l2;

      // MR: close outside ±2σ, then a close back inside *both* bands, so a full
      // traverse fires nothing. Close-only on both legs.
      if (i > 0 && Number.isFinite(u2) && Number.isFinite(points[i - 1].u2)) {
        let side: "long" | "short" | null = null;
        if (close[i - 1] < points[i - 1].l2 && close[i] >= l2 && close[i] <= u2) side = "long";
        else if (close[i - 1] > points[i - 1].u2 && close[i] >= l2 && close[i] <= u2) side = "short";
        if (side)
          signals.push({
            time: bars[i].time,
            kind: "MR",
            side,
            gated: ranging,
            regime: q,
            price: close[i],
          });
      }

      // Side occupancy over the `occWin` bars *before* this one, and only once
      // the anchor is that far back. Causal — the current close is not in its
      // own window.
      let ctx = 0;
      if (i - anchorBar >= occWin) {
        let cnt = 0;
        for (let j = i - occWin; j < i; j++) if (Number.isFinite(mid[j]) && close[j] > mid[j]) cnt++;
        ctx = cnt >= occMin ? 1 : occWin - cnt >= occMin ? -1 : 0;
      }

      // TC: a touch of the line opens a window of `holdBars` for the close back
      // on side. Overlapping touches merge; the episode dies on a context flip
      // or an anchor reset; one signal per episode.
      if (!Number.isFinite(mid[i])) {
        longDl = shortDl = -1;
      } else {
        if (ctx !== 1) longDl = -1;
        if (ctx !== -1) shortDl = -1;
        if (ctx === 1 && bars[i].low <= mid[i]) longDl = Math.max(longDl, i + p.holdBars);
        if (ctx === -1 && bars[i].high >= mid[i]) shortDl = Math.max(shortDl, i + p.holdBars);
        if (longDl >= i && close[i] > mid[i]) {
          longDl = -1;
          signals.push({
            time: bars[i].time,
            kind: "TC",
            side: "long",
            gated: trending,
            regime: q,
            price: close[i],
          });
        }
        if (shortDl >= i && close[i] < mid[i]) {
          shortDl = -1;
          signals.push({
            time: bars[i].time,
            kind: "TC",
            side: "short",
            gated: trending,
            regime: q,
            price: close[i],
          });
        }
      }
    }
  }

  // The gate's shares are quoted over the session, not the context days behind
  // it — the warm-up is not part of what you are looking at.
  let trend = 0;
  let undef = 0;
  const sess = Math.max(0, n - histCount);
  for (let i = histCount; i < n; i++) {
    if (quad[i] >= 2) trend++;
    else if (quad[i] < 0) undef++;
  }

  return {
    points,
    signals: p.signals === "gated" ? signals.filter((s) => s.gated) : signals,
    anchors: p.anchorMarks ? anchors : [],
    trendPct: sess ? (100 * trend) / sess : 0,
    undefPct: sess ? (100 * undef) / sess : 0,
  };
}

// --- prefs ------------------------------------------------------------------

const pickNum = (v: unknown, opts: readonly number[], d: number): number =>
  typeof v === "number" && opts.includes(v) ? v : d;

/** Validate a stored blob against the offered shortlists — the sim.prefs rule:
 *  a hand-edited value that isn't on a list would leave its picker blank. */
export function modernVwapParams(raw: unknown): ModernVwapParams {
  const d = DEFAULT_MODERN_VWAP;
  if (!raw || typeof raw !== "object") return { ...d };
  const s = raw as Partial<Record<keyof ModernVwapParams, unknown>>;
  const occWindow = pickNum(s.occWindow, MV_OCC_WINDOW_OPTIONS, d.occWindow);
  return {
    anchor: MV_ANCHOR_OPTIONS.some((o) => o.value === s.anchor)
      ? (s.anchor as MvAnchor)
      : d.anchor,
    pivot: pickNum(s.pivot, MV_PIVOT_OPTIONS, d.pivot),
    bands: pickNum(s.bands, MV_BAND_OPTIONS, d.bands) as 1 | 2 | 3,
    adaptive: typeof s.adaptive === "boolean" ? s.adaptive : d.adaptive,
    kerWeight: pickNum(s.kerWeight, MV_KER_WEIGHT_OPTIONS, d.kerWeight),
    kerLen: pickNum(s.kerLen, MV_KER_LEN_OPTIONS, d.kerLen),
    atrLen: pickNum(s.atrLen, MV_ATR_LEN_OPTIONS, d.atrLen),
    regimeLen: pickNum(s.regimeLen, MV_REGIME_LEN_OPTIONS, d.regimeLen),
    regimeColor: typeof s.regimeColor === "boolean" ? s.regimeColor : d.regimeColor,
    signals: MV_SIGNAL_OPTIONS.some((o) => o.value === s.signals)
      ? (s.signals as MvSignalMode)
      : d.signals,
    occWindow,
    // Clamped against the window it was stored beside, not against the default:
    // the pair is only meaningful together.
    occMin: pickNum(s.occMin, mvOccMinOptions(occWindow), Math.min(d.occMin, occWindow)),
    holdBars: pickNum(s.holdBars, MV_HOLD_OPTIONS, d.holdBars),
    anchorMarks: typeof s.anchorMarks === "boolean" ? s.anchorMarks : d.anchorMarks,
  };
}
