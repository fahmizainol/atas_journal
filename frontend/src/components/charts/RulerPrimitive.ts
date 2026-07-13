// TV-style ruler / measure tool: drag from one point to another and read off the
// move between them — price distance (points / ticks / %), what it would pay per
// contract, and how long it took (bars / wall time). One measurement at a time;
// the drag mechanics live in CandlestickChart, this only draws.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { palette } from "../../theme";

export interface RulerData {
  /** Bar times (snapped onto the bar grid) of the two corners. */
  t1: number;
  t2: number;
  /** Prices of the two corners — p1 is the anchor, p2 follows the pointer. */
  p1: number;
  p2: number;
  /** Whole bars between the corners. */
  bars: number;
  /** Wall-clock seconds between the corners' bar times. */
  seconds: number;
}

const UP_FILL = "rgba(33, 192, 122, 0.14)";
const DOWN_FILL = "rgba(245, 69, 95, 0.14)";
const ARROW = 5;

const fmtDur = (s: number): string => {
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.round(s)}s`;
};

// Decimals to print prices with, derived from the tick size (0.25 -> 2).
const decimalsOf = (tick: number | undefined): number => {
  if (!tick || tick >= 1) return 2;
  return Math.min(6, Math.ceil(-Math.log10(tick)));
};

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: () => RulerData | null;
}

class Renderer {
  constructor(
    private c: Ctx,
    private tickSize?: number,
    private pointValue?: number,
  ) {}

  draw(target: any) {
    const d = this.c.data();
    if (!d) return;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const xa = ts.timeToCoordinate(d.t1 as Time);
      const xb = ts.timeToCoordinate(d.t2 as Time);
      const ya = this.c.series.priceToCoordinate(d.p1);
      const yb = this.c.series.priceToCoordinate(d.p2);
      if (xa == null || xb == null || ya == null || yb == null) return;

      const up = d.p2 >= d.p1;
      const color = up ? palette.green : palette.red;
      const x1 = Math.min(xa, xb);
      const x2 = Math.max(xa, xb);
      const y1 = Math.min(ya, yb);
      const y2 = Math.max(ya, yb);

      ctx.fillStyle = up ? UP_FILL : DOWN_FILL;
      ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(x1 + 0.5, y1 + 0.5, x2 - x1 - 1, y2 - y1 - 1);
      ctx.restore();

      // Direction arrows through the middle of the box: vertical for the price
      // leg (anchor -> pointer), horizontal for the time leg (always rightward
      // visually — the numbers carry the sign).
      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1.5;

      ctx.beginPath();
      ctx.moveTo(mx, ya);
      ctx.lineTo(mx, yb);
      ctx.stroke();
      if (Math.abs(yb - ya) > ARROW * 2) {
        const dir = yb > ya ? 1 : -1;
        ctx.beginPath();
        ctx.moveTo(mx, yb);
        ctx.lineTo(mx - ARROW, yb - dir * ARROW * 1.6);
        ctx.lineTo(mx + ARROW, yb - dir * ARROW * 1.6);
        ctx.closePath();
        ctx.fill();
      }

      ctx.beginPath();
      ctx.moveTo(xa, my);
      ctx.lineTo(xb, my);
      ctx.stroke();
      if (Math.abs(xb - xa) > ARROW * 2) {
        const dir = xb > xa ? 1 : -1;
        ctx.beginPath();
        ctx.moveTo(xb, my);
        ctx.lineTo(xb - dir * ARROW * 1.6, my - ARROW);
        ctx.lineTo(xb - dir * ARROW * 1.6, my + ARROW);
        ctx.closePath();
        ctx.fill();
      }

      // The readout chip: above the box for an up move, below for a down move
      // (like TV), clamped back inside the pane so it never clips away.
      const dp = d.p2 - d.p1;
      const dec = decimalsOf(this.tickSize);
      const sign = dp >= 0 ? "+" : "−";
      const pct = d.p1 !== 0 ? (dp / d.p1) * 100 : 0;
      const lines: { text: string; color: string }[] = [];

      let l1 = `${sign}${Math.abs(dp).toFixed(dec)} pts (${sign}${Math.abs(pct).toFixed(2)}%)`;
      if (this.tickSize) {
        const ticks = Math.abs(dp) / this.tickSize;
        l1 += ` · ${sign}${ticks.toFixed(ticks % 1 ? 1 : 0)} ticks`;
      }
      lines.push({ text: l1, color });

      if (this.pointValue) {
        const usd = Math.abs(dp) * this.pointValue;
        lines.push({
          text: `${sign}$${usd.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })} / lot`,
          color,
        });
      }
      lines.push({ text: `${d.bars} bar${d.bars === 1 ? "" : "s"} · ${fmtDur(d.seconds)}`, color: palette.muted });

      ctx.font = "500 11px Inter, sans-serif";
      ctx.textBaseline = "middle";
      const LH = 16;
      const PAD = 8;
      const w = Math.max(...lines.map((l) => ctx.measureText(l.text).width)) + PAD * 2;
      const h = lines.length * LH + PAD;
      let bx = mx - w / 2;
      bx = Math.min(scope.mediaSize.width - w - 4, Math.max(4, bx));
      let by = up ? y1 - h - 8 : y2 + 8;
      by = Math.min(scope.mediaSize.height - h - 4, Math.max(4, by));

      ctx.fillStyle = "rgba(14, 17, 23, 0.92)";
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(bx, by, w, h, 5);
      ctx.fill();
      ctx.stroke();
      lines.forEach((l, i) => {
        ctx.fillStyle = l.color;
        ctx.fillText(l.text, bx + PAD, by + PAD / 2 + LH * i + LH / 2);
      });
    });
  }
}

class View {
  constructor(private _r: Renderer) {}
  update() {}
  renderer() {
    return this._r;
  }
  zOrder() {
    return "top" as const;
  }
}

export class RulerPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _data: RulerData | null = null;

  constructor(
    private tickSize?: number,
    private pointValue?: number,
  ) {}

  // Called on every mousemove while measuring — only swaps data and repaints.
  setData(d: RulerData | null) {
    this._data = d;
    this.requestUpdate?.();
  }

  data(): RulerData | null {
    return this._data;
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      data: () => this._data,
    };
    this.views = [new View(new Renderer(ctx, this.tickSize, this.pointValue))];
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
