// Draws level-interaction events over the candles as a series primitive:
//
//   - touch  -> a dot at (time, zone price), coloured by outcome (reject green /
//               accept red / chop grey); a ring when the zone stacks 2+ sources.
//   - VA-snap -> a triangle at (time, price) pointing the way the value boundary
//               jumped, plus a short horizontal tick at that price. This is the
//               "value crossed price" event, distinct from a touch.
//
// Same shape as VolumeProfilePrimitive / MarkerPrimitive: data + visibility are
// mutable and a change asks the chart for a repaint (a primitive has no native
// visible flag, so each layer culls itself in draw()). Times are UTC epoch
// seconds — the same axis the chart's bars use.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { palette } from "../../theme";
import type { Touch, VaSnap } from "../../lib/interactionTypes";

const OUTCOME_COLOR: Record<string, string> = {
  reject: palette.green,
  accept: palette.red,
  chop: palette.muted,
  unknown: palette.muted,
};

class InteractionRenderer {
  constructor(
    private host: InteractionPrimitive,
    private chart: () => IChartApi,
    private series: () => ISeriesApi<"Candlestick">,
  ) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.chart().timeScale();
      const series = this.series();

      if (this.host.showTouches) {
        for (const t of this.host.touches) {
          const x = ts.timeToCoordinate(t.ts as Time);
          const y = series.priceToCoordinate(t.zone_px);
          if (x == null || y == null) continue;
          ctx.beginPath();
          ctx.arc(x, y, 4, 0, Math.PI * 2);
          ctx.fillStyle = OUTCOME_COLOR[t.outcome] ?? palette.muted;
          ctx.fill();
          if (t.n_sources > 1) {
            ctx.beginPath();
            ctx.arc(x, y, 6.5, 0, Math.PI * 2);
            ctx.strokeStyle = palette.gold ?? "#e0a52a";
            ctx.lineWidth = 1.5;
            ctx.stroke();
          }
        }
      }

      if (this.host.showSnaps) {
        for (const s of this.host.va_snaps) {
          const x = ts.timeToCoordinate(s.ts as Time);
          const y = series.priceToCoordinate(s.px);
          if (x == null || y == null) continue;
          const up = s.snap_dir === "up_over_price";
          // Up-over-price caps a rally (bearish); down-under supports (bullish).
          ctx.fillStyle = ctx.strokeStyle = up ? palette.red : palette.green;
          // short horizontal tick at the level
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(x - 7, y);
          ctx.lineTo(x + 7, y);
          ctx.stroke();
          // triangle pointing the way the boundary jumped
          const h = 7;
          const apexY = up ? y - 10 : y + 10;
          const baseY = up ? y - 3 : y + 3;
          ctx.beginPath();
          ctx.moveTo(x, apexY);
          ctx.lineTo(x - h / 2, baseY);
          ctx.lineTo(x + h / 2, baseY);
          ctx.closePath();
          ctx.fill();
        }
      }
    });
  }
}

class InteractionPaneView {
  private _renderer: InteractionRenderer;
  constructor(
    host: InteractionPrimitive,
    chart: () => IChartApi,
    series: () => ISeriesApi<"Candlestick">,
  ) {
    this._renderer = new InteractionRenderer(host, chart, series);
  }
  update() {}
  renderer() {
    return this._renderer;
  }
  // Above the candles: these are the subject of this view, not background.
  zOrder() {
    return "top" as const;
  }
}

export class InteractionPrimitive {
  private chart!: IChartApi;
  private series!: ISeriesApi<"Candlestick">;
  private views: InteractionPaneView[] = [];
  private requestUpdate?: () => void;
  showTouches = true;
  showSnaps = true;

  constructor(
    public touches: Touch[],
    public va_snaps: VaSnap[],
  ) {}

  setData(touches: Touch[], va_snaps: VaSnap[]) {
    this.touches = touches;
    this.va_snaps = va_snaps;
    this.requestUpdate?.();
  }

  setVisibility(touches: boolean, snaps: boolean) {
    this.showTouches = touches;
    this.showSnaps = snaps;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.views = [new InteractionPaneView(this, () => this.chart, () => this.series)];
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
