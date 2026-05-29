// Port of the streamlit-lightweight-charts-pro TradeVisualizationOptions
// "rectangles" style, which is NOT in vanilla npm lightweight-charts. Draws a
// filled rectangle spanning [entry_time, exit_time] x [entry_price, exit_price],
// BLUE for a profitable trade / ORANGE for a loss at 0.12 fill opacity, and
// labels it with the trade's net_pnl IN DOLLARS (not a price-point delta).

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { TradeRect } from "../../lib/chartTypes";

const BLUE = "59, 130, 246";
const ORANGE = "249, 115, 22";

class RectRenderer {
  constructor(
    private rect: TradeRect,
    private chart: IChartApi,
    private series: ISeriesApi<"Candlestick">,
  ) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ts = this.chart.timeScale();
      const x1 = ts.timeToCoordinate(this.rect.entry_time as Time);
      const x2 = ts.timeToCoordinate(this.rect.exit_time as Time);
      const y1 = this.series.priceToCoordinate(this.rect.entry_price);
      const y2 = this.series.priceToCoordinate(this.rect.exit_price);
      if (x1 == null || x2 == null || y1 == null || y2 == null) return;

      const ctx: CanvasRenderingContext2D = scope.context;
      const left = Math.min(x1, x2);
      const right = Math.max(x1, x2);
      const top = Math.min(y1, y2);
      const bottom = Math.max(y1, y2);
      const w = Math.max(right - left, 1);
      const h = Math.max(bottom - top, 1);
      const rgb = this.rect.profitable ? BLUE : ORANGE;

      ctx.fillStyle = `rgba(${rgb}, 0.12)`;
      ctx.fillRect(left, top, w, h);
      ctx.strokeStyle = `rgba(${rgb}, 0.9)`;
      ctx.lineWidth = 1;
      ctx.strokeRect(left, top, w, h);

      const label = `${this.rect.net_pnl >= 0 ? "+" : ""}$${this.rect.net_pnl.toLocaleString(
        "en-US",
        { minimumFractionDigits: 2, maximumFractionDigits: 2 },
      )}`;
      ctx.font = "11px Inter, sans-serif";
      ctx.fillStyle = `rgba(${rgb}, 1)`;
      ctx.textBaseline = "bottom";
      ctx.fillText(label, left + 3, top - 2);
    });
  }
}

class RectPaneView {
  private _renderer: RectRenderer;
  constructor(rect: TradeRect, chart: IChartApi, series: ISeriesApi<"Candlestick">) {
    this._renderer = new RectRenderer(rect, chart, series);
  }
  update() {}
  renderer() {
    return this._renderer;
  }
  zOrder() {
    return "top" as const;
  }
}

export class TradeRectanglePrimitive {
  private chart!: IChartApi;
  private series!: ISeriesApi<"Candlestick">;
  private views: RectPaneView[] = [];
  private requestUpdate?: () => void;

  constructor(private rects: TradeRect[]) {}

  attached(param: any) {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.views = this.rects.map((r) => new RectPaneView(r, this.chart, this.series));
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
