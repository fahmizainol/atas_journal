// The Modern VWAP layer, as the one thing both chart components mount.
//
// lib/modernVwap is the indicator — the numbers. This is what it *looks like* on
// a pane: seven line series (the mid and up to three σ rings a side), the
// ±1σ→±2σ wash under them, and the primitive that draws the MR/TC marks and the
// anchor ticks. It lived inside ReplayChart until the Interactions Lab wanted
// the same layer over its stitched multi-session tape, and a second copy of the
// ring bookkeeping and the regime tinting is how two charts start disagreeing
// about what one indicator looks like.
//
// The layer owns its own visibility, because the *cost* rides on it: with both
// rows off nothing is computed at all (this is the one layer on either chart
// whose compute is worth avoiding when nobody is looking at it, and it is off by
// default). So turning a row back on has to redraw — which is why the layer is
// built with a `source` it can pull from rather than being pushed at: a cached
// copy of the last inputs would be one bar stale on the replay every time.

import {
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { ModernVwapPrimitive } from "./ModernVwapPrimitive";
import { VwapBandPrimitive } from "./VwapBandPrimitive";
import {
  computeModernVwap,
  type ModernVwapData,
  type ModernVwapParams,
  type MvBar,
  type MvContext,
  type MvPoint,
} from "../../lib/modernVwap";
import { ink } from "../../theme";

/** The seven line series, in the order the indicator names them. */
export const MV_KEYS = ["mid", "u1", "l1", "u2", "l2", "u3", "l3"] as const;
export type MvKey = (typeof MV_KEYS)[number];
/** Which σ ring each series is. 0 is the mid line. Module scope because both the
 *  builder (which sets the dash and the weight from it) and the draw (which sets
 *  the alpha, and skips a ring above the chosen envelope) need the same answer —
 *  and because the price-scale edge markers ask it again. */
export const MV_RING: Record<MvKey, number> = {
  mid: 0,
  u1: 1,
  l1: 1,
  u2: 2,
  l2: 2,
  u3: 3,
  l3: 3,
};

/** Alpha per ring, weighted toward the ±2σ envelope because that is the one the
 *  MR rule actually tests — the others are context. These sat a third lower and
 *  the outer rings read as smudges rather than levels; a band you have to hunt
 *  for is a band you end up reading off the mid line instead. */
const RING_ALPHA: Record<number, number> = { 1: 0.7, 2: 0.95, 3: 0.5 };

/** Everything the indicator is computed from, pulled fresh on every redraw.
 *  Null when the page doesn't offer the layer at all. */
export interface MvSource {
  bars: readonly MvBar[];
  /** Where the context bars end and the session begins: everything is computed
   *  across the whole array so the medians, the KER and the anchors are warm at
   *  the first session bar, and only the trending/undefined shares are reported
   *  over the session alone. */
  histCount: number;
  params: ModernVwapParams | null | undefined;
  ctx?: MvContext;
}

export interface ModernVwapLayer {
  /**
   * Recompute and redraw. Call it whenever the bars or the parameters moved.
   *
   * No parameters (the page doesn't offer the layer), both rows hidden, or no
   * bars draws nothing and computes nothing — the series are emptied rather than
   * left stale, and the `onData` sink is told the layer is dark rather than
   * handed old numbers.
   */
  redraw(): void;
  /**
   * Attach the trigger marks, at the point in the pane's primitive stack where
   * they belong — primitives of the same z-order draw in attachment order, and
   * on the replay this layer's marks go *above* the tape's own marks and below
   * the open position. The wash goes on at build time (it is a background fill
   * and has nowhere else to be); this one is the caller's call.
   */
  attachSignals(): void;
  /** The two legend rows: the line (with its wash) and the trigger marks.
   *  Redraws by itself when that takes the layer out of, or into, dark. */
  setVisible(line: boolean, signals: boolean): void;
  /** Push the now-active ink onto the series. The wash and the per-point regime
   *  tint ride on the *data*, so they come back with the redraw this ends with;
   *  a line series carries its colour in its options and has to be told. */
  relight(): void;
  /** The newest point and how many rings are drawn at it — what the price-scale
   *  edge markers need, and a series' own `data()` hands back a copy of the whole
   *  history to get at the last one. Null while the layer is dark. */
  last(): { pt: MvPoint; bands: number } | null;
  /** The newest `n` points, oldest first — the indicator's own per-bar path over
   *  a short window, for a reader that has to know where these rings *were* and
   *  not only where they are (see lib/levelApproach). Shorter than asked for
   *  early in a session, and capped at `TAIL_MAX`. Empty while the layer is
   *  dark, which is the same answer `last()` gives. */
  tail(n: number): MvPoint[];
}

/** How many points `tail()` can serve. Bounded because this exists to answer a
 *  five-bar question and retaining a whole session's rings to do it would be a
 *  copy of the series we deliberately don't read back. */
const TAIL_MAX = 16;

/**
 * Build the layer on `chart`, with its wash and marks attached to `candle`.
 *
 * `source` is pulled on every redraw, including the ones the layer does on its
 * own — see `setVisible`.
 *
 * `onData` is where the legend's numbers come from: every redraw reports what it
 * computed, or null when it drew nothing. A sink rather than a return value
 * because the layer redraws on its own (see `setVisible`), and a legend quoting
 * a share of the session that a toggle silently invalidated is worse than one
 * that says nothing.
 *
 * The ink is read per draw rather than captured, so a surface change picks up for
 * free — the regime tint rides on per-point colours, and those are re-set anyway.
 */
export function createModernVwapLayer(
  chart: IChartApi,
  candle: ISeriesApi<"Candlestick">,
  source: () => MvSource,
  onData?: (d: ModernVwapData | null) => void,
): ModernVwapLayer {
  const hue = () => ink().modernVwap;
  // Seven lines and the same ±1σ→±2σ wash the other four anchors draw. The wash
  // was left off at first because this one's bands carry the regime colour and a
  // flat fill under a tinted envelope is two colour channels arguing over the
  // same pixels — so the fill takes the regime triplet too (see `render`), and
  // they argue about nothing. Built empty and hidden; the first `setVisible`
  // from the page's persisted toggles decides whether any of it is drawn.
  const lines = Object.fromEntries(
    MV_KEYS.map((k) => [
      k,
      chart.addSeries(LineSeries, {
        color: k === "mid" ? hue().middle : hue().band,
        // The mid and the ±2σ envelope are the two lines a rule is ever read
        // off, so both are solid; ±1σ and ±3σ stay dashed. Weight still
        // separates the mid from its envelope, so the ring you are looking at is
        // legible without counting outward from the middle.
        lineWidth: MV_RING[k] === 0 ? 2 : 1,
        lineStyle: MV_RING[k] === 0 || MV_RING[k] === 2 ? LineStyle.Solid : LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
        // The envelope is drawn, not fitted. A 3σ ring on a fresh swing anchor is
        // a long way from price, and letting it into the autoscale spends a third
        // of the pane on empty air — the same call the demo page makes.
        ...(k === "mid" ? {} : { autoscaleInfoProvider: () => null }),
      }),
    ]),
  ) as Record<MvKey, ISeriesApi<"Line">>;
  const band = new VwapBandPrimitive([], hue().fill, 0.45);
  candle.attachPrimitive(band as any);
  band.setVisible(false);
  const prim = new ModernVwapPrimitive();

  let showLine = false;
  let showSignals = false;
  let last: { pt: MvPoint; bands: number } | null = null;
  /** The newest points, kept for `tail()`. The redraw already has the whole
   *  per-bar array in hand and was throwing all but its last entry away; a
   *  bounded slice of it costs nothing and is the only per-bar path of this
   *  indicator that survives the render. */
  let tailPts: MvPoint[] = [];

  const render = () => {
    // Nothing is pulled while the layer is dark — the source itself may be doing
    // work (the replay builds its POC map into one), and the whole point of the
    // dark branch is that a hidden layer costs nothing.
    const inp = showLine || showSignals ? source() : null;
    const p = inp?.params;
    if (!inp || !p || inp.bars.length === 0) {
      for (const k of MV_KEYS) lines[k].setData([]);
      band.setPoints([]);
      prim.setData([], [], new Map());
      last = null;
      tailPts = [];
      onData?.(null);
      return;
    }
    const d = computeModernVwap(inp.bars as MvBar[], inp.histCount, p, inp.ctx);
    last = d.points.length ? { pt: d.points[d.points.length - 1], bands: p.bands } : null;
    tailPts = d.points.slice(-TAIL_MAX);

    // Per-point colour rather than a series colour: the regime read is the only
    // thing on this indicator that changes bar to bar, and the band it tints is
    // where it belongs (his own choice, and the reason the mid line gives up its
    // hue to stay legible under it).
    const regimeRgb = (pt: MvPoint): string => {
      const r = hue().regime;
      return pt.regime < 0 ? r.undefined : pt.regime >= 2 ? r.trending : r.ranging;
    };
    const tint = (pt: MvPoint, alpha: number): string | undefined =>
      p.regimeColor ? `rgba(${regimeRgb(pt)}, ${alpha})` : undefined;
    for (const k of MV_KEYS) {
      // A ring above the chosen envelope draws nothing at all — the series is
      // kept (creating and destroying series on a knob turn is how a chart
      // leaks) and simply handed an empty array.
      if (MV_RING[k] > p.bands) {
        lines[k].setData([]);
        continue;
      }
      lines[k].setData(
        d.points.map((pt) => {
          const v = pt[k];
          // A gap, not a joined line: before the accumulator has any volume there
          // is no value, and drawing across it would invent one.
          if (!Number.isFinite(v)) return { time: pt.time as Time };
          return k === "mid"
            ? { time: pt.time as Time, value: v }
            : { time: pt.time as Time, value: v, color: tint(pt, RING_ALPHA[MV_RING[k]]) };
        }),
      );
    }
    // The wash shades ±1σ→±2σ, so it needs both rings to exist: at `bands: 1`
    // there is no outer edge to fill to and the region is simply not drawn. Tint
    // and points are set together — the tint indexes into the array below.
    band.setPoints(
      p.bands < 2
        ? []
        : d.points.map((pt) => ({
            time: pt.time,
            middle: pt.mid,
            upper1: pt.u1,
            lower1: pt.l1,
            upper2: pt.u2,
            lower2: pt.l2,
          })),
    );
    band.setTint(p.regimeColor ? (i) => regimeRgb(d.points[i]) : null);
    prim.setData(
      d.signals,
      p.anchorMarks ? d.anchors : [],
      new Map(inp.bars.map((b) => [b.time, b])),
    );
    onData?.(d);
  };

  return {
    redraw: render,
    attachSignals() {
      candle.attachPrimitive(prim as any);
    },
    setVisible(line, signals) {
      const wasDark = !showLine && !showSignals;
      showLine = line;
      showSignals = signals;
      for (const k of MV_KEYS) lines[k].applyOptions({ visible: line });
      // The wash belongs to the lines, not to the marks — a fill with no envelope
      // over it is a stain.
      band.setVisible(line);
      prim.setVisible(signals);
      // Only when the layer crossed into or out of dark: hiding one of two live
      // rows is a `visible` flip and nothing more, and recomputing a session's
      // worth of bars to do it would be the most expensive no-op on the chart.
      if (wasDark !== (!line && !signals)) render();
    },
    relight() {
      const h = hue();
      for (const k of MV_KEYS) {
        lines[k].applyOptions({ color: k === "mid" ? h.middle : h.band });
      }
      band.setRgb(h.fill);
      // The regime tint rides on per-point colours, so it comes back only when
      // the data is re-set.
      render();
    },
    last: () => last,
    tail: (n) => (n >= tailPts.length ? tailPts.slice() : tailPts.slice(-n)),
  };
}
