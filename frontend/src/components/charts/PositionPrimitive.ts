// TradingView-style open-position overlay: the entry line, its stop and target,
// the shaded risk / reward zones between them, and a chip pinned to the right of
// each line carrying what that leg is worth in dollars. Plus the price-axis
// labels, so the three levels read off the scale the same way the crosshair does.
//
// The two bracket lines are draggable. The grab/drag mechanics live in
// ReplayChart — as they do for every other tool on that chart, because only the
// chart owns the pointer — so this exposes `hit(x, y)`: the boxes it actually
// drew, not a re-derivation of where they should be. That means a chip is
// grabbable exactly where it looks grabbable, whatever the layout did to fit it.

import type { IChartApi, ISeriesApi, ISeriesPrimitiveAxisView, Time } from "lightweight-charts";
import { palette } from "../../theme";
import { byPointer } from "../../lib/pointer";

/** What a pointer can grab: a bracket line/chip, the entry chip's ✕, or the entry
 *  chip itself — the last only while a leg is missing, since the one gesture it
 *  carries is "hold, then pull the leg you haven't got out of here". */
export type PosHit = "stop" | "target" | "close" | "entry";
type Leg = "entry" | "stop" | "target";

export interface PositionData {
  side: "long" | "short";
  size: number;
  entry: number;
  stop: number | null;
  target: number | null;
  /** Bar time (epoch seconds) the position opened at — where the zones start. */
  entryTime: number;
  /** Mark price. The entry chip prices the open position off it, and it's what
   *  a drag is clamped against (a bracket you could not have working is not a
   *  bracket — see ReplayChart's clamp). */
  last: number;
  pointValue: number;
  tickSize: number;
}

const ZONE_UP = "rgba(33, 192, 122, 0.10)";
const ZONE_DOWN = "rgba(245, 69, 95, 0.10)";
const CHIP_BG = "rgba(14, 17, 23, 0.92)";

const CHIP_H = 18;
const PAD = 6;
const MARGIN = 6;
/** The ✕ hit box. Drawn at the same size it is hit at, so a finger-sized target
 *  is also a finger-sized thing to aim at. */
const CLOSE_W = byPointer(18, 30);
/** How close (px) the pointer must be to a bare line to grab it. A fingertip
 *  covers roughly 9mm of glass and lands a few px from where its owner thinks it
 *  did; 5px is a mouse's tolerance and would make these lines ungrabbable. */
const GRAB_PX = byPointer(5, 22);

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
interface Layout {
  /** Left edge of the position: nothing before the entry bar is grabbable. */
  x0: number;
  /** Chips, topmost-drawn first — the ✕ sits on top of the entry line. */
  chips: { hit: PosHit; rect: Rect }[];
  lines: { hit: PosHit; y: number }[];
}

const inside = (r: Rect, x: number, y: number) =>
  x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;

const decimalsOf = (tick: number): number =>
  !tick || tick >= 1 ? 2 : Math.min(6, Math.ceil(-Math.log10(tick)));

