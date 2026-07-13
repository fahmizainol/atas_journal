// Shades the region between the ±1σ and ±2σ lines of one anchored VWAP — the
// band fill that lightweight-charts has no native equivalent for (an Area
// series only fills to a baseline, not between two arbitrary lines). Two ribbons
// are drawn per anchor: upper1→upper2 and lower1→lower2. The mid-to-±1σ region
// is deliberately left clear so the mid line stays readable.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { VwapPoint } from "../../lib/chartTypes";

const FILL_ALPHA = 0.3; // 70% transparent

class BandRenderer {
  constructor(
    private points: VwapPoint[],
    private rgb: string,
    private chart: IChartApi,
    private series: ISeriesApi<"Candlestick">,
    private visible: () => boolean,
  ) {}

  draw(target: any) {
    if (!this.visible() || this.points.length < 2) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.chart.timeScale();

      // Resolve every point once; a null coordinate (off-screen time or a price
      // outside the visible scale) breaks the ribbon into a separate polygon
      // rather than closing across the gap.
      const cols: { x: number; inner: number; outer: number }[][] = [[], []];
      for (const p of this.points) {
        const x = ts.timeToCoordinate(p.time as Time);
        const yU1 = this.series.priceToCoordinate(p.upper1);
        const yU2 = this.series.priceToCoordinate(p.upper2);
        const yL1 = this.series.priceToCoordinate(p.lower1);
        const yL2 = this.series.priceToCoordinate(p.lower2);
        if (x == null) {
          cols[0].push(null as any);
          cols[1].push(null as any);
          continue;
        }
        cols[0].push(yU1 == null || yU2 == null ? (null as any) : { x, inner: yU1, outer: yU2 });
        cols[1].push(yL1 == null || yL2 == null ? (null as any) : { x, inner: yL1, outer: yL2 });
      }

      ctx.fillStyle = `rgba(${this.rgb}, ${FILL_ALPHA})`;
      for (const ribbon of cols) {
        let run: { x: number; inner: number; outer: number }[] = [];
        const flush = () => {
          if (run.length >= 2) {
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
          if (c) run.push(c);
          else flush();
        }
        flush();
      }
    });
  }
}

class BandPaneView {
  private _renderer: BandRenderer;
  constructor(
    points: VwapPoint[],
    rgb: string,
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
    visible: () => boolean,
  ) {
    this._renderer = new BandRenderer(points, rgb, chart, series, visible);
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

  constructor(
    private points: VwapPoint[],
    private rgb: string,
  ) {}

  // Driven by the legend toggle alongside the anchor's line series. A primitive
  // has no `visible` option, so it culls itself in draw() and asks for a repaint.
  setVisible(v: boolean) {
    this.visible = v;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.views = [
      new BandPaneView(this.points, this.rgb, this.chart, this.series, () => this.visible),
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
