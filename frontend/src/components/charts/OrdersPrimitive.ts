// Working orders: the orders that are placed but not filled yet, drawn the way a
// platform draws them — a line at the resting price with a chip carrying what it
// is and how far the market still has to come, plus the bracket it will attach
// on fill sketched in behind it.
//
// Not the P&L colours, deliberately: a working order is not a position, and the
// moment it fills the drawing hands over to PositionPrimitive in green or red.
// Nothing on this overlay is money at risk yet, so the hue says *what kind of
// order* instead — blue for a limit waiting on the passive side, amber for a
// stop waiting to be run through. Only the side word inside the chip wears green
// or red, because that is the one thing you must never misread.
//
// Every line here is draggable and every chip cancellable; as with the position
// overlay the pointer itself belongs to ReplayChart, so this publishes `hit(x,y)`
// against the boxes the last paint actually drew.

import type { ISeriesApi, ISeriesPrimitiveAxisView } from "lightweight-charts";
import { palette } from "../../theme";
import { byPointer } from "../../lib/pointer";

/** One order as the page knows it, with its levels resolved to prices. */
export interface WorkingOrderView {
  id: number;
  type: "limit" | "stop";
  side: "long" | "short";
  size: number;
  /** The limit, or the stop's trigger. */
  price: number;
  stop: number | null;
  target: number | null;
  /** The bracket this order carries would not take effect if it filled now —
   *  it adds to, or takes size off, a position whose own bracket stands. The
   *  legs are still here (a drag on the resting price carries them, and they
   *  come back the moment the order would open a position again), but nothing
   *  is drawn for them: a sketched-in stop that isn't coming is the chart
   *  making a promise the simulation won't keep. */
  inert?: boolean;
}

export type OrderLeg = "price" | "stop" | "target" | "cancel";
export interface OrderHit {
  id: number;
  leg: OrderLeg;
}

const LIMIT_LINE = palette.blue;
const STOP_LINE = palette.orange;
const lineOf = (type: "limit" | "stop") => (type === "stop" ? STOP_LINE : LIMIT_LINE);
const SL_LINE = "rgba(245, 69, 95, 0.55)";
const TP_LINE = "rgba(33, 192, 122, 0.55)";
const CHIP_BG = "rgba(14, 17, 23, 0.92)";

const CHIP_H = 18;
const PAD = 6;
const MARGIN = 6;
/** See PositionPrimitive — same sizes, same reason. */
const CLOSE_W = byPointer(18, 30);
const GRAB_PX = byPointer(5, 22);

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
interface Layout {
  chips: { hit: OrderHit; rect: Rect }[];
  lines: { hit: OrderHit; y: number }[];
}

const inside = (r: Rect, x: number, y: number) =>
  x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;

const decimalsOf = (tick: number): number =>
  !tick || tick >= 1 ? 2 : Math.min(6, Math.ceil(-Math.log10(tick)));

interface Ctx {
  series: ISeriesApi<"Candlestick">;
  orders: () => WorkingOrderView[];
  mark: () => number;
  tickSize: () => number;
  hover: () => OrderHit | null;
  setLayout: (l: Layout | null) => void;
}

