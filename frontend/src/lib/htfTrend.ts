// The higher-timeframe trend, read onto the chart you are actually watching.
//
// The failure this exists for: the 30-second MAs roll over, the short goes on,
// and the 5- and 15-minute charts — which were in an uptrend and pulling back to
// their 20 — were never looked at. A second pane holds the answer but not the
// eye. So the answer is brought into this pane twice: each higher frame's EMA as
// a stepped line (the level the pullback is heading for), and the frames'
// agreement as a background wash (which way you should be leaning at all).
//
// The rule is the one `data/research/htf-alignment/audit.py` scored the manual
// book with, so the chart says exactly what the study measured:
//
//   per frame   up   = last CLOSED bar's close above its EMA, and the EMA higher
//                      than it was `SLOPE_BARS` closed bars ago
//               down = the mirror;  anything else is flat
//   combined    up/down only when every selected frame agrees; else nothing
//
// Closed bars only, everywhere. The frame's bucket still forming is left out of
// both the EMA and the state, so neither can move inside a bucket and neither
// can know a close that hasn't printed — the line steps once per frame bar, the
// same way a higher-timeframe study with no lookahead does. That also makes the
// study's labels and the wash the same function of the same bars.
//
// Built from the drawn bars regrouped, like lib/externalChart and for the same
// reason: no fetch, and no way to disagree with the candles. The price of that is
// warm-up — a 15m EMA20 needs five hours of bars on the chart before it means
// anything, so a frame draws nothing until it has `length` closed buckets and
// votes nothing until it also has the slope's lookback. The legend says so.
//
// Audit result (2026-09-24, see memory htf-alignment-audit): with-trend beat
// against-trend in every cohort, but no interval excluded zero. This is a
// context layer, not a signal — it never gates or warns.

import type { Bar } from "./chartTypes";

/** Closed frame bars the EMA's slope is read over. The audit's value. */
export const SLOPE_BARS = 3;

/** The frame sets offered. A shortlist of combinations rather than a free
 *  multi-select, because the settings panel is selects and the useful sets are
 *  few. Everything here floors cleanly on the clock (see lib/externalChart on
 *  why the list stops short of a day). */
export const HTF_FRAME_SETS = [
  { value: "300,900", label: "5m + 15m" },
  { value: "300,900,3600", label: "5m + 15m + 1h" },
  { value: "900,3600", label: "15m + 1h" },
  { value: "300", label: "5m" },
  { value: "900", label: "15m" },
] as const;

export const HTF_LENGTHS = [9, 20, 50] as const;

export type HtfTint = "wash" | "ribbon" | "off";

export interface HtfTrendParams {
  /** One of HTF_FRAME_SETS' values — seconds, comma-joined, ascending. */
  frames: string;
  /** EMA length on every frame. */
  length: number;
  /** Draw each frame's EMA as a stepped line. */
  lines: boolean;
  /** How the combined state is shown: a full-height wash, a strip along the
   *  bottom of the pane, or not at all (lines only). */
  tint: HtfTint;
}

export const DEFAULT_HTF_TREND: HtfTrendParams = {
  frames: "300,900",
  length: 20,
  lines: true,
  tint: "wash",
};

export function clampHtfTrend(raw: unknown): HtfTrendParams {
  const o = (raw ?? {}) as Partial<HtfTrendParams>;
  const d = DEFAULT_HTF_TREND;
  return {
    frames: HTF_FRAME_SETS.some((s) => s.value === o.frames) ? o.frames! : d.frames,
    length: (HTF_LENGTHS as readonly number[]).includes(o.length as number) ? o.length! : d.length,
    lines: typeof o.lines === "boolean" ? o.lines : d.lines,
    tint: o.tint === "ribbon" || o.tint === "off" || o.tint === "wash" ? o.tint : d.tint,
  };
}

export function framePeriods(p: HtfTrendParams): number[] {
  return p.frames.split(",").map(Number);
}

