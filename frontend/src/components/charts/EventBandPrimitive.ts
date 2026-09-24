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
// the offer, orange hit the bid — so the two layers speak one language. But an
// event, unlike a print, has two sides in it, and how much of it was one side's
// is most of the read: the hue desaturates toward the theme's neutral as the
// split approaches even, so a clean one-sided run keeps its colour and a 52/48
// fight over one price greys out instead of impersonating it. The kind is the
// outline: a burst is solid (it arrived), an absorption dashed (it sat) — and
// an event still growing has a dashed *right* edge, because that edge is the
// claim "it ended here" and the clusterer hasn't made it yet.
//
// The kind is also the cloth. Both kinds cluster where price traded, so with
// both drawn they overlap — and two solid washes stack into a third colour that
// names neither. So the burst's wash is solid and the absorption's is hatched:
// where they overlap the eye sees solid *with lines through it*, which reads as
// "both happened here" at any pile-up depth, and survives on a 6px band where a
// dash pattern doesn't. Each wash also has its own loudness knob, so one kind
// can run outline-only while the other keeps its colour.
//
// A burst overlapping an absorption in time and price is not clutter to be
// managed but the most interesting thing the layer can say — aggressive size
// arriving into size that wouldn't move. That intersection gets a thin neutral
// stroke (the theme's, brighter than either hue: the collision is a fact about
// both sides, so it wears neither's colour). Descriptive, like everything here
// — nothing about it has been tested as an edge.
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
import { ink } from "../../theme";

/** Aggressor hues, shared with BigTradePrimitive, and the neutral a contested
 *  band fades toward (palette.muted): at 50/50 the majority side means nothing,
 *  so it isn't shown. */
const BUY_RGB = [59, 130, 246]; // palette.blue
const SELL_RGB = [249, 115, 22]; // palette.orange
const EVEN_RGB = [138, 143, 156]; // palette.muted

/** The side hue at the event's one-sidedness: full colour when every lot was
 *  one side's, the neutral when the split is even, linear between. */
function sideRgb(e: TapeEvent): string {
  const t = e.lots > 0 ? Math.min(1, Math.abs((2 * e.buyLots) / e.lots - 1)) : 0;
  const hue = e.buy ? BUY_RGB : SELL_RGB;
  return hue.map((v, i) => Math.round(EVEN_RGB[i] + (v - EVEN_RGB[i]) * t)).join(", ");
}

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
/** The absorption wash's hatch: line every STEP px, at GAIN× the alpha a solid
 *  wash would get — the lines cover roughly a fifth of the area, so each one
 *  carries more ink to keep the two washes comparable at the same knob. */
const HATCH_STEP = 5;
const HATCH_GAIN = 2.5;
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

/** How loud the layer draws. All strengths/alphas rather than pixels: the
 *  geometry of a band is the read, and nothing here is allowed to change it.
 *  The wash is per kind — with both kinds on, being able to quiet one without
 *  toggling it off is most of what keeps the pile readable. */
export interface EventStyle {
  /** Strength at or above which a band carries its lot count. 0 = never. */
  labelSt: number;
  /** Fill alpha at strength 1, per kind. 0 = outline only. */
  fillSweep: number;
  fillAbsorb: number;
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
    rgb: sideRgb(e),
  };
}

