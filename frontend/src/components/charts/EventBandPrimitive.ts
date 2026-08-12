// Tape events as bands: the prices a sweep burst or an absorption printed
// across, drawn as the rectangle it happened in.
//
// The band's *height* is the whole read, which is why this is a box and not
// another bubble. Size that walked leaves a tall band — the shape of a stop-run
// working through resting orders. Size that went nowhere leaves a flat one,
// which is what a refilling passive order looks like from the trade feed. Both
// are the same number of lots; the difference between them is the only thing the
// tape can say about whether that size was initiating or defending.
//
// Hue is the aggressor, exactly as the big-trade bubbles have it — blue lifted
// the offer, orange hit the bid — so the two layers speak one language. The kind
// is the outline: a burst is solid (it arrived), an absorption dashed (it sat).
//
// Drawn in two layers, for the reason the fixed-range profile splits: a single
// z-order can't serve both halves. The wash goes *under* the candles so it never
// becomes a lid over the price action, while the outline and the size label go
// *over* them — a box whose edges are buried behind a dense candle cluster is a
// box you can't see, and the edges are what carry the read (where it started,
// where it stopped, how tall it is).
//
// These are proxies, and measured *negative* ones. Against a frozen composite,
// both land further from its levels than the session's own volume-weighted tape
// does (+20.5 / +22.9pt paired on 40 sessions, +6.8 / +7.4 on 120, and the sign
// never flips) — so an event stacking on a level is not evidence of anything:
// events cluster where price traded, and price traded where value is. Read them
// as shape, not as confirmation.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { TapeEvent } from "../../lib/replayEngine";
import { palette } from "../../theme";

/** Aggressor hues, shared with BigTradePrimitive. */
const BUY_RGB = "59, 130, 246"; // palette.blue
const SELL_RGB = "249, 115, 22"; // palette.orange

/** A band this narrow has no height to read, so it is drawn as a bar of this
 *  many px instead of vanishing into the candle it sits on. Flat is a *reading*
 *  — size that went nowhere — so it has to survive being flat. */
const MIN_H = 6;
/** Minimum width, for an event that begins and ends inside one bar — which most
 *  of them do on a minute bar. */
const MIN_W = 7;
/** Fill alpha at strength 1 — the wash sits under the candles, so it can afford
 *  to be seen. A setting rather than a constant, because how loud a band should
 *  be depends on what else is drawn: with a composite, its nodes and the
 *  developing profile all on, 0.2 is a lot of colour. Zero is outline-only,
 *  which still says everything the band's *shape* says.
 *
 *  What each event adds over strength 1, and the ceiling, ride with it — they
 *  are proportions of the base (0.7× and 2.5×), which is what they were at the
 *  measured default. */
const FILL_A = 0.2;
const FILL_GAIN = 0.7;
const FILL_MAX = 2.5;
/** The outline, over the candles: opaque, because this is the part that says
 *  where the event was. */
const EDGE_W = 1.5;
/** The side flag — a solid stub on the left edge, so an event that is flat and
 *  narrow is still unmistakably an event. */
const FLAG_W = 3;
/** Strength at which an event gets its lots written on it. Below this it is a
 *  number on every band, and there are ~19 a session — but where the line goes
 *  is a reading choice, so it is a setting with this as its default. Zero is no
 *  labels at all. */
const LABEL_ST = 1.5;

/** How loud the layer draws. Both are strengths/alphas rather than pixels: the
 *  geometry of a band is the read, and nothing here is allowed to change it. */
export interface EventStyle {
  /** Strength at or above which a band carries its lot count. 0 = never. */
  labelSt: number;
  /** Fill alpha at strength 1. 0 = outline only. */
  fill: number;
}

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  events: () => TapeEvent[];
  style: () => EventStyle;
}

/** One event resolved to pixels, or null when it can't be (its bars aren't on
 *  the chart, or its prices are off the current scale). Nothing is clamped onto
 *  an edge — the same rule every other mark on this chart follows. */