class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const orders = this.c.orders();
    if (!orders.length) {
      this.c.setLayout(null);
      return;
    }
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const series = this.c.series;
      const W = scope.mediaSize.width;
      const hover = this.c.hover();
      const mark = this.c.mark();

      // No prices in the chips, as on the position overlay: every level drawn
      // here carries its own price-axis label, and printing it twice only makes
      // the chip long enough to cover the candles behind it.
      ctx.font = "600 10px Inter, sans-serif";
      ctx.textBaseline = "middle";

      const chips: { hit: OrderHit; rect: Rect }[] = [];
      const lines: { hit: OrderHit; y: number }[] = [];

      const hot = (h: OrderHit) => hover != null && hover.id === h.id && hover.leg === h.leg;

      const line = (y: number, color: string, dash: number[], h: OrderHit) => {
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = hot(h) ? 2 : 1;
        ctx.setLineDash(dash);
        ctx.beginPath();
        ctx.moveTo(0, Math.round(y) + 0.5);
        ctx.lineTo(W, Math.round(y) + 0.5);
        ctx.stroke();
        ctx.restore();
        lines.push({ hit: h, y });
      };

      // One chip = a right-aligned pill of coloured segments, hung off the price
      // scale — the same shape the position overlay uses, so a resting order and
      // a filled one read as the same family of thing.
      const chip = (
        y: number,
        segs: { text: string; color: string }[],
        border: string,
        closeFor: OrderHit | null,
      ): Rect => {
        const textW = segs.reduce((a, s) => a + ctx.measureText(s.text).width, 0);
        const w = textW + PAD * 2 + (segs.length - 1) * PAD + (closeFor ? CLOSE_W : 0);
        const r: Rect = { x: W - MARGIN - w, y: y - CHIP_H / 2, w, h: CHIP_H };
        ctx.fillStyle = CHIP_BG;
        ctx.strokeStyle = border;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(r.x, r.y, r.w, r.h, 4);
        ctx.fill();
        ctx.stroke();
        let tx = r.x + PAD;
        for (const s of segs) {
          ctx.fillStyle = s.color;
          ctx.fillText(s.text, tx, y);
          tx += ctx.measureText(s.text).width + PAD;
        }
        if (closeFor) {
          const cx = r.x + r.w - CLOSE_W / 2;
          ctx.save();
          ctx.strokeStyle = hot(closeFor) ? palette.red : palette.muted;
          ctx.lineWidth = hot(closeFor) ? 1.6 : 1.2;
          ctx.beginPath();
          ctx.moveTo(cx - 3.5, y - 3.5);
          ctx.lineTo(cx + 3.5, y + 3.5);
          ctx.moveTo(cx + 3.5, y - 3.5);
          ctx.lineTo(cx - 3.5, y + 3.5);
          ctx.stroke();
          ctx.restore();
          chips.push({
            hit: closeFor,
            rect: { x: r.x + r.w - CLOSE_W, y: r.y, w: CLOSE_W, h: r.h },
          });
        }
        return r;
      };

      for (const o of orders) {
        const yL = series.priceToCoordinate(o.price);
        if (yL == null) continue;
        // The attached bracket first, so the resting price draws over it.
        const leg = (price: number | null, color: string, label: string, key: OrderLeg) => {
          if (price == null) return;
          const y = series.priceToCoordinate(price);
          if (y == null) return;
          const h: OrderHit = { id: o.id, leg: key };
          line(y, color, [2, 3], h);
          const rect = chip(y, [{ text: label, color }], color, null);
          chips.push({ hit: h, rect });
        };
        if (!o.inert) {
          leg(o.target, TP_LINE, "TP", "target");
          leg(o.stop, SL_LINE, "SL", "stop");
        }

        const hRest: OrderHit = { id: o.id, leg: "price" };
        const hue = lineOf(o.type);
        // A stop is drawn in longer dashes than a limit, so the two still read
        // apart in a screenshot, or to an eye that doesn't separate the hues.
        line(yL, hue, o.type === "stop" ? [9, 5] : [6, 4], hRest);
        const away = Number.isFinite(mark) ? Math.abs(o.price - mark) : null;
        const rect = chip(
          yL,
          [
            {
              text: `${o.side === "long" ? "BUY" : "SELL"} ${
                o.type === "stop" ? "STP" : "LMT"
              } ×${o.size}`,
              color: o.side === "long" ? palette.green : palette.red,
            },
            ...(away != null ? [{ text: `${away.toFixed(2)} pts`, color: palette.muted }] : []),
          ],
          hue,
          { id: o.id, leg: "cancel" },
        );
        chips.push({ hit: hRest, rect });
      }

      this.c.setLayout({ chips, lines });
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

/** A price-axis label per level of a working order — the resting price and, when
 *  it has them, the two bracket legs. The legs used to be left unlabelled, on the
 *  grounds that a trade which hasn't happened shouldn't take three slots on the
 *  live scale; they get one now because the chips no longer carry the number, so
 *  this is the only place the level is readable at all. */
