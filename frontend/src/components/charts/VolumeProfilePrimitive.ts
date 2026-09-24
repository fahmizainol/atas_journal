// Draws the volume profile as a horizontal histogram anchored to the right edge
// of the pane, growing leftward: one bar per price row, widest at the POC.
// lightweight-charts has no series type that runs along the price axis, so this
// is drawn by hand — same primitive shape as VwapBandPrimitive.
//
// Rows inside the value area are drawn solid, rows outside it faint, and the POC
// row gold, so VAH/POC/VAL are legible from the histogram alone; the horizontal
// lines and axis labels for those three prices are price lines owned by the
// chart (see CandlestickChart).
//
// Delta, when it is asked for, is a *second lane* to the left of the volume one
// rather than a colour on these rows: volume-at-price and net-delta-at-price are
// two distributions, and the interesting prices are the ones where they
// disagree — heavy volume that netted to nothing, a thin shelf that was bought
// one-way. A tint would have hidden exactly that, since one bar can only be one
// colour.
//
// How long those bars are is not this file's arithmetic: it draws whatever
// fractions `lib/deltaFlow` hands over, along with the marks for the rows that
// module flagged and the verdicts it reached. That split is what keeps the
// viewport histogram and the fixed-range tool from ever disagreeing about what
// a lane means, and it is why "the lane as it has always been drawn"
// (|delta| / maxAbsDelta) is a *mode* there rather than a formula here.

import type { ISeriesApi } from "lightweight-charts";
import { ink } from "../../theme";
import type { VolumeProfile } from "../../lib/volumeProfile";
import type { LaneReading } from "../../lib/deltaFlow";
import { VERDICT_GAP, drawDeltaRow, drawVerdict } from "../../lib/deltaLane";
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

// The delta lane, as a share of the volume lane's width, and the gutter between
// the two. Narrower than the volume lane on purpose: it is the second reading,
// and the profile's own shape is the one the pane is being given up for. The
// gutter is what makes them two lanes rather than one bar with a two-tone tail.
const DELTA_WIDTH_FRAC = 0.6;
const LANE_GUTTER = 3;

// Value area / tails / point of control. Read from the active ink at draw time
// rather than fixed here: on a light surface these three washes are re-cut
// (theme.ts), and a canvas that repaints every frame is the one place a
// recolour costs nothing to pick up.

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
      const { viewportProfile: vp, profileDelta } = ink();
      // The delta lane draws only when asked for *and* when the source had an
      // aggressor tag to read: an untagged tape gets the volume profile it has
      // rather than an empty second lane implying the flow was flat.
      const deltaOn = this.host.showDelta && profile.hasDelta && profile.maxAbsDelta > 0;
      // Its baseline sits a gutter to the left of the volume lane, and its bars
      // grow leftward from there — the same direction as the volume rows, so the
      // two lanes are read the same way round.
      const deltaBase = right - maxWidth - LANE_GUTTER;
      const deltaMax = maxWidth * DELTA_WIDTH_FRAC;
      // Lengths, flags and verdicts all come from one reading (lib/deltaFlow).
      // Its absence is not an error: a host that has never set one gets the lane
      // at its original scale, which is what `net` is.
      const lane = this.host.lane;
      const flagged = lane ? new Set(lane.flagged) : null;
      // The verdict column sits past the lane's full extent, so the longest bar
      // on the chart still can't reach it.
      const verdictX = deltaBase - deltaMax - VERDICT_GAP;

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
        ctx.fillStyle = row === pocRow ? vp.poc : profile.valueArea.has(i) ? vp.va : vp.out;
        ctx.fillRect(right - w, yHigh, w, h);

        if (!deltaOn || row.delta == null) continue;
        // A row whose sides cancelled exactly draws no bar — that is the reading,
        // and a 1px stub of some colour would be a direction it doesn't have. It
        // can still be *flagged*, and then the cap is the only mark it gets.
        const isFlagged = flagged?.has(i) ?? false;
        // Sign — and so colour — from the reading, not the row: a windowed or
        // visit lane's direction at a price is the window's, and can be the
        // opposite of the session number this row carries.
        const d = lane?.delta ? lane.delta[i] : row.delta;
        drawDeltaRow(
          ctx,
          d,
          lane ? lane.frac[i] : Math.abs(row.delta) / profile.maxAbsDelta,
          isFlagged,
          deltaBase,
          deltaMax,
          -1,
          yHigh,
          h,
          profileDelta,
          lane?.under ? { frac: lane.under.frac[i], delta: lane.under.delta[i] } : undefined,
        );
        const verdict = isFlagged ? lane?.verdict?.get(i) : undefined;
        if (verdict) drawVerdict(ctx, verdict, d, verdictX, yHigh, h, profileDelta);
      }

      // The delta lane's own baseline, so its bars have something to hang off and
      // an empty row reads as "nothing net here" rather than as a gap in a lane
      // you can no longer see the edge of.
      if (deltaOn) {
        ctx.fillStyle = vp.axis;
        ctx.fillRect(deltaBase, 0, 1, scope.mediaSize.height);
      }

      // A hairline along the histogram's baseline separates it from the price
      // scale and gives the rows something to sit against.
      ctx.fillStyle = vp.axis;
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

  /** Draw the net-delta lane beside the volume rows. A display choice and not a
   *  data one: the rows carry delta whenever the source had a tag to read, and
   *  the lane simply isn't drawn when they don't. */
  public showDelta = false;

  /** How that lane is read: bar lengths, which rows are flagged, and their
   *  verdicts (lib/deltaFlow). Null until a host sets one, which draws the lane
   *  at its original |delta| / maxAbsDelta scale — the same thing `net` means. */
  public lane: LaneReading | null = null;

  constructor(public profile: VolumeProfile | null) {}

  setShowDelta(on: boolean) {
    if (on === this.showDelta) return;
    this.showDelta = on;
    this.requestUpdate?.();
  }

  setLane(lane: LaneReading | null) {
    this.lane = lane;
    this.requestUpdate?.();
  }

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