const fmtUsd = (v: number): string =>
  `${v < 0 ? "−" : "+"}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: () => PositionData | null;
  hover: () => PosHit | null;
  setLayout: (l: Layout | null) => void;
}

class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const d = this.c.data();
    if (!d) {
      this.c.setLayout(null);
      return;
    }
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const series = this.c.series;
      const W = scope.mediaSize.width;
      const yE = series.priceToCoordinate(d.entry);
      if (yE == null) {
        this.c.setLayout(null);
        return;
      }
      const yS = d.stop != null ? series.priceToCoordinate(d.stop) : null;
      const yT = d.target != null ? series.priceToCoordinate(d.target) : null;
      // Off the left edge once the entry has scrolled away, which is normal:
      // the position is still open, it just started before the visible range.
      const rawX = this.c.chart.timeScale().timeToCoordinate(d.entryTime as Time);
      const x0 = Math.max(0, rawX ?? 0);

      // Risk and reward zones. Drawn from the entry bar to the right edge, so
      // the shading reads as "what this trade is still exposed to", not as a box
      // around history.
      const zone = (y: number | null, fill: string) => {
        if (y == null) return;
        ctx.fillStyle = fill;
        ctx.fillRect(x0, Math.min(yE, y), W - x0, Math.abs(y - yE));
      };
      zone(yT, ZONE_UP);
      zone(yS, ZONE_DOWN);

      const hover = this.c.hover();
      const line = (y: number, color: string, dashed: boolean, hot: boolean) => {
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = hot ? 2 : 1;
        if (dashed) ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.moveTo(x0, Math.round(y) + 0.5);
        ctx.lineTo(W, Math.round(y) + 0.5);
        ctx.stroke();
        ctx.restore();
      };
      const sideColor = d.side === "long" ? palette.green : palette.red;
      line(yE, sideColor, false, false);
      if (yS != null) line(yS, palette.red, true, hover === "stop");
      if (yT != null) line(yT, palette.green, true, hover === "target");

      // --- chips ------------------------------------------------------------
      // No prices in the chips: each of these three levels already names itself on
      // the price axis, in a label the same colour as its line. Printing it twice
      // only makes the chip long enough to cover the candles it sits over.
      ctx.font = "600 10px Inter, sans-serif";
      ctx.textBaseline = "middle";
      const dir = d.side === "long" ? 1 : -1;
      const mult = d.pointValue * d.size;
      const riskPts = d.stop != null ? Math.abs(d.entry - d.stop) : null;

      const chips: { hit: PosHit; rect: Rect }[] = [];
      // One chip = right-aligned pill of coloured segments. Returns its rect so
      // the hit map is whatever fitted on screen.
      const chip = (
        y: number,
        segs: { text: string; color: string }[],
        border: string,
        closeBtn: boolean,
        hot = false,
      ): Rect => {
        const textW = segs.reduce((a, s) => a + ctx.measureText(s.text).width, 0);
        const w = textW + PAD * 2 + (segs.length - 1) * PAD + (closeBtn ? CLOSE_W : 0);
        const r: Rect = { x: W - MARGIN - w, y: y - CHIP_H / 2, w, h: CHIP_H };
        ctx.fillStyle = CHIP_BG;
        ctx.strokeStyle = border;
        ctx.lineWidth = hot ? 2 : 1;
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
        if (closeBtn) {
          const cx = r.x + r.w - CLOSE_W / 2;
          ctx.save();
          ctx.strokeStyle = hover === "close" ? palette.red : palette.muted;
          ctx.lineWidth = hover === "close" ? 1.6 : 1.2;
          ctx.beginPath();
          ctx.moveTo(cx - 3.5, y - 3.5);
          ctx.lineTo(cx + 3.5, y + 3.5);
          ctx.moveTo(cx + 3.5, y - 3.5);
          ctx.lineTo(cx - 3.5, y + 3.5);
          ctx.stroke();
          ctx.restore();
          chips.push({
            hit: "close",
            rect: { x: r.x + r.w - CLOSE_W, y: r.y, w: CLOSE_W, h: r.h },
          });
        }
        return r;
      };

      // Bracket chips are grabbable as well as their lines, so a drag can start
      // on the number you're trying to change. Recorded from the rect the layout
      // actually produced rather than a guessed strip.
      const legChips: { hit: PosHit; rect: Rect }[] = [];

      // Target first, stop last: when the three chips crowd together the ✕ on
      // the entry chip is the one that must stay clickable, and `chips` is
      // searched in order.
      if (yT != null && d.target != null) {
        const pts = Math.abs(d.target - d.entry);
        const r = riskPts && riskPts > 0 ? pts / riskPts : null;
        const rect = chip(
          yT,
          [
            { text: "TP", color: palette.green },
            {
              text: `${fmtUsd(pts * mult)}${r != null ? ` · ${r.toFixed(1)}R` : ""}`,
              color: palette.green,
            },
          ],
          palette.green,
          false,
        );
        legChips.push({ hit: "target", rect });
      }
      const openPnl = Number.isFinite(d.last) ? (d.last - d.entry) * dir * mult : 0;
      const bare = d.stop == null || d.target == null;
      const entryRect = chip(
        yE,
        [
          { text: `${d.side === "long" ? "LONG" : "SHORT"} ×${d.size}`, color: sideColor },
          { text: fmtUsd(openPnl), color: openPnl >= 0 ? palette.green : palette.red },
        ],
        sideColor,
        true,
        bare && hover === "entry",
      );
      if (yS != null && d.stop != null) {
        const rect = chip(
          yS,
          [
            { text: "SL", color: palette.red },
            { text: fmtUsd(-Math.abs(d.entry - d.stop) * mult), color: palette.red },
          ],
          palette.red,
          false,
        );
        legChips.push({ hit: "stop", rect });
      }
      // The entry chip is grabbable only while a leg is missing, and it is checked
      // after both bracket chips: where they crowd it, the leg you can actually
      // drag outranks the one you'd have to hold to pull out.
      if (bare) legChips.push({ hit: "entry", rect: entryRect });

      // Everything drawn: publish where it landed.
      const lines: { hit: PosHit; y: number }[] = [];
      if (yS != null) lines.push({ hit: "stop", y: yS });
      if (yT != null) lines.push({ hit: "target", y: yT });
      this.c.setLayout({ x0, chips: [...chips, ...legChips], lines });
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

// A price-axis label per leg. Values are read live through the primitive, so the
// three view objects (and the array holding them) can be created once — which is
// what the library wants: it caches on the array's identity.
class AxisView implements ISeriesPrimitiveAxisView {
  constructor(
    private p: PositionPrimitive,
    private leg: Leg,
  ) {}
  private price(): number | null {
    const d = this.p.data();
    if (!d) return null;
    const v = this.leg === "entry" ? d.entry : this.leg === "stop" ? d.stop : d.target;
    return v ?? null;
  }
  coordinate(): number {
    const price = this.price();
    if (price == null) return -100;
    return this.p.series?.priceToCoordinate(price) ?? -100;
  }
  text(): string {
    const price = this.price();
    const d = this.p.data();
    if (price == null || !d) return "";
    return price.toFixed(decimalsOf(d.tickSize));
  }
  textColor(): string {
    return "#ffffff";
  }
  backColor(): string {
    const d = this.p.data();
    if (this.leg === "stop") return palette.red;
    if (this.leg === "target") return palette.green;
    return d?.side === "short" ? palette.red : palette.green;
  }
  visible(): boolean {
    return this.price() != null;
  }
}

const NO_AXIS_VIEWS: ISeriesPrimitiveAxisView[] = [];

export class PositionPrimitive {
  series: ISeriesApi<"Candlestick"> | null = null;
  private views: View[] = [];
  private axisViews: ISeriesPrimitiveAxisView[] = [];
  private requestUpdate?: () => void;
  private _data: PositionData | null = null;
  private _hover: PosHit | null = null;
  private _layout: Layout | null = null;

  /** Swap the whole position (open, closed, or bracket moved) and repaint. */
  setData(d: PositionData | null) {
    this._data = d;
    if (!d) this._layout = null;
    this.requestUpdate?.();
  }

  data(): PositionData | null {
    return this._data;
  }

  /** Called from the chart's pointer handlers — highlights the grabbed leg. */
  setHover(h: PosHit | null) {
    if (h === this._hover) return;
    this._hover = h;
    this.requestUpdate?.();
  }

  /** What's under the pointer, using the boxes the last paint actually drew. */
  hit(x: number, y: number): PosHit | null {
    const L = this._layout;
    if (!L || !this._data) return null;
    for (const c of L.chips) if (inside(c.rect, x, y)) return c.hit;
    if (x < L.x0) return null;
    for (const l of L.lines) if (Math.abs(y - l.y) <= GRAB_PX) return l.hit;
    return null;
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    this.series = param.series;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      data: () => this._data,
      hover: () => this._hover,
      setLayout: (l) => {
        this._layout = l;
      },
    };
    this.views = [new View(new Renderer(ctx))];
    this.axisViews = [
      new AxisView(this, "entry"),
      new AxisView(this, "stop"),
      new AxisView(this, "target"),
    ];
    this.requestUpdate?.();
  }

  detached() {
    this.views = [];
    this.axisViews = [];
    this.series = null;
  }

  updateAllViews() {
    this.views.forEach((v) => v.update());
  }

  paneViews() {
    return this.views;
  }

  // Stable array identities both ways round — the library caches axis views on
  // the reference it was handed, so a fresh `[]` every call would thrash it.
  priceAxisViews() {
    return this._data ? this.axisViews : NO_AXIS_VIEWS;
  }
}
