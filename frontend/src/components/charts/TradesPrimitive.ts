// Closed trades on the replay chart: an entry arrow, an exit dot, the leg
// between them, and what it paid.
//
// The blotter already lists every trade, but a list can't tell you *where* you
// were — which is the whole question a replay is asked ("would I take that
// again?"). So the marks are drawn on the tape they happened on and they stay
// there for the rest of the session.
//
// Two things are being read off a mark — which way you leaned, and how it went —
// and they are deliberately encoded in different channels:
//
//   - *hue* is the side: blue bought, orange sold. Not green/red, which is what
//     the candles under the mark are already using — a green line over green
//     bodies is a line you have to hunt for;
//   - *shape* is the outcome: the exit dot is filled on a winner and hollow on a
//     loser, and the label carries the signed number.
//
// So a losing long is a blue arrow on a blue dotted leg ending in a hollow dot.
// Nothing about the mark competes with the candles for the eye, and neither cue
// depends on telling two colours apart.
//
// A series primitive rather than the library's own markers, for the same reason
// MarkerPrimitive exists: this chart already carries custom primitives (the
// position overlay, the working orders), which suppress the built-in marker
// layer in lightweight-charts.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { ink, palette } from "../../theme";

/** One closed trade, in the terms the chart draws in: bar times (epoch seconds)
 *  and prices. The page derives it from its own trade log. */
export interface TradeMarkView {
  id: number;
  side: "long" | "short";
  size: number;
  entryTime: number;
  entryPrice: number;
  exitTime: number;
  exitPrice: number;
  pnl: number;
  /** Stake R: what the trade made against the money it risked at open. Null when
   *  it carried no stop, so there was no risk to measure by. */
  r: number | null;
  /** `reduce` is size taken off by an order on the other side — a scale-out, or
   *  the closing half of a flip. `trail` is a stop the ladder had moved, which
   *  is a different event from the stop you placed. */
  reason: "manual" | "stop" | "target" | "reduce" | "trail";
  /** The bracket the position opened with, drawn across the trade's own span.
   *  Optional: a live replay draws its bracket on the *position* overlay while
   *  the trade is on, and has no reason to repeat it once the trade is history.
   *  Recall's back card is the caller that does — there the trade is only ever
   *  history, and where the risk sat is half of what it is being asked. */
  stop?: number | null;
  target?: number | null;
  /** A resting entry: the order waited at `price` from bar time `from` until the
   *  fill. Only the wait is worth drawing — a limit fills *at* its limit, so the
   *  level itself is already under the entry arrow. */
  rest?: { from: number; price: number } | null;
}

/** Side hues, as "r, g, b" triplets — the renderer composes its own alpha. Blue
 *  bought, orange sold: the two the candles never use. */
const LONG_RGB = "59, 130, 246"; // palette.blue
const SHORT_RGB = "249, 115, 22"; // palette.orange
/** The wash between entry and exit. Same weight as the position overlay's risk
 *  and reward zones — a closed trade shouldn't shout louder than the open one. */
const WASH_ALPHA = 0.1;
/** Below this the trade is a couple of pixels wide and a label would be a smear
 *  of text over the candles it belongs to. The marks still draw. */
const LABEL_MIN_W = 26;
const ARROW = 5;
/** The bracket legs, in the hues OrdersPrimitive sketches them in — a stop is a
 *  stop wherever it is drawn. Dotted and short, which is what keeps them from
 *  reading as levels price could still reach. */
const SL_LINE = "rgba(245, 69, 95, 0.55)";
const TP_LINE = "rgba(33, 192, 122, 0.55)";
/** How short a bracket leg is allowed to get. Most trades are over inside one
 *  bar, so entry and exit land on the same x and the legs would be 1px stubs —
 *  invisible, and there is no point drawing a level nobody can see. The mark
 *  itself says how long the trade lasted; this width is legibility, not
 *  duration, and it never *shrinks* a leg below the span it covered. */
const LEG_MIN_W = 36;
/** The leg, and every other stroke here: dotted, so a mark never reads as one of
 *  the chart's own levels (a VWAP, an IB edge, a bracket) at a glance. */
