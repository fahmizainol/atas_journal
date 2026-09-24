// The higher-timeframe trend on the canvas (see lib/htfTrend for the rule).
//
// Two views, because the two halves belong at different depths: the wash is a
// background and goes under the candles, the EMA lines are levels and go over
// them — a 5m EMA20 hidden behind a wall of 30-second bodies is exactly the
// level this layer exists to put in front of you.
//
// Both walk only the visible logical range. The per-bar arrays are as long as
// the session, and a pan across a full day is thousands of bars, but at most a
// few hundred are ever on screen.

import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { ink } from "../../theme";
import type { Bar } from "../../lib/chartTypes";
import type { HtfTrend, HtfTrendParams } from "../../lib/htfTrend";

/** Strip height when the tint is a ribbon rather than a wash. */
const RIBBON_H = 5;

/** The frames are told apart by dash, not hue: they are one idea at three
 *  scales, and three new colours on a chart that already carries four VWAPs
 *  would read as three more indicators. Finest frame solid. */
const DASHES: number[][] = [[], [6, 4], [2, 3]];
const LINE_W = 1.5;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  bars: () => readonly Bar[];
  trend: () => HtfTrend | null;
  params: () => HtfTrendParams | null;
  visible: () => boolean;
}

function visibleSpan(c: Ctx, n: number): [number, number] {
  const r = c.chart.timeScale().getVisibleLogicalRange();
  if (!r) return [0, n - 1];
  return [Math.max(0, Math.floor(r.from) - 1), Math.min(n - 1, Math.ceil(r.to) + 1)];
}

class WashRenderer {
  constructor(private c: Ctx) {}
  draw(target: any) {
    const p = this.c.params();
    const t = this.c.trend();
    if (!this.c.visible() || !p || !t || p.tint === "off") return;
    const bars = this.c.bars();
    const n = Math.min(bars.length, t.combined.length);
    if (!n) return;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const h = scope.mediaSize.height;
      const half = ts.options().barSpacing / 2;
      const hi = ink().htfTrend;
      const y0 = p.tint === "ribbon" ? h - RIBBON_H : 0;
      const hh = p.tint === "ribbon" ? RIBBON_H : h;
      const [a, b] = visibleSpan(this.c, n);

      // Runs of one state become one rectangle — a wash is mostly long runs, and
      // a rect per bar would seam at fractional bar widths.
      let runState = 0;
      let runX0 = 0;
      let runX1 = 0;
      const flush = () => {
        if (runState === 0) return;
        ctx.fillStyle = runState > 0 ? (p.tint === "ribbon" ? hi.ribbonUp : hi.washUp) : p.tint === "ribbon" ? hi.ribbonDown : hi.washDown;
        ctx.fillRect(runX0, y0, Math.max(1, runX1 - runX0), hh);
      };
      for (let i = a; i <= b; i++) {
        const x = ts.timeToCoordinate(bars[i].time as any);
        if (x == null) continue;
        const s = t.combined[i];
        if (s !== runState) {
          flush();
          runState = s;
          runX0 = x - half;
        }
        runX1 = x + half;
      }
      flush();
    });
  }
}

class LineRenderer {
  constructor(private c: Ctx) {}
  draw(target: any) {
    const p = this.c.params();
    const t = this.c.trend();
    if (!this.c.visible() || !p || !t || !p.lines) return;
    const bars = this.c.bars();
    if (!bars.length) return;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const series = this.c.series;
      const half = ts.options().barSpacing / 2;
      const hi = ink().htfTrend;

      ctx.save();
      ctx.lineWidth = LINE_W;
      ctx.strokeStyle = hi.line;
      t.frames.forEach((f, fi) => {
        // The bar list grows in place between repaints; read only what was computed.
        const [a, b] = visibleSpan(this.c, Math.min(bars.length, f.ema.length));
        ctx.setLineDash(DASHES[Math.min(fi, DASHES.length - 1)]);
        ctx.beginPath();
        let open = false;
        let prevY: number | null = null;
        for (let i = a; i <= b; i++) {
          const v = f.ema[i];
          const x = ts.timeToCoordinate(bars[i].time as any);
          if (v == null || x == null) {
            open = false;
            prevY = null;
            continue;
          }
          const y = series.priceToCoordinate(v);
          if (y == null) {
            open = false;
            continue;
          }
          // Stepped: the value holds across the frame bar, then jumps — it is a
          // closed-bar reading, and a diagonal would claim a value in between.
          if (!open) {
            ctx.moveTo(x - half, y);
            open = true;
          } else if (prevY !== y) {
            ctx.lineTo(x - half, prevY!);
            ctx.lineTo(x - half, y);
          }
          ctx.lineTo(x + half, y);
          prevY = y;
        }
        ctx.stroke();
      });
      ctx.restore();
    });
  }
}

class View {
  constructor(
    private _r: WashRenderer | LineRenderer,
    private _z: "bottom" | "top",
  ) {}
  update() {}
  renderer() {
    return this._r;
  }
  zOrder() {
    return this._z;
  }
}

export class HtfTrendPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _bars: readonly Bar[] = [];
  private _trend: HtfTrend | null = null;
  private _params: HtfTrendParams | null = null;
  private _visible = false;

  /** The bars the trend was computed over, and the result — set together so the
   *  per-bar arrays can never be read against a different bar list. */
  setData(bars: readonly Bar[], trend: HtfTrend) {
    this._bars = bars;
    this._trend = trend;
    this.requestUpdate?.();
  }

  setParams(p: HtfTrendParams) {
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
      trend: () => this._trend,
      params: () => this._params,
      visible: () => this._visible,
    };
    this.views = [new View(new WashRenderer(ctx), "bottom"), new View(new LineRenderer(ctx), "top")];
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