function box(
  c: Ctx,
  e: TapeEvent,
): { x: number; y: number; w: number; h: number; rgb: string } | null {
  const ts = c.chart.timeScale();
  const xa = ts.timeToCoordinate(e.from as Time);
  const xb = ts.timeToCoordinate(e.to as Time);
  const yHi = c.series.priceToCoordinate(e.hi);
  const yLo = c.series.priceToCoordinate(e.lo);
  if (xa == null || xb == null || yHi == null || yLo == null) return null;
  return {
    x: Math.min(xa, xb),
    y: Math.min(yHi, yLo),
    w: Math.max(MIN_W, Math.abs(xb - xa)),
    h: Math.max(MIN_H, Math.abs(yLo - yHi)),
    rgb: e.buy ? BUY_RGB : SELL_RGB,
  };
}

// Under the candles: the wash.
class FillRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const events = this.c.events();
    const base = this.c.style().fill;
    // Outline-only: the whole wash pass is skipped rather than drawn at alpha 0.
    if (!events.length || base <= 0) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      for (const e of events) {
        const b = box(this.c, e);
        if (!b) continue;
        const a = Math.min(base * FILL_MAX, base * (1 + FILL_GAIN * (e.st - 1)));
        ctx.fillStyle = `rgba(${b.rgb}, ${a})`;
        ctx.fillRect(b.x, b.y, b.w, b.h);
      }
    });
  }
}

// Over the candles: the outline, the side flag, and the size.
class EdgeRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const events = this.c.events();
    if (!events.length) return;
    const labelSt = this.c.style().labelSt;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const labels: { x: number; y: number; text: string; color: string }[] = [];

      for (const e of events) {
        const b = box(this.c, e);
        if (!b) continue;
        ctx.save();
        ctx.strokeStyle = `rgb(${b.rgb})`;
        ctx.lineWidth = EDGE_W;
        // Solid arrived, dashed sat there — the one thing the outline says that
        // the colour doesn't.
        if (e.kind === "absorb") ctx.setLineDash([4, 3]);
        ctx.strokeRect(b.x + EDGE_W / 2, b.y + EDGE_W / 2, b.w - EDGE_W, b.h - EDGE_W);
        ctx.restore();
        // The flag: solid, undashed, full height. A 6px-tall band eight pixels
        // wide is otherwise four faint dashes.
        ctx.fillStyle = `rgb(${b.rgb})`;
        ctx.fillRect(b.x, b.y, FLAG_W, b.h);

        if (labelSt > 0 && e.st >= labelSt) {
          labels.push({
            x: b.x + b.w + 4,
            y: b.y + b.h / 2,
            text: `${Math.round(e.lots)}`,
            color: e.buy ? palette.blue : palette.orange,
          });
        }
      }

      if (!labels.length) return;
      ctx.save();
      ctx.font = "600 10px Inter, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      for (const l of labels) {
        // Halo, so a number that lands over a candle stays legible.
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(14, 17, 23, 0.85)";
        ctx.strokeText(l.text, l.x, l.y);
        ctx.fillStyle = l.color;
        ctx.fillText(l.text, l.x, l.y);
      }
      ctx.restore();
    });
  }
}

class View {
  private _r: FillRenderer | EdgeRenderer;
  constructor(
    c: Ctx,
    private _z: "bottom" | "normal",
  ) {
    this._r = _z === "bottom" ? new FillRenderer(c) : new EdgeRenderer(c);
  }
  update() {}
  renderer() {
    return this._r;
  }
  zOrder() {
    return this._z;
  }
}

export class EventBandPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _events: TapeEvent[] = [];
  private _style: EventStyle = { labelSt: LABEL_ST, fill: FILL_A };

  /** What to draw, as of the clock — already filtered by the caller (the
   *  strength floor and the two per-kind toggles), because the same filtered
   *  list also feeds the marginals over the profiles and the legend's counts,
   *  and three copies of one filter is three places for them to disagree. */
  setEvents(events: TapeEvent[]) {
    this._events = events;
    this.requestUpdate?.();
  }

  /** How loud to draw. Separate from `setEvents` because it changes on its own
   *  clock — a knob turned while the replay is paused has to repaint. */
  setStyle(style: EventStyle) {
    this._style = style;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      events: () => this._events,
      style: () => this._style,
    };
    this.views = [new View(ctx, "bottom"), new View(ctx, "normal")];
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
