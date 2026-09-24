// The higher-timeframe candles, drawn over the ones this chart already has.
//
// ATAS calls this the External Chart indicator: pick a coarser period, and it
// outlines that period's bars on top of the bars you are trading, as hollow
// rectangles. The read it buys is the one a second monitor usually costs — where
// this 1-minute push sits inside the 15-minute bar containing it, and whether
// the bar containing it is up or down at all. Blue outline for a bucket that
// closed above its open, red for one that closed below.
//
// A bucketing rule, not a data request — the same claim lib/timeframes makes
// about the drawn bar, and for the same reason. The bars are already on the
// chart; grouping them is arithmetic, so no fetch, no second engine pass and no
// way for the overlay to disagree with the candles underneath it. That matters
// more here than it would elsewhere: an HTF box whose high came from a different
// source than the wicks inside it would be a bug nobody could see.
//
// Each drawn bar is assigned to the bucket its *open* falls in. Exact whenever
// the drawn timeframe divides the external one (1m under 15m, 5m under 1h — the
// case this is used in), and well-defined when it doesn't: a 7-minute bar that
// straddles a 15-minute boundary lands wholly in the bucket it opened in rather
// than being split across both. Splitting would need the ticks, which is the one
// thing this module has decided not to ask for.

import type { Bar } from "./chartTypes";

/** One external-period candle, built from the drawn bars inside it.
 *
 *  `from`/`to` are the bar times (seconds) of the first and last drawn bar in
 *  the bucket rather than the bucket's own clock edges, because those are what
 *  the time scale can turn into x-coordinates: a boundary at 09:45:00 has no
 *  x on a chart whose bars are 500-tick, and on any chart it lands in whatever
 *  gap the session left there. Drawing between the bars that exist keeps the box
 *  pinned to the price action it summarises. */
export interface ExternalBar {
  from: number;
  to: number;
  open: number;
  high: number;
  low: number;
  close: number;
  /** The rightmost bucket, still taking bars in. Drawn with a dashed body so a
   *  box that is still moving is tellable from one that has closed — the same
   *  distinction the range tool draws with a solid right edge. Without it the
   *  newest box reads as a settled HTF bar that price has already left, which is
   *  the one misreading this layer could actually cost money on. */
  live: boolean;
}

/** The external periods offered, in seconds.
 *
 *  A shortlist, like every other knob the app owns: these are the bucketings
 *  that floor cleanly on the epoch, so a box boundary is the same wall-clock
 *  instant on every chart that draws one. ATAS runs its list up to Monthly; the
 *  daily-and-longer end is left off deliberately, because a futures "day" opens
 *  at 18:00 ET and a bucket floored on UTC midnight would cut the session in the
 *  middle and draw a box that is wrong rather than coarse. Adding it means
 *  anchoring on the session, which is lib/replayEngine's answer to give. */
export const EXTERNAL_PERIOD_OPTIONS = [
  { value: 300, label: "5m" },
  { value: 900, label: "15m" },
  { value: 1800, label: "30m" },
  { value: 3600, label: "1h" },
  { value: 7200, label: "2h" },
  { value: 14400, label: "4h" },
] as const;

export type ExternalPalette = "theme" | "custom";

export interface ExternalChartParams {
  /** Seconds per external candle — one of EXTERNAL_PERIOD_OPTIONS. */
  period: number;
  /** Whether the high/low box is drawn round the open/close body. ATAS's "Show
   *  Grid": it is what makes the overlay a candle rather than just a body. */
  grid: boolean;
  /** Whether the body is washed as well as outlined. Off by default — a filled
   *  box over a dense minute chart buries the candles it is meant to frame. */
  fill: boolean;
  /** Over the candles rather than behind them (ATAS's "Show above chart"). */
  above: boolean;
  /** Whether the hues follow the surface's ink or two colours picked here. */
  palette: ExternalPalette;
  bull: string;
  bear: string;
}

export const DEFAULT_EXTERNAL_CHART: ExternalChartParams = {
  period: 900,
  grid: true,
  fill: false,
  above: false,
  palette: "theme",
  bull: "#4d8bff",
  bear: "#f5455f",
};

const HEX = /^#[0-9a-f]{6}$/i;

/** Take a stored blob back as params, clamping every field to something
 *  drawable — the chartPrefs convention, so a hand-edited key or a setting from
 *  an older build can never put the layer in a state that throws at draw time. */
export function clampExternalChart(raw: unknown): ExternalChartParams {
  const o = (raw ?? {}) as Partial<ExternalChartParams>;
  const d = DEFAULT_EXTERNAL_CHART;
  const period = EXTERNAL_PERIOD_OPTIONS.some((p) => p.value === o.period) ? o.period! : d.period;
  return {
    period,
    grid: typeof o.grid === "boolean" ? o.grid : d.grid,
    fill: typeof o.fill === "boolean" ? o.fill : d.fill,
    above: typeof o.above === "boolean" ? o.above : d.above,
    palette: o.palette === "custom" ? "custom" : "theme",
    bull: typeof o.bull === "string" && HEX.test(o.bull) ? o.bull : d.bull,
    bear: typeof o.bear === "string" && HEX.test(o.bear) ? o.bear : d.bear,
  };
}

/**
 * The drawn bars, grouped into external-period candles.
 *
 * `bars` must be in ascending time, which is what both charts hold. Buckets come
 * back in the same order, so the caller can draw them without sorting.
 *
 * Returns an empty list when the external period is no coarser than the bars it
 * was handed — a 15-minute overlay on a 15-minute chart is one box per candle,
 * which is not a second read but the same one drawn twice. The caller shows that
 * as a dimmed legend row rather than a chart with a box round every bar.
 */
export function groupExternalBars(bars: readonly Bar[], period: number): ExternalBar[] {
  if (period <= 0 || bars.length === 0) return [];

  const out: ExternalBar[] = [];
  let key = NaN;
  let cur: ExternalBar | null = null;

  for (const b of bars) {
    const k = Math.floor(b.time / period);
    if (!cur || k !== key) {
      key = k;
      cur = {
        from: b.time,
        to: b.time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        live: false,
      };
      out.push(cur);
      continue;
    }
    cur.to = b.time;
    if (b.high > cur.high) cur.high = b.high;
    if (b.low < cur.low) cur.low = b.low;
    cur.close = b.close;
  }

  // Only the last one is still open. On a replay that is the bucket the clock is
  // inside; on a finished day it is the one the session ended in, which is
  // equally "not a closed external bar" and should say so.
  if (out.length) out[out.length - 1].live = true;

  // One box per drawn bar is the degenerate case above: the overlay is only a
  // second read when it groups. Measured on the bars actually handed over rather
  // than by comparing timeframe ids, which a tick bar has no way to answer.
  return out.length === bars.length ? [] : out;
}
