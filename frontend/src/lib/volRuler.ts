// The vol ruler: the chart's bar-range volatility, read three ways at once —
// an ATR(14) line (the indicator as every platform draws it), the session's
// developing median bar range (the same information as one number that cannot
// be yanked by the last hour), and yesterday's settled median as the flat
// causal reference. All in ticks, drawn in their own pane under the candles,
// with the 50-tick stop as a fixed rule to read them against.
//
// Why the median exists next to the ATR: the ATR remembers ~14 bars, so
// mid-session it reports the last hour, not the day — it spikes on the open,
// sinks in the lunch lull, and hands you a range instead of a number. The
// expanding median since 10:00 ET converges on the day's character an hour or
// two in and one wild stretch cannot drag it. See docs/research/atr-vs-r5.html
// for the study this pane is the live version of.
//
// Everything is computed from the drawn bars at whatever resolution the chart
// is showing — a 1-minute bar is about half a 5-minute bar, so the numbers
// shrink when the timeframe does. That is a property of the question ("how big
// are the bars I am looking at"), not a bug in the answer.

import type { Bar } from "./replayEngine";

/** The stop the ruler is read against, in ticks — the manual bracket this
 *  whole pane exists to sanity-check before the open. */
export const VR_STOP_TICKS = 50;

const ATR_PERIOD = 14;
/** 10:00–16:00 ET, as seconds-of-day on the ET-shifted bar clock. The first
 *  half hour runs 2–4× the rest of the day everywhere and would drag the
 *  median of a short session; the settled window is what the demo's ladder was
 *  calibrated on. */
const WIN_START = 10 * 3600;
const WIN_END = 16 * 3600;
/** An expanding median over fewer bars than this says nothing yet. */
const MIN_BARS = 3;

export interface VolRulerPoint {
  time: number;
  value: number;
}

export interface VolRulerData {
  /** Wilder ATR(ATR_PERIOD) over the drawn bars, in ticks — session bars only,
   *  but warmed through the context days so it has a value from the first bar. */
  atr: VolRulerPoint[];
  /** The expanding median of session bar ranges since 10:00 ET, in ticks. */
  dev: VolRulerPoint[];
  /** The prior session's settled (10:00–16:00) median bar range in ticks, from
   *  the context days when any are loaded — null without them. */
  yday: number | null;
}

/** Seconds-of-day for a bar time (ET wall clock carried on the UTC epoch). */
const tod = (t: number) => ((t % 86400) + 86400) % 86400;

/** Median of a sorted array. */
const medianOf = (sorted: number[]): number => {
  const n = sorted.length;
  const mid = n >> 1;
  return n % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

/** Insert `v` into `sorted` in place, keeping order — O(n), which over a
 *  session of appends is the same total work as one sort. */
const insertSorted = (sorted: number[], v: number) => {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < v) lo = mid + 1;
    else hi = mid;
  }
  sorted.splice(lo, 0, v);
};

/** Recompute the whole ruler from the drawn bars. Called on snapshot and on
 *  bar close, never per tick — the last (still-forming) bar is excluded so
 *  every value is a fact about a closed bar. */
export function computeVolRuler(bars: Bar[], histCount: number, tickSize: number): VolRulerData {
  const closed = bars.length > 0 ? bars.length - 1 : 0;
  const atr: VolRulerPoint[] = [];
  const dev: VolRulerPoint[] = [];
  if (closed === 0 || tickSize <= 0) return { atr, dev, yday: null };

  // --- ATR, Wilder's smoothing, seeded with the simple mean of the first
  // period's true ranges. Computed across the context days too so the line is
  // already warm at the session's first bar; only session points are emitted.
  let atrVal = 0;
  let seedSum = 0;
  for (let i = 0; i < closed; i++) {
    const b = bars[i];
    const pc = i > 0 ? bars[i - 1].close : b.open;
    const tr =
      Math.max(b.high, pc) - Math.min(b.low, pc);
    if (i < ATR_PERIOD) {
      seedSum += tr;
      atrVal = seedSum / (i + 1);
    } else {
      atrVal += (tr - atrVal) / ATR_PERIOD;
    }
    if (i >= histCount && i + 1 >= ATR_PERIOD)
      atr.push({ time: b.time, value: atrVal / tickSize });
  }

  // --- The developing median: session bars inside the settled window, each
  // point the median of every qualifying range up to and including its own bar.
  const sorted: number[] = [];
  for (let i = histCount; i < closed; i++) {
    const b = bars[i];
    const s = tod(b.time);
    if (s < WIN_START || s >= WIN_END) continue;
    insertSorted(sorted, (b.high - b.low) / tickSize);
    if (sorted.length >= MIN_BARS) dev.push({ time: b.time, value: medianOf(sorted) });
  }

  // --- Yesterday: the last calendar day among the context bars with a settled
  // window to read. Context days are whole sessions, so when any are loaded the
  // most recent one has one; a handful of bars is not a day.
  let yday: number | null = null;
  if (histCount > 0) {
    const byDay = new Map<number, number[]>();
    for (let i = 0; i < histCount; i++) {
      const b = bars[i];
      const s = tod(b.time);
      if (s < WIN_START || s >= WIN_END) continue;
      const day = Math.floor(b.time / 86400);
      let arr = byDay.get(day);
      if (!arr) byDay.set(day, (arr = []));
      arr.push((b.high - b.low) / tickSize);
    }
    const days = [...byDay.keys()].sort((a, b) => a - b);
    for (let d = days.length - 1; d >= 0; d--) {
      const arr = byDay.get(days[d])!;
      if (arr.length >= MIN_BARS * 2) {
        arr.sort((a, b) => a - b);
        yday = medianOf(arr);
        break;
      }
    }
  }

  return { atr, dev, yday };
}