// Under the candles: the wash. Solid for a burst, hatched for an absorption —
// the kind as a texture, which is the only encoding that survives the two
// overlapping (see the header).
class FillRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const events = this.c.events();
    const style = this.c.style();
    // Outline-only: the whole wash pass is skipped rather than drawn at alpha 0.
    if (!events.length || (style.fillSweep <= 0 && style.fillAbsorb <= 0)) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      for (const e of events) {
        const base = e.kind === "absorb" ? style.fillAbsorb : style.fillSweep;
        if (base <= 0) continue;
        const b = box(this.c, e);
        if (!b) continue;
        const a = Math.min(base * FILL_MAX, base * (1 + FILL_GAIN * (e.st - 1)));
        if (e.kind === "absorb") {
          ctx.save();
          ctx.beginPath();
          ctx.rect(b.x, b.y, b.w, b.h);
          ctx.clip();
          ctx.strokeStyle = `rgba(${b.rgb}, ${Math.min(0.9, a * HATCH_GAIN)})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          for (let s = -b.h; s < b.w; s += HATCH_STEP) {
            ctx.moveTo(b.x + s, b.y);
            ctx.lineTo(b.x + s + b.h, b.y + b.h);
          }
          ctx.stroke();
          ctx.restore();
        } else {
          ctx.fillStyle = `rgba(${b.rgb}, ${a})`;
          ctx.fillRect(b.x, b.y, b.w, b.h);
        }
      }
    });
  }
}

// Over the candles: the outline, the side flag, the collision stroke, and the
// size.
class EdgeRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const events = this.c.events();
    if (!events.length) return;
    const labelSt = this.c.style().labelSt;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const labels: { x: number; y: number; text: string; color: string; kind: string }[] = [];
      const placed: { e: TapeEvent; b: NonNullable<ReturnType<typeof box>> }[] = [];

      for (const e of events) {
        const b = box(this.c, e);
        if (!b) continue;
        placed.push({ e, b });
        ctx.save();
        ctx.strokeStyle = `rgb(${b.rgb})`;
        ctx.lineWidth = EDGE_W;
        // Solid arrived, dashed sat there — the one thing the outline says that
        // the colour doesn't.
        if (e.kind === "absorb") ctx.setLineDash([4, 3]);
        const x0 = b.x + EDGE_W / 2;
        const y0 = b.y + EDGE_W / 2;
        const x1 = x0 + b.w - EDGE_W;
        const y1 = y0 + b.h - EDGE_W;
        if (e.open) {
          // Still growing: the right edge is the claim "it ended here", so it
          // stays dashed — tighter than absorption's dash, so the two reads
          // don't blur — until the clusterer settles the event.
          ctx.beginPath();
          ctx.moveTo(x1, y0);
          ctx.lineTo(x0, y0);
          ctx.lineTo(x0, y1);
          ctx.lineTo(x1, y1);
          ctx.stroke();
          ctx.setLineDash([2, 2]);
          ctx.beginPath();
          ctx.moveTo(x1, y0);
          ctx.lineTo(x1, y1);
          ctx.stroke();
        } else {
          ctx.strokeRect(x0, y0, b.w - EDGE_W, b.h - EDGE_W);
        }
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
            // The band's own blended hue, so a grey band doesn't wear a
            // committed-blue number.
            color: `rgb(${b.rgb})`,
            kind: e.kind,
          });
        }
      }

      // A burst meeting an absorption — aggressive size arriving into size that
      // wouldn't move — is the layer's most interesting sentence, and where the
      // pile-up is worst. Overlap is decided on the events' own times and
      // prices, not their pixels: MIN_W/MIN_H pad tiny boxes, and two bands
      // that merely *draw* close by are not a collision.
      ctx.save();
      ctx.strokeStyle = ink().eventCollide;
      ctx.lineWidth = 1;
      for (const s of placed) {
        if (s.e.kind !== "sweep") continue;
        for (const a of placed) {
          if (a.e.kind !== "absorb") continue;
          if (s.e.from > a.e.to || a.e.from > s.e.to) continue;
          if (s.e.lo > a.e.hi || a.e.lo > s.e.hi) continue;
          const x = Math.max(s.b.x, a.b.x);
          const y = Math.max(s.b.y, a.b.y);
          const w = Math.min(s.b.x + s.b.w, a.b.x + a.b.w) - x;
          const h = Math.min(s.b.y + s.b.h, a.b.y + a.b.h) - y;
          if (w > 2 && h > 2) ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
        }
      }
      ctx.restore();

      if (!labels.length) return;
      // Two numbers on one spot attribute to nothing: nudge a label clear of
      // the one above it when their boxes land together, top-down so a stack of
      // three fans out instead of leapfrogging.
      labels.sort((p, q) => p.y - q.y || p.x - q.x);
      for (let i = 1; i < labels.length; i++) {
        const prev = labels[i - 1];
        const l = labels[i];
        if (Math.abs(l.x - prev.x) < 40 && l.y - prev.y < 11) l.y = prev.y + 11;
      }
      ctx.save();
      ctx.font = "600 10px Inter, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      for (const l of labels) {
        // Halo, so a number that lands over a candle stays legible.
        ctx.lineWidth = 3;
        ctx.strokeStyle = ink().chip.outline;
        ctx.strokeText(l.text, l.x + 8, l.y);
        ctx.fillStyle = l.color;
        ctx.fillText(l.text, l.x + 8, l.y);
        // The kind, in the outline's own vocabulary: a filled square arrived, a
        // hollow one sat — so a number nudged away from its band still says
        // which box owns it.
        if (l.kind === "sweep") {
          ctx.fillRect(l.x, l.y - 2.5, 5, 5);
        } else {
          ctx.lineWidth = 1;
          ctx.strokeStyle = l.color;
          ctx.strokeRect(l.x + 0.5, l.y - 2, 4, 4);
        }
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
  private _style: EventStyle = { labelSt: LABEL_ST, fillSweep: FILL_A, fillAbsorb: FILL_A };

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
