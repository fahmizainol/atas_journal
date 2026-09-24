// The Dynamic Swing VWAP's frozen segments.
//
// Every anchored VWAP this indicator has drawn and then superseded, kept on the
// pane exactly as it stood when its flip arrived. They cannot be a line series:
// each new segment is anchored at a pivot *earlier* than the flip that created
// it, so during that overlap two or more of them cover the same bars, and a line
// series holds one value per bar. In the Pine they are `polyline` objects for the
// same reason, and this is the same thing on our canvas.
//
// Reading them is the point of the indicator rather than a bonus: the live line
// on its own tells you where the current leg's average is, and the frozen ones
// are what it is peeling away *from*. Drawn under the live line and dimmer, so
// the eye still knows which one is current.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { DsvSegment } from "../../lib/dynamicSwingVwap";

/** Frozen segments are context by default, not the reading: faint enough to sit
 *  behind the live line without competing, solid enough to follow across a
 *  crowded pane. `setAlpha` moves that — see the `pastAlpha` knob, which exists
 *  because "context" is the wrong answer when the past legs *are* what you came
 *  to read. */
const DEFAULT_ALPHA = 0.5;
/** And their σ rings fainter still, per ring, on the same weighting the live
 *  envelope uses — there can be a hundred of these and the mid lines have to stay
 *  findable through them. Held as a *proportion* of the mid's alpha rather than
 *  absolute, so turning the segments up carries their envelopes with them and the
 *  ordering never inverts. No wash: a hundred stacked fills is a smear. */
const RING_RATIO: Record<number, number> = { 1: 0.44, 2: 0.6, 3: 0.32 };
/** The shadow — the segment a flip on the next bar would produce. Drawn in the
 *  *opposite* leg's colour, because that is the leg it would be, and dashed so it
 *  never reads as a level that exists. Fainter than a frozen segment: a frozen
 *  one happened, this one is a hypothesis.
 *
 *  Its σ rings ride at the same RING_RATIO share of that alpha the frozen ones
 *  use, and keep the shadow's dash rather than taking the ±2σ-solid convention —
 *  on this segment the dash is what says "not a level", and it has to survive on
 *  every line of it. No wash under them either: a fill would give a hypothesis
 *  the one piece of weight the live envelope has that the rings alone do not. */
const SHADOW_ALPHA = 0.4;
const SHADOW_DASH = [2, 4];

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  segments: () => DsvSegment[];
  ink: () => { bull: string; bear: string };
  width: () => number;
  /** How many σ rings each frozen segment gets. 0 — the default — draws mids
   *  only, which is what `bandScope: "live"` means down here. */
  bands: () => number;
  /** The alpha the frozen mid lines take; their rings follow at RING_RATIO. */
  alpha: () => number;
  /** The pending segment, or null when the shadow is off. */
  shadow: () => DsvSegment | null;
  /** How many σ rings it gets — the *live* leg's count, not the frozen ones':
   *  the shadow is the leg you would inherit, so it is drawn with the envelope
   *  the live line has rather than the one the past legs were granted. */
  shadowBands: () => number;
  visible: () => boolean;
}

