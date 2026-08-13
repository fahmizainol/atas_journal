// Shades the region between the ±1σ and ±2σ lines of one anchored VWAP — the
// band fill that lightweight-charts has no native equivalent for (an Area
// series only fills to a baseline, not between two arbitrary lines). Two ribbons
// are drawn per anchor: upper1→upper2 and lower1→lower2. The mid-to-±1σ region
// is deliberately left clear so the mid line stays readable.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { VwapPoint } from "../../lib/chartTypes";
import { ink } from "../../theme";

/** One resolved column of a ribbon: where it sits and what colour it is in. */
interface Col {
  x: number;
  inner: number;
  outer: number;
  rgb: string;
}

class BandRenderer {
  constructor(
    // Read through an accessor, not a captured array: the Simulator's replay
    // grows its band a point per bar and swaps the array in as the tape plays,
    // so the renderer must always draw the current points, not the ones that
    // existed when the primitive was attached.
    private points: () => VwapPoint[],
    // An accessor for the same reason the points are one: the wash is re-cut
    // when the chart crosses between a light and a dark surface, and the
    // primitive is attached once for the life of the chart.
    private rgb: () => string,
    // Per-bar override of the above, when the anchor has something to say that
    // changes within a session (the Modern VWAP's regime read is the only one
    // today). Null for the four session anchors, whose wash is one colour.
    private tint: () => ((i: number) => string | undefined) | null,
    private alphaScale: number,
    private chart: IChartApi,
    private series: ISeriesApi<"Candlestick">,
    private visible: () => boolean,
  ) {}

  draw(target: any) {
    const points = this.points();
    if (!this.visible() || points.length < 2) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.chart.timeScale();

      // Resolve every point once; a null coordinate (off-screen time or a price
      // outside the visible scale) breaks the ribbon into a separate polygon
      // rather than closing across the gap.
      const base = this.rgb();
      const tint = this.tint();
      const cols: Col[][] = [[], []];
      for (let i = 0; i < points.length; i++) {
        const p = points[i];
        const rgb = tint?.(i) ?? base;
        // A non-finite point is a session-boundary break (see Interactions.tsx) —
        // split the ribbon here so the fill stops at the anchor's end rather than
        // washing across the gap to the next session.
        const x =
          Number.isFinite(p.upper1) &&
          Number.isFinite(p.upper2) &&
          Number.isFinite(p.lower1) &&
          Number.isFinite(p.lower2)
            ? ts.timeToCoordinate(p.time as Time)
            : null;
        if (x == null) {
          cols[0].push(null as any);
          cols[1].push(null as any);
          continue;
        }
        const yU1 = this.series.priceToCoordinate(p.upper1);
        const yU2 = this.series.priceToCoordinate(p.upper2);
        const yL1 = this.series.priceToCoordinate(p.lower1);
        const yL2 = this.series.priceToCoordinate(p.lower2);
        cols[0].push(yU1 == null || yU2 == null ? (null as any) : { x, inner: yU1, outer: yU2, rgb });
        cols[1].push(yL1 == null || yL2 == null ? (null as any) : { x, inner: yL1, outer: yL2, rgb });
      }

      const alpha = ink().bandAlpha * this.alphaScale;
      for (const ribbon of cols) {
        let run: Col[] = [];
        const flush = () => {
          if (run.length >= 2) {
            ctx.fillStyle = `rgba(${run[0].rgb}, ${alpha})`;
            ctx.beginPath();
            ctx.moveTo(run[0].x, run[0].inner);
            for (let i = 1; i < run.length; i++) ctx.lineTo(run[i].x, run[i].inner);
            for (let i = run.length - 1; i >= 0; i--) ctx.lineTo(run[i].x, run[i].outer);
            ctx.closePath();
            ctx.fill();
          }
          run = [];
        };
        for (const c of ribbon) {
          if (!c) {
            flush();
            continue;
          }
          // A colour change closes the polygon and opens the next one *on the
          // same column*, so the two abut on a shared edge: start it at `c`
          // instead and the quad between the two columns would go unfilled — a
          // one-bar hole in the wash at every regime change.
          const prev = run[run.length - 1];
          if (prev && prev.rgb !== c.rgb) {
            flush();
            run.push(prev);
          }
          run.push(c);
        }
        flush();
      }
    });
  }
}

class BandPaneView {
  private _renderer: BandRenderer;
  constructor(
    points: () => VwapPoint[],
    rgb: () => string,
    tint: () => ((i: number) => string | undefined) | null,
    alphaScale: number,
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
    visible: () => boolean,
  ) {
    this._renderer = new BandRenderer(points, rgb, tint, alphaScale, chart, series, visible);
  }
  update() {}
  renderer() {
    return this._renderer;
  }
  // Under the candles and the VWAP lines — this is a background wash.
  zOrder() {
    return "bottom" as const;
  }
}

export class VwapBandPrimitive {
  private chart!: IChartApi;
  private series!: ISeriesApi<"Candlestick">;
  private views: BandPaneView[] = [];
  private requestUpdate?: () => void;
  private visible = true;
  private tint: ((i: number) => string | undefined) | null = null;

  constructor(
    private points: VwapPoint[],
    private rgb: string,
    /** Scales the surface's `bandAlpha`. 1 for the session anchors, whose ±1σ→±2σ
     *  region is a few dozen pixels tall. The Modern VWAP's is not: a swing
     *  anchor's σ runs several times a session anchor's, so the same alpha lays
     *  the same wash over a slab several times the area and the candles inside it
     *  stop being the thing you are looking at. Quieter per pixel, so the layer
     *  weighs about what the others do overall. */
    private alphaScale = 1,
  ) {}

  // Driven by the legend toggle alongside the anchor's line series. A primitive
  // has no `visible` option, so it culls itself in draw() and asks for a repaint.
  setVisible(v: boolean) {
    this.visible = v;
    this.requestUpdate?.();
  }

  // Replace the points and repaint. The journal charts set their band once at
  // build time; the Simulator calls this on every snapshot and playback step, so
  // the fill develops with the tape exactly like the σ lines it shades between.
  setPoints(points: VwapPoint[]) {
    this.points = points;
    this.requestUpdate?.();
  }

  /** Re-cut the wash — the light surfaces carry their own triplet per anchor
   *  (theme.ts). Paired with the anchor's line series being re-coloured, so the
   *  envelope and the fill under it never disagree about which cut is in force. */
  setRgb(rgb: string) {
    this.rgb = rgb;
    this.requestUpdate?.();
  }

  /** Colour the wash per bar, by index into the points last handed to
   *  `setPoints`. Set alongside those points, never on its own — an index into a
   *  stale array is a wash in the wrong colour. Null goes back to the flat
   *  `rgb`, which is what the four session anchors always use. */
  setTint(tint: ((i: number) => string | undefined) | null) {
    this.tint = tint;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.views = [
      new BandPaneView(
        () => this.points,
        () => this.rgb,
        () => this.tint,
        this.alphaScale,
        this.chart,
        this.series,
        () => this.visible,
      ),
    ];
    this.requestUpdate?.();
  }

  detached() {
    this.views = [];
  }

  updateAllViews() {
    this.views.forEach((v) => v.update());
  }

  paneViews() {
    return this.views;
  }
}
