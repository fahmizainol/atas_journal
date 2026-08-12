// The Modern VWAP's signal marks and anchor ticks.
//
// A series primitive rather than the library's own markers, for the reason
// MarkerPrimitive and TradesPrimitive both give: this chart already carries
// custom primitives (the position overlay, the working orders, the band washes),
// and attaching one suppresses the built-in marker layer in lightweight-charts.
//
// Two encodings, kept in different channels so neither read depends on the
// other:
//
//   - *shape* is the rule that fired. MR (mean reversion, a close back inside
//     ±2σ) is a triangle; TC (trend continuation, a touch of the line reclaimed)
//     is a ring. They are opposite claims about the same tape and the eye should
//     not have to read a label to separate them.
//   - *fill* is the regime gate. A solid mark passed — it fired in the regime it
//     belongs to; a hollow, grey one was blocked. The blocked marks are drawn at
//     all because you cannot see what a gate is doing by looking only at what
//     survived it, which is the whole reason the demo page keeps them too.
//
// Anchor ticks are a separate, much quieter thing: a hairline under the bar
// where the accumulator reset. On a swing anchor that is the only way to see the
// construct working without inferring it from where the line steps.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import type { Bar } from "../../lib/replayEngine";
import type { MvSignal } from "../../lib/modernVwap";

/** Long green, short red — the signal is a direction, and these marks sit off
 *  the bar's high/low rather than over the body, so they never compete with a
 *  candle for the same pixels the way the trade marks would. */
const LONG_RGB = "34, 197, 94";
const SHORT_RGB = "244, 63, 94";
/** A blocked mark: grey, hollow, and thin. Present, not addressed to you. */
const BLOCKED_RGB = "100, 116, 139";
const ANCHOR_RGB = "148, 163, 184";

const SIZE = 5;
/** How far off the bar's extreme a mark floats, in pixels. */
const OFFSET = 9;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  signals: () => MvSignal[];
  anchors: () => number[];
  bars: () => Map<number, Bar>;
  visible: () => boolean;
}

class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    if (!this.c.visible()) return;
    const bars = this.c.bars();
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();

      // Anchor ticks first, so a signal mark on the same bar draws over them.
      ctx.lineWidth = 1;
      ctx.strokeStyle = `rgba(${ANCHOR_RGB}, 0.5)`;
      for (const t of this.c.anchors()) {
        const x = ts.timeToCoordinate(t as Time);
        const bar = bars.get(t);
        if (x == null || !bar) continue;
        const y = this.c.series.priceToCoordinate(bar.low);
        if (y == null) continue;
        ctx.beginPath();
        ctx.moveTo(x, y + 3);
        ctx.lineTo(x, y + 9);
        ctx.stroke();
      }

      for (const s of this.c.signals()) {
        const x = ts.timeToCoordinate(s.time as Time);
        const bar = bars.get(s.time);
        if (x == null || !bar) continue;
        const long = s.side === "long";
        // Below the bar for a long, above for a short — the mark points the way
        // the rule leans, from the side price would have to come off.
        const y0 = this.c.series.priceToCoordinate(long ? bar.low : bar.high);
        if (y0 == null) continue;
        const y = long ? y0 + OFFSET : y0 - OFFSET;
        const rgb = !s.gated ? BLOCKED_RGB : long ? LONG_RGB : SHORT_RGB;
        ctx.strokeStyle = `rgb(${rgb})`;
        ctx.fillStyle = `rgba(${rgb}, ${s.gated ? 0.9 : 0})`;
        ctx.lineWidth = s.gated ? 1.5 : 1;

        ctx.beginPath();
        if (s.kind === "MR") {
          // Triangle, apex toward the move the rule expects.
          const tip = long ? y - SIZE : y + SIZE;
          const base = long ? y + SIZE : y - SIZE;
          ctx.moveTo(x, tip);
          ctx.lineTo(x - SIZE, base);
          ctx.lineTo(x + SIZE, base);
          ctx.closePath();
        } else {
          ctx.arc(x, y, SIZE - 1, 0, Math.PI * 2);
        }
        if (s.gated) ctx.fill();
        ctx.stroke();
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

export class ModernVwapPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _signals: MvSignal[] = [];
  private _anchors: number[] = [];
  private _bars = new Map<number, Bar>();
  private _visible = false;

  /** Swap the whole set — the indicator is recomputed whole on each bar close
   *  (see ReplayChart.refreshMv), so there is never an append to make. */
  setData(signals: MvSignal[], anchors: number[], bars: Map<number, Bar>) {
    this._signals = signals;
    this._anchors = anchors;
    this._bars = bars;
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
          signals: () => this._signals,
          anchors: () => this._anchors,
          bars: () => this._bars,
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
