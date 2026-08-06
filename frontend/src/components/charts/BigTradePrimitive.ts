// Big trades on the replay chart: one bubble per sweep, at the price it worked
// to, sized by the lots it took.
//
// The tape is 300-400k prints a session and almost all of it is one and two
// lots. What is worth seeing is the handful of orders that had to be worked
// through the book — so the mark is scaled by size (area ∝ lots) and the
// aggressor is the hue: blue paid up, red hit the bid. Flat discs, no outline:
// the ring was reading as a level of its own, and size is a blob on the tape,
// not a shape drawn around a price.
//
// The bubble sits on the *bar*, not on a sub-bar offset. With tick bars there is
// no time span to place a print inside — the bars fall wherever the tape decided
// — and everything else on this chart (fills, the IB, the profiles) is pinned to
// the bar grid for the same reason. What the mark says precisely is the price;
// when it happened, it says to the bar.
//
// A series primitive rather than the library's markers: markers snap above or
// below the bar, and the price a sweep filled at is the whole point of drawing
// it. (This chart's other custom primitives suppress the built-in marker layer
// anyway — see TradesPrimitive.)

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { BigTrade } from "../../lib/replayEngine";
import { palette } from "../../theme";

/** Aggressor hues, as "r, g, b" so the renderer can compose its own alpha.
 *  Blue bought, orange sold — the two the candles never use, so a disc never
 *  disappears into the body it is sitting on. */
const BUY_RGB = "59, 130, 246"; // palette.blue
const SELL_RGB = "249, 115, 22"; // palette.orange
/** Solid enough to read as a disc on its own, translucent enough that the candle
 *  it sits on still shows through — the mark is where size traded, not a lid
 *  over the price action there. */
const FILL_ALPHA = 0.55;

/** Radius in px. Square-rooted so the *area* tracks the lots, and measured from
 *  the threshold rather than from zero — every mark on screen is over it, so the
 *  scale is spent on the range that actually varies. */
const R_MIN = 4;
const R_MAX = 24;
const R_GAIN = 1.55;
const radiusFor = (lots: number, min: number): number =>
  Math.min(R_MAX, R_MIN + R_GAIN * Math.sqrt(Math.max(1, lots - min + 1)));

/** Only the outliers get their size written next to them: at a 50-lot threshold
 *  every bubble is "big", and a number on each one is a wall of text over the
 *  candles. Twice the threshold is the one worth naming. */
const LABEL_FACTOR = 2;

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const overlaps = (a: Box, b: Box): boolean =>
  a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  trades: () => BigTrade[];
  minLots: () => number;
  visible: () => boolean;
}

class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const trades = this.c.trades();
    if (!this.c.visible() || !trades.length) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const series = this.c.series;
      const min = this.c.minLots();

      // Biggest first so it lands underneath. Around the bell the sweeps stack
      // on a handful of ticks, and a 300-lot circle drawn last would swallow
      // every smaller one inside it.
      const ordered = [...trades].sort((a, b) => b.lots - a.lots);
      const drawn: { x: number; y: number; r: number; t: BigTrade }[] = [];
      for (const t of ordered) {
        const x = ts.timeToCoordinate(t.time as Time);
        const y = series.priceToCoordinate(t.price);
        // A bar the chart hasn't loaded, or a price off the current scale —
        // nothing is clamped onto an edge, same rule the trade marks follow.
        if (x == null || y == null) continue;
        const r = radiusFor(t.lots, min);
        const rgb = t.buy ? BUY_RGB : SELL_RGB;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${rgb}, ${FILL_ALPHA})`;
        ctx.fill();
        drawn.push({ x, y, r, t });
      }

      // Labels last, still largest-first, dropping any that would land on one
      // already placed.
      ctx.save();
      ctx.font = "600 10px Inter, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      const placed: Box[] = [];
      for (const d of drawn) {
        if (d.t.lots < min * LABEL_FACTOR) continue;
        const text = d.t.fills > 1 ? `${d.t.lots}×${d.t.fills}` : String(d.t.lots);
        const w = ctx.measureText(text).width;
        const box = { x0: d.x + d.r + 3, y0: d.y - 7, x1: d.x + d.r + 5 + w, y1: d.y + 7 };
        if (placed.some((p) => overlaps(box, p))) continue;
        placed.push(box);
        // Halo, so a number that lands over another bubble stays legible.
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(14, 17, 23, 0.85)";
        ctx.strokeText(text, box.x0 + 1, d.y);
        ctx.fillStyle = d.t.buy ? palette.blue : palette.orange;
        ctx.fillText(text, box.x0 + 1, d.y);
      }
      ctx.restore();
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
    // Above the candles and the bands, below the open position and the working
    // orders: the tape's history should never cover what you are doing now.
    return "normal" as const;
  }
}

export class BigTradePrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _trades: BigTrade[] = [];
  private _minLots = 1;
  private _visible = true;

  /** The full list as of the clock. A rewind hands back the shorter one; a
   *  playback step hands back the same list with its tail grown. */
  setTrades(trades: BigTrade[], minLots?: number) {
    this._trades = trades;
    if (minLots) this._minLots = minLots;
    this.requestUpdate?.();
  }

  setMinLots(lots: number) {
    if (lots === this._minLots) return;
    this._minLots = lots;
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
      trades: () => this._trades,
      minLots: () => this._minLots,
      visible: () => this._visible,
    };
    this.views = [new View(new Renderer(ctx))];
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
