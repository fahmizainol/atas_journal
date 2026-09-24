// The external-period candles, on the canvas (see lib/externalChart for what
// they are and how the drawn bars get grouped into them).
//
// Two rectangles per bucket: the high/low box in the surface's grid hue, and the
// open/close body outlined in blue or red. Hollow by default, which is the whole
// design — the layer's job is to frame the candles underneath it, and anything
// that fills the frame buries what it was framing.
//
// Two views rather than one, for the same reason DevelopingProfilePrimitive has
// two: "above the chart" is a z-order, lightweight-charts fixes a view's z-order
// when it builds the view, and this layer's is a setting that can be flipped
// while you watch. So both exist permanently and each draws only when the
// setting names it — the alternative is a primitive that has to be detached and
// rebuilt to change one checkbox.

import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { ink } from "../../theme";
import type { ExternalBar, ExternalChartParams } from "../../lib/externalChart";

/** Line weight of the body outline, and of the high/low box round it. The box is
 *  thinner on purpose: it is the extent, the body is the direction, and at equal
 *  weight the eye reads the outer rectangle first. */
const BODY_W = 1.5;
const RANGE_W = 1;

/** How far past the first and last bar of a bucket its box is drawn, as a share
 *  of one bar's width. Half a bar at each end, so consecutive boxes meet at the
 *  boundary between them instead of leaving a gap the width of a candle — the
 *  overlay has to read as a continuous series of bars, which is what it is. */
const EDGE = 0.5;

/** Below this many pixels a box is not a bar, it is a smudge. Happens when the
 *  chart is zoomed far out and a whole external period lands inside two pixels;
 *  drawing them anyway produces a solid band of outlines across the pane that
 *  says nothing. Skipped rather than merged — the legend already says which
 *  period is on, so a reader who has zoomed past it can see why it went. */
const MIN_W = 6;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  bars: () => readonly ExternalBar[];
  params: () => ExternalChartParams;
  visible: () => boolean;
}

class Renderer {
  constructor(
    private c: Ctx,
    private z: "bottom" | "top",
  ) {}

  draw(target: any) {
    const p = this.c.params();
    if (!this.c.visible()) return;
    // The other view's turn.
    if ((p.above ? "top" : "bottom") !== this.z) return;
    const bars = this.c.bars();
    if (!bars.length) return;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const series = this.c.series;
      const paneW = scope.mediaSize.width;

      const ec = ink().externalChart;
      const bull = p.palette === "custom" ? p.bull : ec.bull;
      const bear = p.palette === "custom" ? p.bear : ec.bear;

      // Half a bar, in pixels. `barSpacing` is the distance between bar centres,
      // so this is what turns "the first and last bar in the bucket" into "the
      // span the bucket occupies".
      const pad = Math.max(0, ts.options().barSpacing * EDGE);

      ctx.save();
      ctx.lineJoin = "miter";
      for (const b of bars) {
        const xa = ts.timeToCoordinate(b.from as any);
        const xb = ts.timeToCoordinate(b.to as any);
        if (xa == null || xb == null) continue;
        const x0 = xa - pad;
        const x1 = xb + pad;
        if (x1 < 0 || x0 > paneW) continue; // off-screen
        const w = x1 - x0;
        if (w < MIN_W) continue;

        const yHigh = series.priceToCoordinate(b.high);
        const yLow = series.priceToCoordinate(b.low);
        const yOpen = series.priceToCoordinate(b.open);
        const yClose = series.priceToCoordinate(b.close);
        if (yHigh == null || yLow == null || yOpen == null || yClose == null) continue;

        const up = b.close >= b.open;
        const stroke = up ? bull : bear;

        // The extent first, so the body's outline draws over it where they touch
        // (a bucket that closed on its high has the two rectangles sharing an
        // edge, and the direction is the one worth seeing there).
        if (p.grid) {
          ctx.strokeStyle = ec.range;
          ctx.lineWidth = RANGE_W;
          ctx.setLineDash([]);
          ctx.strokeRect(
            Math.round(x0) + 0.5,
            Math.round(yHigh) + 0.5,
            Math.round(w),
            Math.max(1, Math.round(yLow - yHigh)),
          );
        }

        const top = Math.min(yOpen, yClose);
        const h = Math.max(1, Math.abs(yClose - yOpen));
        if (p.fill) {
          ctx.fillStyle = up ? ec.fillBull : ec.fillBear;
          ctx.fillRect(Math.round(x0), Math.round(top), Math.round(w), Math.round(h));
        }
        ctx.strokeStyle = stroke;
        ctx.lineWidth = BODY_W;
        // The open bucket dashed — it is not a closed external bar yet, and the
        // box will still move.
        ctx.setLineDash(b.live ? [4, 3] : []);
        ctx.strokeRect(Math.round(x0) + 0.5, Math.round(top) + 0.5, Math.round(w), Math.round(h));
      }
      ctx.restore();
    });
  }
}

class View {
  private _r: Renderer;
  constructor(c: Ctx, private _z: "bottom" | "top") {
    this._r = new Renderer(c, _z);
  }
  update() {}
  renderer() {
    return this._r;
  }
  zOrder() {
    return this._z;
  }
}

export class ExternalChartPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _bars: readonly ExternalBar[] = [];
  private _params: ExternalChartParams | null = null;
  private _visible = false;

  /** The grouped buckets, as `lib/externalChart.groupExternalBars` returns them.
   *  Empty is a legitimate answer (the external period didn't group), and draws
   *  nothing. */
  setBars(bars: readonly ExternalBar[]) {
    this._bars = bars;
    this.requestUpdate?.();
  }

  setParams(p: ExternalChartParams) {
    this._params = p;
    this.requestUpdate?.();
  }

  setVisible(on: boolean) {
    if (on === this._visible) return;
    this._visible = on;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      bars: () => this._bars,
      // Never null by the time anything draws — the host sets params on the same
      // pass it sets visibility — but the layer stays off until it has them
      // rather than inventing a period to draw at.
      params: () => this._params!,
      visible: () => this._visible && this._params != null,
    };
    this.views = [new View(ctx, "bottom"), new View(ctx, "top")];
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
