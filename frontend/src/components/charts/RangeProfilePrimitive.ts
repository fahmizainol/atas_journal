// Fixed-range volume profiles: the TradingView tool where you drag across a slice
// of the chart and get the profile for exactly that slice, pinned to those bars
// instead of following the viewport (that's VolumeProfilePrimitive's job). Several
// can be on the chart at once, and each can be resized or moved after the fact —
// the hit-testing that drives that lives in CandlestickChart; this only draws.
//
// Drawn in two layers, because a single z-order can't serve both halves: the
// shading and the histogram go *under* the candles so they don't bury the price
// action, while the POC/VAH/VAL lines and their labels go *over* it so they stay
// readable against a dense candle cluster.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { palette } from "../../theme";
import type { VolumeProfile } from "../../lib/volumeProfile";

export interface RangeProfileItem {
  id: number;
  /** Bar times (already snapped onto the bar grid) bounding the selection. */
  from: number;
  to: number;
  /** Null while the drag is still too narrow to have a traded range. */
  profile: VolumeProfile | null;
}

const SHADE = "rgba(108, 92, 231, 0.10)";
const SHADE_SEL = "rgba(108, 92, 231, 0.16)";
const EDGE = "rgba(108, 92, 231, 0.55)";
const EDGE_SEL = "rgba(147, 130, 255, 0.95)";
const FILL_VA = "rgba(59, 130, 246, 0.45)";
const FILL_OUT = "rgba(138, 143, 156, 0.26)";
const FILL_POC = "rgba(224, 165, 42, 0.75)";
const GAP = 1;
/** Half-height of the grab handle drawn on a selected profile's edges. */
const GRIP_H = 14;
/** Fraction of the selection the widest (POC) row spans. Not the whole width:
 *  the histogram is a shape to read, and a POC bar reaching the far edge of a
 *  wide drag buries every candle inside the range it is measuring. Rows still
 *  grow from the selection's left edge, so which range they belong to is not in
 *  question. */
const ROW_SPAN = 0.42;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  items: () => RangeProfileItem[];
  selected: () => number | null;
}

// Pixel span of one selection. Null when either edge can't be resolved (scrolled
// out of the data window), which means there's nothing to draw.
function span(c: Ctx, d: RangeProfileItem): { x1: number; x2: number } | null {
  const ts = c.chart.timeScale();
  const a = ts.timeToCoordinate(d.from as Time);
  const b = ts.timeToCoordinate(d.to as Time);
  if (a == null || b == null) return null;
  return { x1: Math.min(a, b), x2: Math.max(a, b) };
}

// Under the candles: selection shading + the histograms.
class FillRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const sel = this.c.selected();
      for (const d of this.c.items()) {
        const s = span(this.c, d);
        if (!s) continue;
        const { x1, x2 } = s;

        ctx.fillStyle = d.id === sel ? SHADE_SEL : SHADE;
        ctx.fillRect(x1, 0, x2 - x1, scope.mediaSize.height);

        const p = d.profile;
        if (!p || p.maxVolume <= 0) continue;

        // Rows grow rightward from the selection's left edge — so the histogram
        // reads as belonging to the range it measures, the way TV's fixed-range
        // tool does — with the widest (POC) row spanning `ROW_SPAN` of it.
        const width = (x2 - x1) * ROW_SPAN;
        const pocRow = p.rows.reduce((a, b) => (b.volume > a.volume ? b : a));
        for (let i = 0; i < p.rows.length; i++) {
          const row = p.rows[i];
          if (row.volume <= 0) continue;
          const yHigh = this.c.series.priceToCoordinate(row.high);
          const yLow = this.c.series.priceToCoordinate(row.low);
          if (yHigh == null || yLow == null) continue;
          ctx.fillStyle = row === pocRow ? FILL_POC : p.valueArea.has(i) ? FILL_VA : FILL_OUT;
          ctx.fillRect(
            x1,
            yHigh,
            (row.volume / p.maxVolume) * width,
            Math.max(1, yLow - yHigh - GAP),
          );
        }
      }
    });
  }
}

// Over the candles: each selection's edges, its grab handles when selected, and
// the three prices the whole tool exists to report.
class OverlayRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const sel = this.c.selected();
      ctx.font = "500 10px Inter, sans-serif";
      ctx.textBaseline = "middle";

      for (const d of this.c.items()) {
        const s = span(this.c, d);
        if (!s) continue;
        const { x1, x2 } = s;
        const on = d.id === sel;
        const h = scope.mediaSize.height;

        ctx.fillStyle = on ? EDGE_SEL : EDGE;
        ctx.fillRect(x1, 0, on ? 2 : 1, h);
        ctx.fillRect(x2 - (on ? 2 : 1), 0, on ? 2 : 1, h);

        // Grips make it discoverable that the selected profile's edges can be
        // dragged; the hit zone is wider than what's drawn (see CandlestickChart).
        if (on) {
          const my = h / 2;
          for (const x of [x1, x2]) {
            ctx.fillRect(x - 2, my - GRIP_H / 2, 4, GRIP_H);
          }
        }

        const p = d.profile;
        if (!p) continue;

        const lines = [
          { price: p.poc, color: palette.gold, label: "POC", dashed: false },
          { price: p.vah, color: palette.blue, label: "VAH", dashed: true },
          { price: p.val, color: palette.blue, label: "VAL", dashed: true },
        ];

        for (const l of lines) {
          const y = this.c.series.priceToCoordinate(l.price);
          if (y == null) continue;

          ctx.save();
          ctx.strokeStyle = l.color;
          ctx.lineWidth = 1;
          if (l.dashed) ctx.setLineDash([4, 3]);
          ctx.beginPath();
          ctx.moveTo(x1, y + 0.5);
          ctx.lineTo(x2, y + 0.5);
          ctx.stroke();
          ctx.restore();

          // Label sits just inside the right edge, on a chip so it survives being
          // drawn over a candle body.
          const text = `${l.label} ${l.price.toFixed(2)}`;
          const w = ctx.measureText(text).width;
          const bx = x2 - w - 10;
          ctx.fillStyle = "rgba(14, 17, 23, 0.82)";
          ctx.fillRect(bx - 3, y - 7, w + 6, 14);
          ctx.fillStyle = l.color;
          ctx.fillText(text, bx, y);
        }
      }
    });
  }
}

class View {
  private _r: FillRenderer | OverlayRenderer;
  constructor(
    c: Ctx,
    private _z: "bottom" | "top",
  ) {
    this._r = _z === "bottom" ? new FillRenderer(c) : new OverlayRenderer(c);
  }
  update() {}
  renderer() {
    return this._r;
  }
  zOrder() {
    return this._z;
  }
}

export class RangeProfilePrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private items: RangeProfileItem[] = [];
  private selected: number | null = null;

  // Called on every mousemove while dragging, so it must stay cheap: it only
  // swaps the data and asks for a repaint.
  setData(items: RangeProfileItem[], selected: number | null) {
    this.items = items;
    this.selected = selected;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      items: () => this.items,
      selected: () => this.selected,
    };
    this.views = [new View(ctx, "bottom"), new View(ctx, "top")];
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
