// Draws a divergence as a segment joining the two swing points (A → B) that
// contradicted each other, with a dot at each end and a small label.
//
// Attached to whichever series owns the units the segment is measured in — which
// is the whole reason this takes raw `v1`/`v2` rather than a `CvdDivergence`. The
// cumulative-CVD pane hangs it off the delta line, so `priceToCoordinate`
// resolves in delta units and the segment lands where the reading was taken
// (moving these off the price candles is what made "where is this measured from"
// visible at all). The oscillator draws the *same* divergence twice — once on its
// own pane against the windowed delta, once over the candles against the pivot
// prices — and those are two different unit systems on two different series. One
// renderer, two attachments, no second copy of the collision logic.
//
// This only draws. The pairing is done elsewhere: by the backend for the
// cumulative line (api/session_chart._cvd_divergences), on the frontend for the
// oscillator (lib/cvdOsc), because the replay steps that one live.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { palette } from "../../theme";

/** One A→B mark in the host series' own units.
 *
 *  `kind` is the read, not the colour: bearish (price up, flow down) is red and
 *  labels above the B point, bullish is green and labels below. Both panes agree
 *  on that, which is why the same segment can be handed to either. */
export interface DivergenceSegment {
  kind: "bear" | "bull";
  t1: number;
  v1: number;
  t2: number;
  v2: number;
  /** What to print at B. The cumulative pane says `bear`/`bull`; the oscillator
   *  says `+RD`/`-RD`, its source script's own notation for a regular
   *  divergence. */
  label: string;
}

class Renderer {
  constructor(
    private host: { segs: DivergenceSegment[] },
    private chart: IChartApi,
    private series: ISeriesApi<"Line"> | ISeriesApi<"Histogram"> | ISeriesApi<"Candlestick">,
  ) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.chart.timeScale();

      // Resolve every divergence to pane coordinates once. Bearish (price up,
      // delta down) reads red; bullish reads green — the ruler/candle convention.
      const items = [];
      for (const d of this.host.segs) {
        if (!Number.isFinite(d.v1) || !Number.isFinite(d.v2)) continue;
        const x1 = ts.timeToCoordinate(d.t1 as Time);
        const x2 = ts.timeToCoordinate(d.t2 as Time);
        const y1 = this.series.priceToCoordinate(d.v1);
        const y2 = this.series.priceToCoordinate(d.v2);
        if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
        const bear = d.kind === "bear";
        items.push({
          x1,
          y1,
          x2,
          y2,
          bear,
          label: d.label,
          color: bear ? palette.red : palette.green,
        });
      }

      // Pass 1 — the A→B line and its endpoint dots, always drawn. The line's
      // slope IS the read: delta falling under a rising price (bear) or rising
      // under a falling price (bull). A is hollow (where it started), B filled
      // (where it confirmed), so direction reads even where a label is dropped.
      for (const it of items) {
        ctx.save();
        ctx.strokeStyle = it.color;
        ctx.fillStyle = it.color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(it.x1, it.y1);
        ctx.lineTo(it.x2, it.y2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(it.x2, it.y2, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(it.x1, it.y1, 3, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // Pass 2 — labels, left-to-right with per-side collision skipping so a
      // cluster of divergences doesn't overprint into an unreadable smear. The
      // line + dots already carry the mark; a dropped label costs nothing.
      ctx.font = "600 10px Inter, sans-serif";
      ctx.textAlign = "left";
      const PAD = 4;
      let lastAbove = -Infinity;
      let lastBelow = -Infinity;
      for (const it of [...items].sort((a, b) => a.x2 - b.x2)) {
        const lx = it.x2 + 5;
        if (it.bear) {
          if (lx < lastAbove + PAD) continue;
          lastAbove = lx + ctx.measureText(it.label).width;
        } else {
          if (lx < lastBelow + PAD) continue;
          lastBelow = lx + ctx.measureText(it.label).width;
        }
        ctx.fillStyle = it.color;
        ctx.textBaseline = it.bear ? "bottom" : "top";
        ctx.fillText(it.label, lx, it.bear ? it.y2 - 4 : it.y2 + 4);
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
    return "top" as const;
  }
}

export class CvdDivergencePrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;

  constructor(public segs: DivergenceSegment[] = []) {}

  /** Replace the marks. The journal charts set these once at mount, but the
   *  replay re-runs the pairing as the tape advances — a pivot is only a pivot
   *  once the bars to its right exist, so segments genuinely appear mid-session.
   *  The renderer reads through to `this`, so nothing is rebuilt. */
  setData(segs: DivergenceSegment[]) {
    this.segs = segs;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    this.views = [new View(new Renderer(this, param.chart, param.series))];
    this.requestUpdate?.();
  }

  detached() {
    this.views = [];
    this.requestUpdate = undefined;
  }

  updateAllViews() {
    this.views.forEach((v) => v.update());
  }

  paneViews() {
    return this.views;
  }
}