export function framesLabel(p: HtfTrendParams): string {
  return HTF_FRAME_SETS.find((s) => s.value === p.frames)?.label ?? p.frames;
}

export function periodLabel(sec: number): string {
  return sec >= 3600 ? `${sec / 3600}h` : `${sec / 60}m`;
}

/** +1 up, −1 down, 0 flat / not yet warm. */
export type TrendState = 1 | -1 | 0;

export interface HtfFrame {
  period: number;
  /** Per drawn bar: the EMA as of the last closed frame bar, or null while the
   *  frame is still warming up (fewer than `length` closed buckets). */
  ema: (number | null)[];
  /** Per drawn bar: this frame's vote. */
  state: TrendState[];
}

export interface HtfTrend {
  frames: HtfFrame[];
  /** Per drawn bar: every frame agrees, or 0. */
  combined: TrendState[];
  /** Whether any frame drew at least one EMA point — false on a chart too short
   *  (or too coarse) for the frames asked for, which the legend dims. */
  warm: boolean;
}

function frameOf(bars: readonly Bar[], period: number, length: number): HtfFrame {
  const n = bars.length;
  const ema: (number | null)[] = new Array(n).fill(null);
  const state: TrendState[] = new Array(n).fill(0);
  const alpha = 2 / (length + 1);

  // Closed-bucket history, only as deep as the slope needs.
  const hist: number[] = [];
  let closed = 0;
  let e = NaN;
  let lastClose = NaN;

  let key = NaN;
  let bucketClose = NaN;

  for (let i = 0; i < n; i++) {
    const b = bars[i];
    const k = Math.floor(b.time / period);
    if (k !== key) {
      // The bucket before this one just closed: fold its close in.
      if (!Number.isNaN(key)) {
        e = closed === 0 ? bucketClose : alpha * bucketClose + (1 - alpha) * e;
        closed++;
        lastClose = bucketClose;
        hist.push(e);
        if (hist.length > SLOPE_BARS + 1) hist.shift();
      }
      key = k;
    }
    bucketClose = b.close;

    if (closed >= length) {
      ema[i] = e;
      if (closed >= length + SLOPE_BARS && hist.length === SLOPE_BARS + 1) {
        const then = hist[0];
        if (lastClose > e && e > then) state[i] = 1;
        else if (lastClose < e && e < then) state[i] = -1;
      }
    }
  }
  // One bucket per drawn bar means the frame is no coarser than the chart — a
  // 5m frame over a 5m chart would redraw each bar's own EMA one bar late.
  // Measured on the bars handed over, as lib/externalChart does, because a
  // tick chart has no fixed spacing to compare a period against.
  if (n > 1 && closed + 1 === n) return { period, ema: [], state: [] };
  return { period, ema, state };
}

/**
 * Every selected frame's EMA and vote, per drawn bar, plus their agreement.
 *
 * `bars` ascending, as both charts hold them. Frames not coarser than the drawn
 * bars are dropped — they would be the chart's own bars again — so a 5m + 15m
 * set on a 5-minute chart reads as 15m alone.
 */
export function computeHtfTrend(bars: readonly Bar[], p: HtfTrendParams): HtfTrend {
  const frames = framePeriods(p)
    .map((sec) => frameOf(bars, sec, p.length))
    .filter((f) => f.ema.length === bars.length);
  const n = bars.length;
  const combined: TrendState[] = new Array(n).fill(0);
  if (frames.length) {
    for (let i = 0; i < n; i++) {
      const s0 = frames[0].state[i];
      if (s0 === 0) continue;
      let agree = true;
      for (let f = 1; f < frames.length; f++) {
        if (frames[f].state[i] !== s0) {
          agree = false;
          break;
        }
      }
      if (agree) combined[i] = s0;
    }
  }
  const warm = frames.some((f) => f.ema.some((v) => v != null));
  return { frames, combined, warm };
}
