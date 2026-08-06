// Draws the volume profile as a horizontal histogram anchored to the right edge
// of the pane, growing leftward: one bar per price row, widest at the POC.
// lightweight-charts has no series type that runs along the price axis, so this
// is drawn by hand — same primitive shape as VwapBandPrimitive.
//
// Rows inside the value area are drawn solid, rows outside it faint, and the POC
// row gold, so VAH/POC/VAL are legible from the histogram alone; the horizontal
// lines and axis labels for those three prices are price lines owned by the
// chart (see CandlestickChart).

import type { ISeriesApi } from "lightweight-charts";
import { palette } from "../../theme";
import type { VolumeProfile } from "../../lib/volumeProfile";
import { drawEventMarginal } from "../../lib/eventMarginal";
import type { TapeEvent } from "../../lib/replayEngine";

// Fraction of the pane the widest (POC) row spans. The shape is the reading —
// where the shelves are, not how long the bars get — so this is kept to the
// narrowest that still resolves a hump from its shoulder, and the price action
// keeps the rest of the pane. (The two range-pinned profiles run the same rule
// against their own span: see RangeProfilePrimitive and
// CompositeProfilePrimitive.)
const MAX_WIDTH_FRAC = 0.11;
const GAP = 1; // px between rows, so they read as a histogram not a block

const FILL_VA = "rgba(59, 130, 246, 0.42)"; // inside the value area
const FILL_OUT = "rgba(138, 143, 156, 0.22)"; // the tails
const FILL_POC = "rgba(224, 165, 42, 0.72)"; // point of control

class ProfileRenderer {
  constructor(
    private host: VolumeProfilePrimitive,
    private series: () => ISeriesApi<"Candlestick">,
  ) {}

  draw(target: any) {
    const profile = this.host.profile;
    if (!this.host.isVisible() || !profile || profile.maxVolume <= 0) return;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const series = this.series();
      const right = scope.mediaSize.width;
      const maxWidth = right * MAX_WIDTH_FRAC;

      // The POC row is the one whose price the chart also draws a gold line at;
      // recomputing it here (rather than storing an index) keeps the renderer
      // honest if the profile is swapped out mid-flight.
      const pocRow = profile.rows.reduce((a, b) => (b.volume > a.volume ? b : a));

      for (let i = 0; i < profile.rows.length; i++) {
        const row = profile.rows[i];
        if (row.volume <= 0) continue;
        const yHigh = series.priceToCoordinate(row.high);
        const yLow = series.priceToCoordinate(row.low);
        // Off-scale (the price scale is zoomed past this row) — nothing to draw.
        if (yHigh == null || yLow == null) continue;

        const h = Math.max(1, yLow - yHigh - GAP);
        const w = (row.volume / profile.maxVolume) * maxWidth;
        ctx.fillStyle =
          row === pocRow ? FILL_POC : profile.valueArea.has(i) ? FILL_VA : FILL_OUT;
        ctx.fillRect(right - w, yHigh, w, h);
      }

      // A hairline along the histogram's baseline separates it from the price
      // scale and gives the rows something to sit against.
      ctx.fillStyle = palette.grid;
      ctx.fillRect(right - 1, 0, 1, scope.mediaSize.height);

      // The event marginal, measured off this histogram's own baseline and
      // width so the outline and the bars can never drift apart. Empty on every
      // chart but the Simulator's, which is the only one with a tape to find
      // events on.
      drawEventMarginal(
        ctx,
        (price) => series.priceToCoordinate(price),
        this.host.events,
        profile.rows[0].low,
        profile.rows[profile.rows.length - 1].high,
        right,
        maxWidth,
        -1,
      );
    });
  }
}

class ProfilePaneView {
  private _renderer: ProfileRenderer;
  constructor(host: VolumeProfilePrimitive, series: () => ISeriesApi<"Candlestick">) {
    this._renderer = new ProfileRenderer(host, series);
  }
  update() {}
  renderer() {
    return this._renderer;
  }
  // Behind the candles: the profile is context, not the subject.
  zOrder() {
    return "bottom" as const;
  }
}

export class VolumeProfilePrimitive {
  private series!: ISeriesApi<"Candlestick">;
  private views: ProfilePaneView[] = [];
  private requestUpdate?: () => void;
  private visible = true;

  /** Tape events to draw as a marginal over the histogram. Already filtered by
   *  the caller (strength floor, per-kind toggles) — this only draws. */
  public events: TapeEvent[] = [];

  constructor(public profile: VolumeProfile | null) {}

  setEvents(events: TapeEvent[]) {
    this.events = events;
    this.requestUpdate?.();
  }

  // The profile is recomputed as the user pans/zooms (it covers the visible
  // bars), so unlike the VWAP band this primitive's data is mutable.
  setProfile(profile: VolumeProfile | null) {
    this.profile = profile;
    this.requestUpdate?.();
  }

  // A primitive has no `visible` option, so it culls itself in draw() and asks
  // for a repaint — same trick as VwapBandPrimitive.
  setVisible(v: boolean) {
    this.visible = v;
    this.requestUpdate?.();
  }

  isVisible() {
    return this.visible;
  }

  attached(param: any) {
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.views = [new ProfilePaneView(this, () => this.series)];
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
