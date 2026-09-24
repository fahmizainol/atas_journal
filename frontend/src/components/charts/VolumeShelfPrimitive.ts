// Volume shelves, drawn in two readings of the same data (see lib/volumeShelf).
//
//   the raster    every window's size-per-visit, as a price x time field under
//                 the candles. One hue, opacity carrying the z-score. NO
//                 threshold anywhere in it — it is the honest picture, and it is
//                 what lets a box be judged rather than believed.
//
//   the boxes     the bands that cleared `zMin` and held long enough, over the
//                 candles, with their score.
//
// Both, rather than either, and that is the point. A box alone cannot be
// checked: a band that just missed the threshold and a stretch of chart where
// nothing happened look identical — empty. Every level-geometry study in this
// repo has come back null, so a layer that shows only its own conclusions is the
// last thing this chart needs. The raster is always on when the layer is on; the
// boxes can be turned off.
//
// The raster draws one of two fields (`ShelfField`). `size` is the reading above;
// `flow` swaps in how one-sidedly each row traded, on a diverging ramp, leaving
// the boxes where they are. That pairing is the point of having it — a box says
// size concentrated here, and the flow field under it says which side put it
// there. Neither answers that alone, and one field at a time rather than a tint
// on the other because two quantities in one mark are read as their product: the
// volume profile tried tinting its rows by delta and the lane it has now, in its
// own bars beside them, is what replaced it.
//
// Split across two z-orders for the reason RangeProfilePrimitive is: a single
// order cannot serve both halves. The raster must sit *under* the candles or it
// buries the price action it is describing, and the boxes must sit *over* them
// or their edges vanish into a dense cluster.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { ink } from "../../theme";
import type { ShelfBox } from "../../lib/volumeShelf";

/** One window's reading, kept with the bar it was taken at so the raster can be
 *  drawn as a field. `z` is per profile row and parallel to `rows`. */
export interface ShelfColumn {
  time: number;
  /** Row edges for this window's profile — the raster's vertical bins. */
  rows: { low: number; high: number }[];
  /** Standard deviations above the window's mean, NaN where nothing traded. */
  z: Float64Array;
  /** `delta / sqrt(volume)` per row — see `lib/volumeShelf.shelfFlow`. Absent
   *  when the tape carried no aggressor tag; NaN per row where that row had no
   *  reading, which is not the same as a row that traded evenly. */
  flow?: Float64Array;
}

/** Which quantity the raster draws. The boxes are unaffected either way: a shelf
 *  is defined by where size concentrated, and `flow` exists to ask *who built
 *  it* — a question that needs the box and the field together. */
export type ShelfField = "size" | "flow";

export interface ShelfData {
  columns: ShelfColumn[];
  boxes: ShelfBox[];
  /** Whether to draw the thresholded read on top. The raster does not switch —
   *  a shelf layer with no raster is a layer that cannot be checked. */
  showBoxes: boolean;
  /** Where the boxes' threshold sits, so the raster can be read against it. */
  zMin: number;
  field: ShelfField;
  /** Whether any column had flow at all. False draws nothing in `flow` — an
   *  untagged tape has no sides, and a blank field must not be mistaken for a
   *  balanced one. The legend is where that gets said. */
  flowAvailable: boolean;
}

/** Below this the cell is not painted at all. Not a second threshold on the
 *  reading — it is the alpha at which a wash stops being visible and starts
 *  being a tax on every frame. */
const FLOOR_Z = 0.5;
/** z at which the ramp reaches full strength. Fixed rather than scaled to each
 *  window's maximum: a per-window normalisation would make a quiet stretch and a
 *  violent one paint identically, which is exactly the comparison the raster
 *  exists to allow. */
const FULL_Z = 4;
const MAX_ALPHA = 0.5;

/** Where the flow field starts painting and where it saturates, in units of
 *  `delta / sqrt(volume)`.
 *
 *  Fixed, like the size field's, and for the same reason — but unlike the size
 *  field this quantity arrives with a scale attached, so the numbers are not
 *  taste. The scale is *measured*, not the theoretical one: split `v` contracts
 *  randomly between two sides and the statistic has spread 1 by construction,
 *  but on a real NQ session its spread is **1.90** — aggressor side comes in
 *  runs, so live tape is about twice as one-sided as an independent coin toss.
 *  A floor at the theoretical 1.0 therefore painted rows that were entirely
 *  consistent with live-tape chance (57% of relevant rows, mostly run-noise),
 *  which made the field look like a claim it was not making. The floor sits at
 *  the measured spread instead — one empirical sigma, rounded up — so a painted
 *  cell means the row is more one-sided than runs alone produce, and the
 *  ceiling at ~4 empirical sigma mirrors where the size field's ramp saturates.
 *  The median is 0.00 to two places, so the field is genuinely centred and a
 *  diverging ramp is the honest encoding. */
