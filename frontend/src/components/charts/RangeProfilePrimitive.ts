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
import { ink, palette, type ChartInk } from "../../theme";
import type { VolumeProfile } from "../../lib/volumeProfile";
import type { ProfilePt } from "../../lib/replayEngine";
import type { LaneReading } from "../../lib/deltaFlow";
import { drawDeltaRow } from "../../lib/deltaLane";

export interface RangeProfileItem {
  id: number;
  /** Bar times (already snapped onto the bar grid) bounding the selection. */
  from: number;
  to: number;
  /** Null while the drag is still too narrow to have a traded range. */
  profile: VolumeProfile | null;
  /** How this slice's delta lane is read — scale and flags, computed with the
   *  profile it belongs to (lib/deltaFlow).
   *
   *  No verdicts here, deliberately, and it is not an omission to fill in later:
   *  a verdict asks what price did *after* a row filled, and this tool's right
   *  edge is wherever the drag was released. Classifying against it would report
   *  the shape of the selection rather than the behaviour of the market. The
   *  viewport histogram, whose right edge is the market's, is where that reading
   *  belongs. */
  lane?: LaneReading | null;
  /** Whether this selection's right edge is pinned to the live edge rather than
   *  to a bar the drag landed on. A latched profile keeps the left edge it was
   *  given and takes in each new bar as it prints, so it answers "the
   *  distribution since here, up to now". Drawn with a solid right edge so which
   *  profiles are still moving is visible without touching one. */
  live?: boolean;
  /** POC/VAH/VAL as they developed across the selection, one point per bar
   *  close — the same reading the developing VP gives the session, over the
   *  slice this tool was dragged out on. Empty while a drag is in flight (the
   *  span changes every pointer move, and the walk is per-bar), so a trace
   *  appearing on release is the tool settling, not a glitch. */
  path?: ProfilePt[];
}

// The box (indigo chrome around the selection) and the histogram inside it
// (the same blue / grey / gold the viewport profile uses, a touch stronger since
// this one is asked for rather than resident) both come off the active ink at
// draw time — the light surfaces re-cut them, and this canvas repaints every
// frame anyway. See theme.ts.
const GAP = 1;
/** Half-height of the grab handle drawn on a selected profile's edges. */
const GRIP_H = 14;
/** Fraction of the selection the widest (POC) row spans. Not the whole width:
 *  the histogram is a shape to read, and a POC bar reaching the far edge of a
 *  wide drag buries every candle inside the range it is measuring. Rows still
 *  grow from the selection's left edge, so which range they belong to is not in
 *  question. */
const ROW_SPAN = 0.42;
/** The delta lane's share of the row span, and the gutter between the two lanes
 *  — the viewport profile's proportions, so one chart's two histograms are the
 *  same instrument at two sizes (VolumeProfilePrimitive). */
const DELTA_WIDTH_FRAC = 0.6;
const LANE_GUTTER = 3;
/** How far under the headline levels the developing trace sits. Low enough that
 *  the three prices the tool reports read first, high enough that the path is
 *  followable across a dense candle cluster. */
