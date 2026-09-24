// The Dynamic Swing VWAP layer, as the one thing both chart components mount.
//
// lib/dynamicSwingVwap is the indicator — the numbers. This is what it *looks
// like* on a pane, which for this one is three things rather than a line:
//
//   * the **live segment**, as a line series, so it is fitted by the price scale
//     and readable under the crosshair like every other level on the chart, with
//     its optional σ rings and the ±1σ→±2σ wash under them;
//   * the **frozen segments** behind it (DsvSegmentPrimitive), which cannot be a
//     series because they overlap each other and the live one;
//   * the **swing flags** (DsvFlagPrimitive) marking where each was anchored,
//     and — when `shadow` is on — a ghosted preview of the segment a flip on the
//     next bar would produce, its σ rings with it, with its own hollow flag.
//
// Built as a layer rather than inline in either chart for the reason
// modernVwapLayer gives: a second copy of the same bookkeeping is how two charts
// start disagreeing about what one indicator looks like.
//
// The layer owns its own visibility, because the *cost* rides on it: with the row
// off nothing is computed at all, and it is off by default. So turning it back on
// has to redraw — which is why the layer is built with a `source` it can pull
// from rather than being pushed at: a cached copy of the last inputs would be one
// bar stale on the replay every time.

import {
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { DsvFlagPrimitive } from "./DsvFlagPrimitive";
import { DsvSegmentPrimitive } from "./DsvSegmentPrimitive";
import { VwapBandPrimitive } from "./VwapBandPrimitive";
import {
  computeDynamicSwingVwap,
  type DsvBar,
  type DsvParams,
  type DsvPoint,
  type DynamicSwingVwapData,
} from "../../lib/dynamicSwingVwap";
import { ink } from "../../theme";

/** The weight both the live line and the frozen ones are drawn at — his `xx`
 *  input, which is a style choice rather than a parameter of the indicator. */
const WIDTH = 2;

/** How many points `tail()` can serve — modernVwapLayer's cap, and the same
 *  reasoning: this answers a five-bar question, not a session-long one. */
const DSV_TAIL_MAX = 16;

/** The six σ rings, and which ring each is. Deliberately the same dash, weight
 *  and alpha conventions modernVwapLayer uses — the two indicators are read on
 *  the same pane, and an envelope that means the same thing should not look
 *  different depending on which VWAP drew it. */
const RINGS = [
  { key: "u1", ring: 1, up: true },
  { key: "l1", ring: 1, up: false },
  { key: "u2", ring: 2, up: true },
  { key: "l2", ring: 2, up: false },
  { key: "u3", ring: 3, up: true },
  { key: "l3", ring: 3, up: false },
] as const;
const RING_ALPHA: Record<number, number> = { 1: 0.7, 2: 0.95, 3: 0.5 };

/** Everything the indicator is computed from, pulled fresh on every redraw.
 *  Null params when the page doesn't offer the layer at all. */
export interface DsvSource {
  bars: readonly DsvBar[];
  /** Where the context bars end and the session begins: everything is computed
   *  across the whole array so the ATR baseline and the latches are warm at the
   *  first session bar, and only the bullish share is reported over the session
   *  alone. */
  histCount: number;
  params: DsvParams | null | undefined;
}

export interface DynamicSwingVwapLayer {
  /** Recompute and redraw. Call it whenever the bars or the parameters moved.
   *  No parameters, hidden, or no bars draws nothing and computes nothing. */
  redraw(): void;
  /** Attach the frozen segments and the flags, at the point in the pane's
   *  primitive stack where they belong — primitives of the same z-order draw in
   *  attachment order, so this is the caller's call rather than something done
   *  at build time. */
  attachOverlays(): void;
  /** The legend row. Redraws by itself when that takes the layer out of, or
   *  into, dark. */
  setVisible(on: boolean): void;
  /** Push the now-active ink. */
  relight(): void;
  /** The newest point of the live segment — what a price-scale edge marker would
   *  need, and a series' own `data()` hands back a copy of the whole history to
   *  get at the last one. Null while the layer is dark. */
  last(): DsvPoint | null;
  /** The live segment's newest `n` points, oldest first — this line's own path
   *  over a short window (see lib/levelApproach). Empty while dark, and short
   *  right after a flip, when the new anchor genuinely has no history. */
  tail(n: number): DsvPoint[];
}

/**
 * Build the layer on `chart`, with its overlays attached to `candle`.
 *
 * `source` is pulled on every redraw, including the ones the layer does on its
 * own — see `setVisible`.
 *
 * `onData` is where the legend's numbers come from: every redraw reports what it
 * computed, or null when it drew nothing. A sink rather than a return value
 * because the layer redraws on its own, and a legend quoting a share of the
 * session that a toggle silently invalidated is worse than one that says nothing.
 */
export function createDynamicSwingVwapLayer(
  chart: IChartApi,
  candle: ISeriesApi<"Candlestick">,
  source: () => DsvSource,
  onData?: (d: DynamicSwingVwapData | null) => void,
): DynamicSwingVwapLayer {
  const hue = () => ink().dynamicSwingVwap;
  const line = chart.addSeries(LineSeries, {
    color: `rgb(${hue().flat})`,
    lineWidth: WIDTH,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    visible: false,
  });
  // Built empty and hidden; `bands` decides per redraw how many are given data.
  // Kept rather than created and destroyed on a knob turn — that is how a chart
  // leaks — and excluded from the price-scale fit, because a fresh anchor's ±3σ
  // ring is a long way from price and letting it into the autoscale spends a
  // third of the pane on empty air. Same call modernVwapLayer makes.
  const rings = RINGS.map((r) => ({
    ...r,
    series: chart.addSeries(LineSeries, {
      color: `rgb(${hue().bull})`,
      lineWidth: 1,
      lineStyle: r.ring === 2 ? LineStyle.Solid : LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: false,
      autoscaleInfoProvider: () => null,
    }),
  }));
  const wash = new VwapBandPrimitive([], hue().bull, 0.45);
  candle.attachPrimitive(wash as any);
  wash.setVisible(false);
  const segs = new DsvSegmentPrimitive();
  const flags = new DsvFlagPrimitive();

  let shown = false;
  let last: DsvPoint | null = null;
  /** The live segment's newest points, kept for `tail()` — see the Modern VWAP
   *  layer's, and the same reason. Scoped to the *live* segment on purpose: a
   *  window that straddled a flip would be two different anchors' lines read as
   *  one path, which is not a level moving, it is a level being replaced. */
  let tailPts: DsvPoint[] = [];

  const render = () => {
    // Nothing is pulled while the layer is dark — the source itself may be doing
    // work, and the whole point of the dark branch is that a hidden layer costs
    // nothing.
    const inp = shown ? source() : null;
    const p = inp?.params;
    if (!inp || !p || inp.bars.length === 0) {
      line.setData([]);
      for (const r of rings) r.series.setData([]);
      wash.setPoints([]);
      segs.setShadow(null);
      segs.setData([]);
      flags.setData([], false, null);
      last = null;
      tailPts = [];
      onData?.(null);
      return;
    }
    const d = computeDynamicSwingVwap(inp.bars as DsvBar[], inp.histCount, p);
    const pts = d.live?.points ?? [];
    last = pts.length ? pts[pts.length - 1] : null;
    tailPts = pts.slice(-DSV_TAIL_MAX);

    const h = hue();
    // One colour for the whole series: the live segment is a single leg, so
    // unlike Modern VWAP's regime tint there is nothing here that changes bar to
    // bar. The frozen ones carry their own directions in the primitive.
    line.applyOptions({ color: `rgb(${d.live?.dir === -1 ? h.bear : h.bull})` });
    line.setData(
      pts.map((pt) =>
        // A gap, not a joined line: before any volume has traded under the anchor
        // there is no value, and drawing across it would invent one.
        Number.isFinite(pt.value)
          ? { time: pt.time as Time, value: pt.value }
          : { time: pt.time as Time },
      ),
    );
    // The rings, in the live segment's own colour. A ring above the chosen
    // envelope is handed an empty array rather than torn down.
    const rgb = d.live?.dir === -1 ? h.bear : h.bull;
    for (const r of rings) {
      r.series.applyOptions({
        color: `rgba(${rgb}, ${RING_ALPHA[r.ring]})`,
        visible: shown && r.ring <= p.bands,
      });
      r.series.setData(
        r.ring > p.bands
          ? []
          : pts.map((pt) =>
              Number.isFinite(pt.value) && Number.isFinite(pt.sd)
                ? {
                    time: pt.time as Time,
                    value: pt.value + (r.up ? pt.sd : -pt.sd) * r.ring,
                  }
                : { time: pt.time as Time },
            ),
      );
    }
    // The wash shades ±1σ→±2σ, so it needs both rings to exist: at one ring
    // there is no outer edge to fill to and the region is simply not drawn.
    wash.setRgb(rgb);
    wash.setVisible(shown && p.bands >= 2);
    wash.setPoints(
      p.bands < 2
        ? []
        : pts
            .filter((pt) => Number.isFinite(pt.value) && Number.isFinite(pt.sd))
            .map((pt) => ({
              time: pt.time,
              middle: pt.value,
              upper1: pt.value + pt.sd,
              lower1: pt.value - pt.sd,
              upper2: pt.value + 2 * pt.sd,
              lower2: pt.value - 2 * pt.sd,
            })),
    );
    segs.setInk(h.bull, h.bear);
    segs.setWidth(WIDTH);
    segs.setBands(p.bandScope === "all" ? p.bands : 0);
    segs.setAlpha(p.pastAlpha);
    // The shadow takes the *live* leg's ring count, not the frozen ones' — it is
    // the leg you would inherit, so `bandScope` has nothing to say about it, and
    // the envelope is half of what the pending anchor is worth looking at: where
    // the spread would sit once measured from there rather than from here.
    segs.setShadow(d.shadow, p.bands);
    segs.setData(d.past);
    // The pending flag rides on `shadow`, not on `flags`: it marks a thing that
    // has not happened, and turning the confirmed flags off is not a statement
    // about whether you want to see what is coming.
    flags.setData(p.flags === "off" ? [] : d.pivots, p.flags === "labels", d.pending);
    onData?.(d);
  };

  return {
    redraw: render,
    attachOverlays() {
      // Frozen segments first, so the flags — and the live line's own series —
      // sit over them rather than under.
      candle.attachPrimitive(segs as any);
      candle.attachPrimitive(flags as any);
    },
    setVisible(on) {
      if (on === shown) return;
      shown = on;
      line.applyOptions({ visible: on });
      // The rings and the wash follow `bands` as well as the row, so the redraw
      // below is what actually sets them — hiding here only covers the case
      // where it draws nothing at all.
      if (!on) {
        for (const r of rings) r.series.applyOptions({ visible: false });
        wash.setVisible(false);
      }
      segs.setVisible(on);
      flags.setVisible(on);
      render();
    },
    relight() {
      const h = hue();
      segs.setInk(h.bull, h.bear);
      flags.setInk(h.bull, h.bear);
      // The live line's colour, the rings' and the wash's all ride on the leg's
      // direction, so they come back with the redraw this ends with.
      render();
    },
    last: () => last,
    tail: (n) => (n >= tailPts.length ? tailPts.slice() : tailPts.slice(-n)),
  };
}