class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    if (!this.c.visible()) return;
    const segs = this.c.segments();
    // Not `!segs.length` alone: early in a tape there is no frozen segment yet
    // but the shadow is already meaningful, and it would silently never draw.
    if (!segs.length && !this.c.shadow()) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const hue = this.c.ink();
      const bands = this.c.bands();
      const alpha = this.c.alpha();
      const mid = this.c.width();
      ctx.lineJoin = "round";
      ctx.lineCap = "round";

      /** One polyline through a segment, at `offset` σ from its mid. A hole is a
       *  hole: before any volume has traded under an anchor there is no value,
       *  and stroking across it would invent one. Off the visible range or off
       *  the price scale gets the same treatment — resume the path rather than
       *  drawing a chord across the gap. */
      const stroke = (seg: DsvSegment, offset: number) => {
        ctx.beginPath();
        let open = false;
        for (const pt of seg.points) {
          const v = offset === 0 ? pt.value : pt.value + offset * pt.sd;
          if (!Number.isFinite(v)) {
            open = false;
            continue;
          }
          const x = ts.timeToCoordinate(pt.time as Time);
          const y = this.c.series.priceToCoordinate(v);
          if (x == null || y == null) {
            open = false;
            continue;
          }
          if (open) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
          open = true;
        }
        ctx.stroke();
      };

      for (const seg of segs) {
        const rgb = seg.dir === 1 ? hue.bull : hue.bear;
        // Rings first and underneath, so a hundred envelopes never bury the mid
        // lines they belong to. Dash and weight follow the live envelope's
        // convention: ±2σ solid, ±1σ and ±3σ dashed, all of them hairlines.
        ctx.lineWidth = 1;
        for (let k = 1; k <= bands; k++) {
          ctx.strokeStyle = `rgba(${rgb}, ${alpha * (RING_RATIO[k] ?? 0.4)})`;
          ctx.setLineDash(k === 2 ? [] : [3, 3]);
          stroke(seg, k);
          stroke(seg, -k);
        }
        ctx.setLineDash([]);
        ctx.lineWidth = mid;
        ctx.strokeStyle = `rgba(${rgb}, ${alpha})`;
        stroke(seg, 0);
      }

      // Last and on top of the frozen ones, because it is the one you are being
      // asked to look at — but under the live line, which is what is actually
      // true right now.
      const sh = this.c.shadow();
      if (sh && sh.points.length > 1) {
        const rgb = sh.dir === 1 ? hue.bull : hue.bear;
        ctx.setLineDash(SHADOW_DASH);
        // Rings first and underneath, as on the frozen ones.
        ctx.lineWidth = 1;
        for (let k = 1; k <= this.c.shadowBands(); k++) {
          ctx.strokeStyle = `rgba(${rgb}, ${SHADOW_ALPHA * (RING_RATIO[k] ?? 0.4)})`;
          stroke(sh, k);
          stroke(sh, -k);
        }
        ctx.lineWidth = mid;
        ctx.strokeStyle = `rgba(${rgb}, ${SHADOW_ALPHA})`;
        stroke(sh, 0);
        ctx.setLineDash([]);
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
    // orders — a study layer should never cover what you are currently doing.
    return "normal" as const;
  }
}

export class DsvSegmentPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _segments: DsvSegment[] = [];
  private _ink = { bull: "33, 192, 122", bear: "245, 69, 95" };
  private _width = 2;
  private _bands = 0;
  private _alpha = DEFAULT_ALPHA;
  private _shadow: DsvSegment | null = null;
  private _shadowBands = 0;
  private _visible = false;

  /** Swap the whole set — the indicator is recomputed whole on each bar close,
   *  so there is never an append to make. */
  setData(segments: DsvSegment[]) {
    this._segments = segments;
    this.requestUpdate?.();
  }

  setInk(bull: string, bear: string) {
    this._ink = { bull, bear };
    this.requestUpdate?.();
  }

  /** Match whatever weight the live line is drawn at, so the only difference
   *  between current and superseded is the alpha. */
  setWidth(w: number) {
    this._width = w;
    this.requestUpdate?.();
  }

  /** How many σ rings each frozen segment draws — 0 for mids only. */
  setBands(n: number) {
    if (n === this._bands) return;
    this._bands = n;
    this.requestUpdate?.();
  }

  /** How lit the frozen mids are, 0–1. Their σ rings ride along at a fixed
   *  proportion of it, so the live leg stays the heaviest thing on the pane even
   *  at full — that is the line weight's job, not the alpha's. */
  setAlpha(a: number) {
    if (a === this._alpha) return;
    this._alpha = a;
    this.requestUpdate?.();
  }

  /** The pending segment to ghost in, or null to draw none, with the number of σ
   *  rings it draws. One call because the count means nothing without the
   *  segment, and the two can then never drift apart across a redraw. */
  setShadow(seg: DsvSegment | null, bands = 0) {
    this._shadow = seg;
    this._shadowBands = bands;
    this.requestUpdate?.();
  }

  setVisible(on: boolean) {
    if (on === this._visible) return;
    this._visible = on;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    this.views = [
      new View(
        new Renderer({
          chart: param.chart,
          series: param.series,
          segments: () => this._segments,
          ink: () => this._ink,
          width: () => this._width,
          bands: () => this._bands,
          alpha: () => this._alpha,
          shadow: () => this._shadow,
          shadowBands: () => this._shadowBands,
          visible: () => this._visible,
        }),
      ),
    ];
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
