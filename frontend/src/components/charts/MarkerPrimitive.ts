// Draws fill (Buy/Sell) arrows and MAE/MFE circles as a series primitive.
//
// We don't use the built-in series.setMarkers(): attaching a custom series
// primitive (the trade rectangle) suppresses the built-in markers layer in
// lightweight-charts v4, so the arrows silently vanish. Drawing them through
// the same primitive/canvas path that the rectangle uses guarantees they
// render. Markers position relative to each bar's high/low (above/below).

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { Bar, ChartMarker } from "../../lib/chartTypes";

class MarkerRenderer {
  constructor(
    private markers: ChartMarker[],
    private barMap: Map<number, Bar>,
    private chart: IChartApi,
    private series: ISeriesApi<"Candlestick">,
  ) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.chart.timeScale();
      for (const m of this.markers) {
        const x = ts.timeToCoordinate(m.time as Time);
        const bar = this.barMap.get(m.time);
        if (x == null || !bar) continue;
        const above = m.position === "aboveBar";
        const yRef = this.series.priceToCoordinate(above ? bar.high : bar.low);
        if (yRef == null) continue;

        ctx.fillStyle = m.color;
        ctx.strokeStyle = m.color;

        if (m.shape === "circle") {
          const r = 5;
          const cy = above ? yRef - 11 : yRef + 11;
          ctx.beginPath();
          ctx.arc(x, cy, r, 0, Math.PI * 2);
          ctx.fill();
          if (m.text) {
            ctx.font = "10px Inter, sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = above ? "bottom" : "top";
            ctx.fillText(m.text, x, above ? cy - r - 1 : cy + r + 1);
          }
        } else {
          const s = 5;
          const gap = 4;
          ctx.beginPath();
          if (m.shape === "arrowUp") {
            // Buy: apex points up, sits just below the bar low.
            const apex = yRef + gap;
            ctx.moveTo(x, apex);
            ctx.lineTo(x - s, apex + s * 1.8);
            ctx.lineTo(x + s, apex + s * 1.8);
          } else {
            // Sell (arrowDown): apex points down, sits just above the bar high.
            const apex = yRef - gap;
            ctx.moveTo(x, apex);
            ctx.lineTo(x - s, apex - s * 1.8);
            ctx.lineTo(x + s, apex - s * 1.8);
          }
          ctx.closePath();
          ctx.fill();
        }
      }
    });
  }
}

class MarkerPaneView {
  private _renderer: MarkerRenderer;
  constructor(
    markers: ChartMarker[],
    barMap: Map<number, Bar>,
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
  ) {
    this._renderer = new MarkerRenderer(markers, barMap, chart, series);
  }
  update() {}
  renderer() {
    return this._renderer;
  }
  zOrder() {
    return "top" as const;
  }
}

export class MarkerPrimitive {
  private chart!: IChartApi;
  private series!: ISeriesApi<"Candlestick">;
  private views: MarkerPaneView[] = [];
  private requestUpdate?: () => void;

  constructor(
    private markers: ChartMarker[],
    private barMap: Map<number, Bar>,
  ) {}

  attached(param: any) {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.views = [new MarkerPaneView(this.markers, this.barMap, this.chart, this.series)];
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