const DOTS = [2, 3];

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
  trades: () => TradeMarkView[];
  tickSize: () => number;
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
      const dec = decimalsOf(this.c.tickSize());

      for (const t of trades) {
        const x1 = ts.timeToCoordinate(t.entryTime as Time);
        const x2 = ts.timeToCoordinate(t.exitTime as Time);
        const y1 = series.priceToCoordinate(t.entryPrice);
        const y2 = series.priceToCoordinate(t.exitPrice);
        // A bar the chart hasn't loaded (or a price off the current scale) gives
        // no coordinate. Nothing is clamped onto an edge — a mark pinned to the
        // wrong bar would be a lie about where the trade happened.
        if (x1 == null || x2 == null || y1 == null || y2 == null) continue;

        const long = t.side === "long";
        const rgb = long ? LONG_RGB : SHORT_RGB;
        const won = t.pnl >= 0;
        const left = Math.min(x1, x2);
        const w = Math.max(Math.abs(x2 - x1), 1);

        // The bracket the position opened with, spanning the trade it covered —
        // under the mark, so the trade is still the thing you see first. Dotted
        // like the rest of this overlay, which is what keeps a stop that was
        // taken out decades of bars ago from reading as a level price could
        // still reach.
        const legW = Math.max(w, LEG_MIN_W);
        const leg = (price: number | null | undefined, colour: string) => {
          if (price == null) return;
          const y = series.priceToCoordinate(price);
          if (y == null) return;
          ctx.save();
          ctx.strokeStyle = colour;
          ctx.lineWidth = 1;
          ctx.setLineDash(DOTS);
          ctx.beginPath();
          ctx.moveTo(left, y);
          ctx.lineTo(left + legW, y);
          ctx.stroke();
          ctx.restore();
        };
        leg(t.stop, SL_LINE);
        leg(t.target, TP_LINE);

        // A resting entry, drawn as the wait rather than as a level: a limit
        // fills at its limit, so the price is already under the entry arrow and
        // what this adds is how long the order sat there for.
        if (t.rest) {
          const x0 = ts.timeToCoordinate(t.rest.from as Time);
          const y0 = series.priceToCoordinate(t.rest.price);
          if (x0 != null && y0 != null) {
            ctx.save();
            ctx.strokeStyle = `rgba(${rgb}, 0.45)`;
            ctx.lineWidth = 1;
            ctx.setLineDash(DOTS);
            ctx.beginPath();
            ctx.moveTo(x0, y0);
            ctx.lineTo(x1, y0);
            ctx.stroke();
            ctx.restore();
          }
        }

        // Entry-to-exit wash. A scratch (both prices equal) collapses to a
        // 1px band, which is the honest picture of it.
        ctx.fillStyle = `rgba(${rgb}, ${WASH_ALPHA})`;
        ctx.fillRect(left, Math.min(y1, y2), w, Math.max(Math.abs(y2 - y1), 1));

        // The leg itself, entry price to exit price.
        ctx.save();
        ctx.strokeStyle = `rgba(${rgb}, 0.9)`;
        ctx.lineWidth = 1.5;
        ctx.setLineDash(DOTS);
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.restore();

        // Entry arrow, pointing the way the position faced, with its apex on the
        // fill price — so the tip marks the level and the body sits clear of it.
        ctx.fillStyle = `rgba(${rgb}, 1)`;
        ctx.beginPath();
        if (long) {
          ctx.moveTo(x1, y1);
          ctx.lineTo(x1 - ARROW, y1 + ARROW * 1.8);
          ctx.lineTo(x1 + ARROW, y1 + ARROW * 1.8);
        } else {
          ctx.moveTo(x1, y1);
          ctx.lineTo(x1 - ARROW, y1 - ARROW * 1.8);
          ctx.lineTo(x1 + ARROW, y1 - ARROW * 1.8);
        }
        ctx.closePath();
        ctx.fill();

        // Exit dot, and the one place the outcome is drawn rather than written:
        // solid took money off the table, hollow gave it back. Shape rather than
        // a second hue, so the mark stays one colour end to end.
        ctx.save();
        ctx.fillStyle = won ? `rgba(${rgb}, 1)` : ink().chip.outline;
        ctx.strokeStyle = `rgba(${rgb}, 1)`;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x2, y2, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.restore();

        if (w < LABEL_MIN_W) continue;
        // What it paid, at the exit end and on the far side of it from the
        // trade, so the text never sits over its own wash.
        const label =
          fmtUsd(t.pnl) + (t.r != null ? ` · ${t.r >= 0 ? "+" : "−"}${Math.abs(t.r).toFixed(1)}R` : "");
        // Saved around the text state: primitives share one canvas, and a font
        // or an alignment left behind here would land on whoever draws next.
        ctx.save();
        ctx.font = "600 10px Inter, sans-serif";
        ctx.textAlign = "left";
        const up = y2 <= y1;
        ctx.textBaseline = up ? "bottom" : "top";
        ctx.fillStyle = `rgba(${rgb}, 1)`;
        ctx.fillText(label, x2 + 6, up ? y2 - 5 : y2 + 5);
        // The exit price, under the label — the one number you'd otherwise have
        // to read off the axis by eye.
        ctx.fillStyle = palette.muted;
        ctx.fillText(t.exitPrice.toFixed(dec), x2 + 6, up ? y2 - 16 : y2 + 16);
        ctx.restore();
      }
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
    // orders — history should never cover the thing you are currently doing.
    return "normal" as const;
  }
}

export class TradesPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _trades: TradeMarkView[] = [];
  private _tickSize = 0.25;
  private _visible = true;

  /** Swap the whole closed-trade set. Called on every fill and every rewind —
   *  the page re-derives its trades from the log, so this is always the full
   *  list as of the current clock, not an append. */
  setTrades(trades: TradeMarkView[], tickSize?: number) {
    this._trades = trades;
    if (tickSize) this._tickSize = tickSize;
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
      tickSize: () => this._tickSize,
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
