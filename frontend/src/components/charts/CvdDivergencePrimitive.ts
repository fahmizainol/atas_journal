// Draws price/CVD divergences on the CVD pane: a line joining the two swing
// points (A -> B) whose cumulative delta contradicted price, with a dot at each
// end and a small label. Attached to the CVD *line* series, so priceToCoordinate
// resolves in delta units and the segment lands on the CVD pane — the whole
// point of moving these off the price candles, where "where is it measured from"
// was invisible. The backend already paired the pivots (see
// api/sim_charts._cvd_divergences); this only draws.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { palette } from "../../theme";
import type { CvdDivergence } from "../../lib/chartTypes";

class Renderer {
  constructor(
    private divs: CvdDivergence[],
    private chart: IChartApi,
    private series: ISeriesApi<"Line">,
  ) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const ts = this.chart.timeScale();

      // Resolve every divergence to pane coordinates once. Bearish (price up,
      // delta down) reads red; bullish reads green — the ruler/candle convention.
      const items = [];
      for (const d of this.divs) {
        const x1 = ts.timeToCoordinate(d.t1 as Time);
        const x2 = ts.timeToCoordinate(d.t2 as Time);
        const y1 = this.series.priceToCoordinate(d.v1);
        const y2 = this.series.priceToCoordinate(d.v2);
        if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
        const bear = d.kind === "bear";
        items.push({ x1, y1, x2, y2, bear, color: bear ? palette.red : palette.green });
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
        const label = it.bear ? "bear" : "bull";
        const lx = it.x2 + 5;
        if (it.bear) {
          if (lx < lastAbove + PAD) continue;
          lastAbove = lx + ctx.measureText(label).width;
        } else {
          if (lx < lastBelow + PAD) continue;
          lastBelow = lx + ctx.measureText(label).width;
        }
        ctx.fillStyle = it.color;
        ctx.textBaseline = it.bear ? "bottom" : "top";
        ctx.fillText(label, lx, it.bear ? it.y2 - 4 : it.y2 + 4);
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

  constructor(private divs: CvdDivergence[]) {}

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    this.views = [new View(new Renderer(this.divs, param.chart, param.series))];
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