const FLOW_FLOOR = 2.0;
const FLOW_FULL = 8.0;

/** Opacity steps the ramp is quantised to.
 *
 *  Quantised so cells can be batched: one `fillStyle` and one path fill per step
 *  per frame instead of per cell, which is what makes a full-session raster
 *  affordable at all (see `RasterRenderer`). Sixteen steps over a 0.5 ceiling is
 *  an alpha increment of 0.033 — under the just-noticeable difference for a wash
 *  this faint over a dark surface, so the picture is the same one. */
const STEPS = 16;

/** Which opacity step a z-score paints at, or -1 for "do not paint". */
function stepFor(z: number): number {
  if (!Number.isFinite(z) || z < FLOOR_Z) return -1;
  const t = Math.min(1, (z - FLOOR_Z) / (FULL_Z - FLOOR_Z));
  return Math.round(t * (STEPS - 1));
}

/** The same, for the signed flow field. Magnitude picks the step; the sign picks
 *  the hue, and is read separately by the caller. */
function flowStep(d: number): number {
  if (!Number.isFinite(d)) return -1;
  const a = Math.abs(d);
  if (a < FLOW_FLOOR) return -1;
  const t = Math.min(1, (a - FLOW_FLOOR) / (FLOW_FULL - FLOW_FLOOR));
  return Math.round(t * (STEPS - 1));
}

const STEP_ALPHA = Array.from({ length: STEPS }, (_, i) =>
  ((i / (STEPS - 1)) * MAX_ALPHA).toFixed(3),
);

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: () => ShelfData;
}

/** Under the candles: the unthresholded field.
 *
 *  Drawn as sixteen paths rather than as cells, because a cell-per-call raster
 *  is quadratic in the thing it is describing and it showed. A session's worth of
 *  readings is a few hundred columns of a few hundred rows, and painting each
 *  surviving cell with its own `fillStyle` assignment and `fillRect` put ~36,000
 *  canvas calls on every frame of a playing replay — the layer worked and the
 *  chart dragged. Three things fix it, in descending order of how much they were
 *  worth:
 *
 *    cull to the viewport  a chart is normally zoomed into minutes, so nearly
 *                          every column is off-screen. Off-screen cells were
 *                          being composited all the same.
 *    merge vertical runs   the residual is smoothed, so neighbouring rows land on
 *                          the same opacity step and become one rectangle.
 *    batch by step         `fillStyle` is a parsed CSS string; assigning one per
 *                          cell was most of what remained after the culls.
 *
 *  None of it changes the picture: same ramp, same geometry, same rows. */
class RasterRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const { columns, field, flowAvailable } = this.c.data();
      if (columns.length === 0) return;
      // An untagged tape has no sides. Drawing a blank field would be indis-
      // tinguishable from drawing a perfectly balanced one, so it draws nothing
      // and the legend says why.
      const flow = field === "flow";
      if (flow && !flowAvailable) return;
      const { volShelf, profileDelta } = ink();
      const ts = this.c.chart.timeScale();
      const width: number = scope.mediaSize.width;
      const height: number = scope.mediaSize.height;

      // Column width from the neighbouring columns' spacing rather than from a
      // bar width: these charts are tick- and volume-bucketed, so bars are not
      // evenly spaced on the time scale and a fixed width leaves gaps in some
      // stretches and overlaps in others. Every column's x is resolved even
      // though most will be culled — the two beyond each edge are what give the
      // edge columns their true width.
      const xs = columns.map((c) => ts.timeToCoordinate(c.time as Time));
      // One path per opacity step, and in the flow field one per step *per
      // side* — the sign is a hue, so buy-led and sell-led cells cannot share a
      // fill however equal their strength.
      const paths: (Path2D | undefined)[] = new Array(STEPS);
      const pathsDown: (Path2D | undefined)[] = new Array(STEPS);

      for (let k = 0; k < columns.length; k++) {
        const x = xs[k];
        if (x == null) continue; // scrolled out of the data window
        const prev = k > 0 ? xs[k - 1] : null;
        const next = k + 1 < columns.length ? xs[k + 1] : null;
        const left = prev == null ? x - 1 : (x + prev) / 2;
        const right = next == null ? x + 1 : (x + next) / 2;
        if (right < 0 || left > width) continue;
        const w = Math.max(1, right - left);

        const col = columns[k];
        const src = flow ? col.flow : col.z;
        if (!src) continue;
        // The open run: one opacity step, one side, and the y span so far.
        let step = -1;
        let down = false;
        let top = 0;
        let bot = 0;
        const flush = () => {
          if (step < 0) return;
          const into = down ? pathsDown : paths;
          (into[step] ??= new Path2D()).rect(left, top, w, Math.max(1, bot - top));
          step = -1;
        };
        for (let i = 0; i < col.rows.length; i++) {
          const v = src[i];
          const s = flow ? flowStep(v) : stepFor(v);
          const neg = flow && v < 0;
          if (s < 0) {
            flush();
            continue;
          }
          const yHigh = this.c.series.priceToCoordinate(col.rows[i].high);
          const yLow = this.c.series.priceToCoordinate(col.rows[i].low);
          if (yHigh == null || yLow == null || yLow < 0 || yHigh > height) {
            flush();
            continue;
          }
          // Rows arrive contiguous in price, so an adjacent row at the same step
          // extends the run — whichever way round the price axis is drawn. The
          // 1.5px slack is for the rounding between two rows' coordinates, not a
          // tolerance for gaps: a real gap is a row that failed the step test and
          // has already flushed.
          if (step === s && down === neg && yLow >= top - 1.5 && yHigh <= bot + 1.5) {
            if (yHigh < top) top = yHigh;
            if (yLow > bot) bot = yLow;
            continue;
          }
          flush();
          step = s;
          down = neg;
          top = yHigh;
          bot = yLow;
        }
        flush();
      }

      // No gap between rows, unlike the profile histograms: this is a field, and
      // a hairline of surface between every cell reads as banding in the data
      // rather than as separation.
      //
      // The flow field borrows the delta lane's own two colours, so a row that
      // reads buy-led here reads buy-led in the lane beside the profile.
      const up = flow ? profileDelta.up : volShelf.ramp;
      for (let s = 0; s < STEPS; s++) {
        const p = paths[s];
        if (p) {
          ctx.fillStyle = `rgba(${up}, ${STEP_ALPHA[s]})`;
          ctx.fill(p);
        }
        const q = pathsDown[s];
        if (q) {
          ctx.fillStyle = `rgba(${profileDelta.down}, ${STEP_ALPHA[s]})`;
          ctx.fill(q);
        }
      }
    });
  }
}

/** Over the candles: the thresholded read. */
class BoxRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const { boxes, showBoxes } = this.c.data();
      if (!showBoxes || boxes.length === 0) return;
      const { volShelf, chip } = ink();
      const ts = this.c.chart.timeScale();

      ctx.save();
      ctx.lineWidth = 1;
      ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textBaseline = "middle";

      for (const b of boxes) {
        const x1 = ts.timeToCoordinate(b.from as Time);
        const x2 = ts.timeToCoordinate(b.to as Time);
        const yHigh = this.c.series.priceToCoordinate(b.hi);
        const yLow = this.c.series.priceToCoordinate(b.lo);
        if (x1 == null || x2 == null || yHigh == null || yLow == null) continue;

        const w = Math.max(1, x2 - x1);
        const h = Math.max(1, yLow - yHigh);
        ctx.strokeStyle = b.live ? volShelf.boxLive : volShelf.box;
        // Dashed on a closed shelf, solid while it is still being detected — the
        // convention the fixed-range profile already uses for a latched right
        // edge, so "still moving" reads the same on both tools.
        ctx.setLineDash(b.live ? [] : [4, 3]);
        ctx.strokeRect(x1 + 0.5, yHigh + 0.5, w, h);

        // The score, on the left edge where the shelf began. Suppressed when the
        // box is too short to hold it — a label wider than the thing it labels
        // stops being a reading and becomes clutter.
        const label = `z${b.z.toFixed(1)}`;
        const tw = ctx.measureText(label).width;
        if (w < tw + 8) continue;
        const ty = yHigh + h / 2;
        ctx.fillStyle = chip.bg;
        ctx.fillRect(x1 + 2, ty - 6, tw + 4, 12);
        ctx.fillStyle = b.live ? volShelf.boxLive : volShelf.box;
        ctx.fillText(label, x1 + 4, ty);
      }
      ctx.restore();
    });
  }
}

class View {
  private _r: RasterRenderer | BoxRenderer;
  constructor(
    c: Ctx,
    private _z: "bottom" | "top",
  ) {
    this._r = _z === "bottom" ? new RasterRenderer(c) : new BoxRenderer(c);
  }
  update() {}
  renderer() {
    return this._r;
  }
  zOrder() {
    return this._z;
  }
}

const EMPTY: ShelfData = {
  columns: [],
  boxes: [],
  showBoxes: true,
  zMin: 2,
  field: "size",
  flowAvailable: false,
};

export class VolumeShelfPrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private data: ShelfData = EMPTY;

  setData(data: ShelfData) {
    this.data = data;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      data: () => this.data,
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
