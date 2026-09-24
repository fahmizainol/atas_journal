// The Dynamic Swing VWAP's anchor flags.
//
// A series primitive rather than the library's own markers, for the reason
// MarkerPrimitive, TradesPrimitive and ModernVwapPrimitive all give: this chart
// already carries custom primitives, and attaching one suppresses the built-in
// marker layer in lightweight-charts.
//
// Each flag marks a bar the line was *anchored at* — a swing low under a bullish
// leg, a swing high over a bearish one. That bar is in the past by the time the
// flip was found, so the flag is drawn with a stem out to the bar the flip was
// actually detected on: the stem's length is the indicator's lag.
//
// On this indicator that stem is not decoration. The line repaints back to the
// pivot (see lib/dynamicSwingVwap), so the segment is drawn as though it had
// always run from there and *nothing else on the pane records otherwise* — the
// stem is the only mark saying "this stretch was rewritten, and you were not
// looking at these prices while those bars printed". It is the reason the flags
// default to on.
//
// The HH/HL/LH/LL label is the structure read — each pivot against the last one
// of its own side. The first pivot of a side carries no label because it has
// nothing to be higher or lower than, and inventing one there would be the only
// dishonest pixel on the layer.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { DsvPivot } from "../../lib/dynamicSwingVwap";

/** How far off the pivot's own extreme the flag floats, in pixels. */
const OFFSET = 7;
const SIZE = 5;
const LABEL_FONT = "600 9px ui-sans-serif, system-ui, sans-serif";

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  pivots: () => DsvPivot[];
  /** The anchor a flip on the next bar would set, drawn hollow. Null when the
   *  shadow is off. */
  pending: () => DsvPivot | null;
  /** "labels" also writes HH/HL/LH/LL; "marks" draws the flag alone. */
  labels: () => boolean;
  ink: () => { bull: string; bear: string };
  visible: () => boolean;
}

class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    if (!this.c.visible()) return;
    const pending = this.c.pending();
    const pivots = this.c.pivots();
    if (!pivots.length && !pending) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const hue = this.c.ink();
      const withLabels = this.c.labels();
      ctx.font = LABEL_FONT;
      ctx.textAlign = "center";

      // The pending one draws with the confirmed ones and differs only in being
      // hollow — same shape, same side, same stem, so the eye reads "an anchor,
      // not yet real" rather than "a different kind of mark".
      for (const p of pending ? [...pivots, pending] : pivots) {
        const ghost = p === pending;
        const x = ts.timeToCoordinate(p.time as Time);
        if (x == null) continue;
        const y0 = this.c.series.priceToCoordinate(p.price);
        if (y0 == null) continue;
        const up = p.dir === 1;
        // Below the bar for a swing low, above for a swing high — the flag sits
        // on the side the pivot is an extreme of, so it never lands inside the
        // candle it belongs to.
        const y = up ? y0 + OFFSET : y0 - OFFSET;
        const rgb = up ? hue.bull : hue.bear;

        // The lag stem: back along the time axis to the bar the flip was seen
        // on. Drawn first and faint, so it reads as provenance rather than as a
        // level of its own.
        const xSeen = ts.timeToCoordinate(p.seenAt as Time);
        if (xSeen != null && xSeen > x + 1) {
          ctx.strokeStyle = `rgba(${rgb}, 0.35)`;
          ctx.lineWidth = 1;
          // The pending stem runs to *now*, not to a detection that has not
          // happened — dashed, so it reads as "so far" rather than a span.
          ctx.setLineDash(ghost ? [2, 3] : []);
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(xSeen, y);
          ctx.stroke();
          // A tick down at the far end, so the stem has an end rather than
          // trailing off into the candles.
          ctx.beginPath();
          ctx.moveTo(xSeen, y - 2);
          ctx.lineTo(xSeen, y + 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // The flag: a triangle pointing the way the new leg goes.
        ctx.fillStyle = `rgba(${rgb}, 0.9)`;
        ctx.strokeStyle = ghost ? `rgba(${rgb}, 0.75)` : `rgb(${rgb})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        const tip = up ? y - SIZE : y + SIZE;
        const bse = up ? y + SIZE : y - SIZE;
        ctx.moveTo(x, tip);
        ctx.lineTo(x - SIZE, bse);
        ctx.lineTo(x + SIZE, bse);
        ctx.closePath();
        if (!ghost) ctx.fill();
        ctx.stroke();

        if (withLabels && p.kind) {
          ctx.fillStyle = ghost ? `rgba(${rgb}, 0.75)` : `rgb(${rgb})`;
          ctx.textBaseline = up ? "top" : "bottom";
          ctx.fillText(p.kind, x, up ? y + SIZE + 3 : y - SIZE - 3);
        }
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

export class DsvFlagPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _pivots: DsvPivot[] = [];
  private _pending: DsvPivot | null = null;
  private _labels = true;
  private _ink = { bull: "33, 192, 122", bear: "245, 69, 95" };
  private _visible = false;

  /** Swap the whole set — the indicator is recomputed whole on each bar close,
   *  so there is never an append to make. */
  setData(pivots: DsvPivot[], labels: boolean, pending: DsvPivot | null = null) {
    this._pivots = pivots;
    this._pending = pending;
    this._labels = labels;
    this.requestUpdate?.();
  }

  setInk(bull: string, bear: string) {
    this._ink = { bull, bear };
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
          pivots: () => this._pivots,
          pending: () => this._pending,
          labels: () => this._labels,
          ink: () => this._ink,
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
