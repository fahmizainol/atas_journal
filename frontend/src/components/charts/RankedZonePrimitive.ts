// The ranked support/resistance zones, on the canvas (see lib/rankedZones for
// what they are and how they are scored).
//
// Three rectangles per drawn zone and nothing else: the zone body, and inside
// it the two halves of a strength bar growing from the left edge. The source
// also carries an invisible centre line — `color.new(c, 100)` is fully
// transparent, and it only becomes visible when the zone breaks — so what is
// drawn here for a broken zone is that line's dotted remains, not a fourth
// rectangle.
//
// Only the *visible* zones are drawn. The source keeps every stored zone's boxes
// alive and sets the ones past the cut fully transparent, which it has to —
// Pine cannot cheaply destroy and recreate drawing objects. Here they are simply
// skipped, which is the same picture for a fraction of the work: `storedLimit`
// defaults to 60 and `visibleLimit` to 8.
//
// One pane view, not two. The zones belong *under* the candles — they are the
// background a bar is read against, and a wash drawn over price would hide the
// thing it is context for.

import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { ink } from "../../theme";
import type { RankedZone, RankedZonesData, RankedZonesParams } from "../../lib/rankedZones";

/** How far past the last bar a live zone's box runs, in bars. The source uses
 *  25 and it is a good number for a reason: far enough that the box reads as
 *  "still in force" rather than ending at price, short enough to leave the
 *  right-hand gutter clear. */
const EXTEND_BARS = 25;

/** The strength bar stops short of the box's right edge, so the zone text has
 *  somewhere to sit and the bar never reads as a full-width fill. The source's
 *  0.82. */
const BAR_SPAN = 0.82;

/** Below this many pixels a zone box is a line, and the text and strength bars
 *  inside it are illegible noise. Drawn as a plain band instead. */
const MIN_H = 7;
/** And below this wide, skipped outright — a zone whose left edge has scrolled
 *  almost to the right edge of the pane. */
const MIN_W = 12;

const FONT = "600 10px Inter, sans-serif";
const TEXT_FONT = "500 10px Inter, sans-serif";

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: () => RankedZonesData | null;
  params: () => RankedZonesParams | null;
  lastTime: () => number;
  visible: () => boolean;
}