class AxisView implements ISeriesPrimitiveAxisView {
  constructor(
    private p: OrdersPrimitive,
    private id: number,
    private leg: "price" | "stop" | "target",
  ) {}
  private order(): WorkingOrderView | undefined {
    return this.p.orders().find((o) => o.id === this.id);
  }
  private price(): number | null {
    const o = this.order();
    if (!o) return null;
    if (this.leg === "price") return o.price;
    // Nothing is drawn for an inert bracket, so nothing labels it either.
    if (o.inert) return null;
    return (this.leg === "stop" ? o.stop : o.target) ?? null;
  }
  coordinate(): number {
    const price = this.price();
    if (price == null) return -100;
    return this.p.series?.priceToCoordinate(price) ?? -100;
  }
  text(): string {
    const price = this.price();
    return price != null ? price.toFixed(decimalsOf(this.p.tickSize())) : "";
  }
  textColor(): string {
    return "#ffffff";
  }
  backColor(): string {
    if (this.leg === "stop") return palette.red;
    if (this.leg === "target") return palette.green;
    const o = this.order();
    return o ? lineOf(o.type) : LIMIT_LINE;
  }
  visible(): boolean {
    return this.price() != null;
  }
}

const NO_AXIS_VIEWS: ISeriesPrimitiveAxisView[] = [];

export class OrdersPrimitive {
  series: ISeriesApi<"Candlestick"> | null = null;
  private views: View[] = [];
  private axisViews: ISeriesPrimitiveAxisView[] = [];
  private axisIds = "";
  private requestUpdate?: () => void;
  private _orders: WorkingOrderView[] = [];
  private _mark = NaN;
  private _tick = 0.25;
  private _hover: OrderHit | null = null;
  private _layout: Layout | null = null;

  setOrders(orders: WorkingOrderView[], tickSize?: number) {
    this._orders = orders;
    if (tickSize) this._tick = tickSize;
    if (!orders.length) this._layout = null;
    // The library caches axis views on the array's identity, so only rebuild it
    // when the *set* of orders changes — a drag re-publishes prices constantly
    // and must not thrash it. All three legs get a view whether or not the order
    // has them; a missing one reports itself invisible, so a bracket appearing or
    // going away needs no rebuild either.
    const ids = orders.map((o) => o.id).join(",");
    if (ids !== this.axisIds) {
      this.axisIds = ids;
      this.axisViews = orders.flatMap((o) => [
        new AxisView(this, o.id, "price"),
        new AxisView(this, o.id, "stop"),
        new AxisView(this, o.id, "target"),
      ]);
    }
    this.requestUpdate?.();
  }

  setMark(v: number) {
    if (!Number.isFinite(v) || v === this._mark) return;
    this._mark = v;
    if (this._orders.length) this.requestUpdate?.();
  }

  orders(): WorkingOrderView[] {
    return this._orders;
  }

  tickSize(): number {
    return this._tick;
  }

  /** Called from the chart's pointer handlers — highlights the grabbed leg. */
  setHover(h: OrderHit | null) {
    const a = this._hover;
    if (a === h || (a && h && a.id === h.id && a.leg === h.leg)) return;
    this._hover = h;
    this.requestUpdate?.();
  }

  /** What's under the pointer, using the boxes the last paint actually drew. */
  hit(x: number, y: number): OrderHit | null {
    const L = this._layout;
    if (!L || !this._orders.length) return null;
    for (const c of L.chips) if (inside(c.rect, x, y)) return c.hit;
    for (const l of L.lines) if (Math.abs(y - l.y) <= GRAB_PX) return l.hit;
    return null;
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    this.series = param.series;
    const ctx: Ctx = {
      series: param.series,
      orders: () => this._orders,
      mark: () => this._mark,
      tickSize: () => this._tick,
      hover: () => this._hover,
      setLayout: (l) => {
        this._layout = l;
      },
    };
    this.views = [new View(new Renderer(ctx))];
    this.requestUpdate?.();
  }

  detached() {
    this.views = [];
    this.series = null;
  }

  updateAllViews() {
    this.views.forEach((v) => v.update());
  }

  paneViews() {
    return this.views;
  }

  priceAxisViews() {
    return this._orders.length ? this.axisViews : NO_AXIS_VIEWS;
  }
}