const TRACE_ALPHA = 0.5;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  items: () => RangeProfileItem[];
  selected: () => number | null;
  /** Whether a net-delta lane is drawn beside the rows — the viewport profile's
   *  setting, since a chart reading one histogram for flow is reading both for
   *  it. Null when it is off, the ink triplets when it is on. */
  deltaInk: () => ChartInk["profileDelta"] | null;
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
      const { rangeBox: box, viewportProfile: vp } = ink();
      const delta = this.c.deltaInk();
      for (const d of this.c.items()) {
        const s = span(this.c, d);
        if (!s) continue;
        const { x1, x2 } = s;

        ctx.fillStyle = d.id === sel ? box.shadeSel : box.shade;
        ctx.fillRect(x1, 0, x2 - x1, scope.mediaSize.height);

        const p = d.profile;
        if (!p || p.maxVolume <= 0) continue;

        // Rows grow rightward from the selection's left edge — so the histogram
        // reads as belonging to the range it measures, the way TV's fixed-range
        // tool does — with the widest (POC) row spanning `ROW_SPAN` of it.
        const width = (x2 - x1) * ROW_SPAN;
        // The delta lane, mirrored: this tool's rows grow rightward, so its
        // second lane sits to the *right* of the first and grows the same way.
        // Same rule as the viewport profile otherwise (VolumeProfilePrimitive).
        const deltaOn = delta && p.hasDelta && p.maxAbsDelta > 0;
        const deltaBase = x1 + width + LANE_GUTTER;
        const deltaMax = width * DELTA_WIDTH_FRAC;
        const pocRow = p.rows.reduce((a, b) => (b.volume > a.volume ? b : a));
        const flagged = d.lane ? new Set(d.lane.flagged) : null;
        for (let i = 0; i < p.rows.length; i++) {
          const row = p.rows[i];
          if (row.volume <= 0) continue;
          const yHigh = this.c.series.priceToCoordinate(row.high);
          const yLow = this.c.series.priceToCoordinate(row.low);
          if (yHigh == null || yLow == null) continue;
          const h = Math.max(1, yLow - yHigh - GAP);
          ctx.fillStyle = row === pocRow ? vp.poc : p.valueArea.has(i) ? vp.va : vp.out;
          ctx.fillRect(x1, yHigh, (row.volume / p.maxVolume) * width, h);

          if (!deltaOn || row.delta == null) continue;
          drawDeltaRow(
            ctx,
            row.delta,
            d.lane ? d.lane.frac[i] : Math.abs(row.delta) / p.maxAbsDelta,
            flagged?.has(i) ?? false,
            deltaBase,
            deltaMax,
            1,
            yHigh,
            h,
            delta!,
          );
        }
        if (deltaOn) {
          ctx.fillStyle = vp.axis;
          ctx.fillRect(deltaBase, 0, 1, scope.mediaSize.height);
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
      const box = ink().rangeBox;
      ctx.font = "500 10px Inter, sans-serif";
      ctx.textBaseline = "middle";

      for (const d of this.c.items()) {
        const s = span(this.c, d);
        if (!s) continue;
        const { x1, x2 } = s;
        const on = d.id === sel;
        const h = scope.mediaSize.height;

        ctx.fillStyle = on ? box.edgeSel : box.edge;
        ctx.fillRect(x1, 0, on ? 2 : 1, h);
        // A latched right edge is drawn like a selected one whether or not the
        // profile is selected: it is the one edge on this chart that moves
        // without being dragged, and which profiles are still taking in bars
        // has to be answerable by looking rather than by poking at them.
        const rw = on || d.live ? 2 : 1;
        ctx.fillStyle = on || d.live ? box.edgeSel : box.edge;
        ctx.fillRect(x2 - rw, 0, rw, h);
        ctx.fillStyle = on ? box.edgeSel : box.edge;

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

        // How those three prices got where they are, drawn first so the levels
        // the tool is actually reporting stay the dominant marks. Stepped, not
        // interpolated: a value area is the state of a *closed* bar and holds
        // until the next one closes, so a diagonal between two points would draw
        // prices the profile never had. Same reason the developing VP steps.
        this.trace(ctx, d, x1, x2);

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
          ctx.fillStyle = ink().chip.bg;
          ctx.fillRect(bx - 3, y - 7, w + 6, 14);
          ctx.fillStyle = l.color;
          ctx.fillText(text, bx, y);
        }
      }
    });
  }

  /** The developing value area across one selection: three stepped paths, held
   *  under the headline levels by alpha rather than by colour so the trace and
   *  the line it ends at are recognisably the same reading. */
  private trace(ctx: CanvasRenderingContext2D, d: RangeProfileItem, x1: number, x2: number) {
    const path = d.path;
    if (!path || path.length < 2) return;
    const ts = this.c.chart.timeScale();

    // Resolved once for all three lines: the x of every bar close in the
    // selection, clamped into the box. A point whose bar is off the data window
    // has no coordinate, and breaks the path rather than being guessed at.
    const xs = path.map((pt) => {
      const x = ts.timeToCoordinate(pt.time as Time);
      return x == null ? null : Math.min(x2, Math.max(x1, x));
    });

    ctx.save();
    ctx.globalAlpha = TRACE_ALPHA;
    ctx.lineWidth = 1;
    for (const k of ["poc", "vah", "val"] as const) {
      ctx.strokeStyle = k === "poc" ? palette.gold : palette.blue;
      if (k !== "poc") ctx.setLineDash([4, 3]);
      else ctx.setLineDash([]);
      ctx.beginPath();
      let open = false;
      let prevY = 0;
      for (let i = 0; i < path.length; i++) {
        const x = xs[i];
        const y = this.c.series.priceToCoordinate(path[i][k]);
        if (x == null || y == null) {
          open = false;
          continue;
        }
        if (!open) {
          ctx.moveTo(x, y + 0.5);
          open = true;
        } else {
          // Along at the level the last closed bar set, then up or down onto
          // this one's — the step.
          ctx.lineTo(x, prevY + 0.5);
          ctx.lineTo(x, y + 0.5);
        }
        prevY = y;
        // The last bar's level holds to the box's edge, which is where the
        // headline line and its label sit.
        if (i === path.length - 1) ctx.lineTo(x2, y + 0.5);
      }
      ctx.stroke();
    }
    ctx.restore();
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
  private showDelta = false;

  // Called on every mousemove while dragging, so it must stay cheap: it only
  // swaps the data and asks for a repaint.
  setData(items: RangeProfileItem[], selected: number | null) {
    this.items = items;
    this.selected = selected;
    this.requestUpdate?.();
  }

  /** Follows the viewport profile's own setting — see `Ctx.deltaInk`. */
  setShowDelta(on: boolean) {
    if (on === this.showDelta) return;
    this.showDelta = on;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      items: () => this.items,
      selected: () => this.selected,
      // Read at draw time, like every other hue here: the light surfaces re-cut
      // the pair, and this canvas repaints every frame anyway.
      deltaInk: () => (this.showDelta ? ink().profileDelta : null),
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