class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    if (!this.c.visible()) return;
    const data = this.c.data();
    const p = this.c.params();
    if (!data || !p) return;
    const drawn = data.zones.filter((z) => z.visible);
    if (!drawn.length && !data.broken.length) return;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.c.chart.timeScale();
      const paneW = scope.mediaSize.width;
      const rz = ink().rankedZones;

      // Where a live zone's box stops. Everything still in force shares this
      // edge, which is what makes the stack read as one set of levels rather
      // than a ragged pile — a zone born an hour ago and one born a minute ago
      // are equally in force, and ending each at its own birth+25 would say
      // otherwise.
      const lastX = ts.timeToCoordinate(this.c.lastTime() as any);
      const right =
        lastX == null
          ? paneW
          : Math.min(paneW, lastX + EXTEND_BARS * Math.max(1, ts.options().barSpacing));

      ctx.save();
      ctx.lineJoin = "miter";
      // Broken first, so a live zone overlapping one draws on top of it — the
      // live one is the reading, the broken one is the history behind it.
      for (const z of data.broken) this.zone(ctx, z, ts, right, paneW, p, rz, true);
      for (const z of drawn) this.zone(ctx, z, ts, right, paneW, p, rz, false);
      ctx.restore();
    });
  }

  private zone(
    ctx: CanvasRenderingContext2D,
    z: RankedZone,
    ts: any,
    liveRight: number,
    paneW: number,
    p: RankedZonesParams,
    rz: ReturnType<typeof ink>["rankedZones"],
    broken: boolean,
  ) {
    const xa = ts.timeToCoordinate(z.leftTime as any);
    if (xa == null) return;
    // A broken zone stops where it broke; a live one runs to the shared edge.
    const xb = broken && z.brokenTime != null ? ts.timeToCoordinate(z.brokenTime as any) : liveRight;
    if (xb == null) return;
    const x0 = Math.max(0, xa);
    const x1 = Math.min(paneW, xb);
    if (x1 <= x0 || x1 - x0 < MIN_W) return;

    const yTop = this.c.series.priceToCoordinate(z.top);
    const yBottom = this.c.series.priceToCoordinate(z.bottom);
    if (yTop == null || yBottom == null) return;
    const y = Math.round(Math.min(yTop, yBottom));
    const h = Math.max(1, Math.round(Math.abs(yBottom - yTop)));
    const w = Math.round(x1 - x0);
    const x = Math.round(x0);

    const support = z.dir === -1;
    const stroke = broken ? rz.broken : support ? rz.support : rz.resistance;
    const fill = broken ? rz.brokenFill : support ? rz.supportFill : rz.resistanceFill;

    ctx.fillStyle = fill;
    ctx.fillRect(x, y, w, h);

    if (broken) {
      // The source's one visible line: where the level was, dotted, stopping at
      // the break rather than extending.
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      const yMid = this.c.series.priceToCoordinate(z.mid);
      if (yMid != null) {
        ctx.beginPath();
        ctx.moveTo(x, Math.round(yMid) + 0.5);
        ctx.lineTo(x + w, Math.round(yMid) + 0.5);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      return;
    }

    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    ctx.strokeRect(x + 0.5, y + 0.5, w, h);

    // Too thin to hold anything. The band alone is still the honest reading —
    // the level is there, it is just narrow at this zoom.
    if (h < MIN_H) return;

    const mid = y + h / 2;
    if (p.strengthBars) {
      const span = w * BAR_SPAN;
      // Bear on top, bull below, both growing from the left edge — the two
      // halves are a comparison, and a shared baseline is what makes the longer
      // one readable at a glance.
      const bearW = Math.round((span * z.bearStrength) / 100);
      const bullW = Math.round((span * z.bullStrength) / 100);
      const halfH = Math.max(1, Math.floor(h / 2));
      ctx.fillStyle = rz.resistanceBar;
      ctx.fillRect(x, y, bearW, halfH);
      ctx.fillStyle = rz.supportBar;
      ctx.fillRect(x, Math.round(mid), bullW, halfH);

      if (h >= 16) {
        ctx.font = FONT;
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        ctx.fillStyle = rz.barText;
        // Inside its own bar when the bar is wide enough to hold the number,
        // just past the end of it when it isn't — a "4%" bar is four pixels
        // long and the label would be unreadable on top of it.
        const put = (v: number, barW: number, cy: number) => {
          const label = `${v}%`;
          const tw = ctx.measureText(label).width;
          ctx.fillText(label, barW > tw + 8 ? x + 4 : x + barW + 4, cy);
        };
        put(z.bearStrength, bearW, y + halfH / 2);
        put(z.bullStrength, bullW, mid + halfH / 2);
      }
    }

    if (p.zoneText && h >= 12) {
      ctx.font = TEXT_FONT;
      ctx.textBaseline = "middle";
      ctx.textAlign = "right";
      ctx.fillStyle = rz.text;
      ctx.fillText(z.label, x + w - 5, mid);
    }
  }
}

class View {
  private _r: Renderer;
  constructor(c: Ctx) {
    this._r = new Renderer(c);
  }
  update() {}
  renderer() {
    return this._r;
  }
  zOrder() {
    return "bottom" as const;
  }
}

export class RankedZonePrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _data: RankedZonesData | null = null;
  private _params: RankedZonesParams | null = null;
  private _lastTime = 0;
  private _visible = false;

  /** The computed zones, and the time of the last bar on the chart — which is
   *  where a live zone's box is measured from, not from anything in the data. */
  setData(data: RankedZonesData | null, lastTime: number) {
    this._data = data;
    this._lastTime = lastTime;
    this.requestUpdate?.();
  }

  setParams(p: RankedZonesParams) {
    this._params = p;
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
      data: () => this._data,
      params: () => this._params,
      lastTime: () => this._lastTime,
      visible: () => this._visible,
    };
    this.views = [new View(ctx)];
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
