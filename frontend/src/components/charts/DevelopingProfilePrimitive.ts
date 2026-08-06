// The developing NY profile: today's volume-at-price from the bell to the
// clock, as a histogram that grows while the replay runs.
//
// The chart already draws this session's VAH/POC/VAL as developing lines. Those
// say where value *is*; this says what the distribution getting there looks
// like — whether the session has built one shelf or two, how thin the ice is
// above the POC, which prices it has refused. That is the picture the demo
// page's live view puts next to the frozen composite
// (demo/_composite_profile_template.html), and it is the half a line can't
// carry.
//
// It has its own gutter, to the left of the viewport profile's. Two histograms
// on one price axis answering different questions — "the bars you can see" and
// "the session so far" — have to be tellable apart, and the cheapest way is to
// stop them overlapping. Hue does the rest: violet here, blue/gold there.
//
// Developing means developing. Every row is volume that has already printed by
// the current clock, so rewinding shrinks it and there is no way for a price
// the session hasn't reached yet to appear in it.
//
// It names its own HVN/LVN nodes, at the same prominence the composite's are
// read at — the shelves *today* has built, against the shelves the days behind
// it built. Those lines are drawn only from the bell rightward, because that is
// the stretch of tape they were measured over: a developing node has no meaning
// over bars that printed before the distribution existed. (The composite's run
// the full pane for the opposite reason — it is frozen, so it applies
// everywhere, and the two are told apart by where they start as much as by hue.)

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { palette } from "../../theme";
import type { ProfileNodes, VolumeProfile } from "../../lib/volumeProfile";
import { drawEventMarginal } from "../../lib/eventMarginal";
import type { TapeEvent } from "../../lib/replayEngine";

/** Fraction of the pane this gutter spans, and how far its baseline sits in
 *  from the right edge — the viewport profile's own width, plus a gap, so the
 *  two sit side by side. Fixed rather than reflowed when that one is hidden: a
 *  gutter that moves when you toggle an unrelated layer is worse than a gap. */
const WIDTH_FRAC = 0.11;
const INSET_FRAC = 0.11;
const GAP_PX = 7;
const GAP = 1; // px between rows, so they read as a histogram not a block

// Violet, the demo page's own profile colour — and distinct from both marginal
// hues (orange sweeps, fuchsia absorption) that get drawn on top of it, and from
// the viewport profile's blue and gold beside it.
const FILL_VA = "rgba(139, 92, 246, 0.46)";
const FILL_OUT = "rgba(139, 92, 246, 0.16)";
const FILL_POC = "rgba(196, 181, 253, 0.80)";

// The nodes stay inside that violet family — they are this histogram's reading,
// not a fourth layer — and split warm/cool the way the composite's do: the pale
// violet is a price the session kept coming back to, the indigo one it passed
// through.
const NODE_HVN = "#c4b5fd";
const NODE_LVN = "#818cf8";
const CHIP = "rgba(14, 17, 23, 0.82)";
/** Vertical room a label needs to itself, in px. */
const LABEL_H = 13;
/** Most node labels drawn before the reading becomes a wall of text. Every node
 *  still gets its line — only the naming is capped. */
const MAX_NODE_LABELS = 6;
/** Left margin the node chips stay out of, in px — the column the composite
 *  labels its value levels in. */
const LEFT_KEEPOUT = 120;

/** The value area as the *engine* has it, at tick resolution — the same numbers
 *  the developing VAH/POC/VAL lines are drawn from.
 *
 *  The histogram cannot be trusted to re-derive it. Binning to readable rows
 *  changes the value-area walk (it annexes a pair of rows at a time, so a 0.5pt
 *  row means it steps a point where the engine steps half of one), and measured
 *  on a real session that moves VAH by ~12pt while POC and VAL land identically.
 *  Shading rows by a number the lines beside them disagree with would be a bug
 *  you could see. So: the shape is the histogram's, the levels are the engine's,
 *  and the two are one distribution again. */
export interface DevelopingVa {
  poc: number;
  vah: number;
  val: number;
}

export interface DevelopingData {
  profile: VolumeProfile;
  /** The engine's value area, or null before it has one. */
  va: DevelopingVa | null;
  /** Null when the node reader is switched off. */
  nodes: ProfileNodes | null;
  /** Bar time of the session's first NY bar — where the node lines start, since
   *  that is where the distribution they were read off starts. */
  from: number;
}

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: () => DevelopingData | null;
  events: () => TapeEvent[];
  visible: () => boolean;
  nodesOn: () => boolean;
}

// Under the candles: the histogram in its gutter.
class Renderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const profile = this.c.data()?.profile;
    if (!this.c.visible() || !profile || profile.maxVolume <= 0) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const series = this.c.series;
      const paneW = scope.mediaSize.width;
      const base = paneW - (paneW * INSET_FRAC + GAP_PX);
      const width = paneW * WIDTH_FRAC;
      if (base - width < 0) return; // pane too narrow to hold both gutters

      const va = this.c.data()?.va ?? null;
      const heaviest = profile.rows.reduce((a, b) => (b.volume > a.volume ? b : a));
      for (let i = 0; i < profile.rows.length; i++) {
        const row = profile.rows[i];
        if (row.volume <= 0) continue;
        const yHigh = series.priceToCoordinate(row.high);
        const yLow = series.priceToCoordinate(row.low);
        if (yHigh == null || yLow == null) continue;
        const w = (row.volume / profile.maxVolume) * width;
        // A row is the POC's if it contains the engine's POC price, and inside
        // value if it overlaps the engine's band at all — never by the row
        // index, which is a fact about this binning and not about the session.
        const isPoc = va
          ? va.poc >= row.low && va.poc < row.high
          : row === heaviest;
        const inVa = va ? row.high > va.val && row.low < va.vah : profile.valueArea.has(i);
        ctx.fillStyle = isPoc ? FILL_POC : inVa ? FILL_VA : FILL_OUT;
        ctx.fillRect(base - w, yHigh, w, Math.max(1, yLow - yHigh - GAP));
      }

      // The baseline the rows hang off, so the gutter reads as a panel rather
      // than as bars floating in the middle of the chart.
      ctx.fillStyle = palette.grid;
      ctx.fillRect(base, 0, 1, scope.mediaSize.height);

      // The events, against the same axis and the same width — which is the
      // whole reason this profile is worth drawing next to them: it is today's
      // distribution, so a burst that landed off the session's own shelf is
      // visible as a disagreement rather than having to be remembered.
      drawEventMarginal(
        ctx,
        (price) => series.priceToCoordinate(price),
        this.c.events(),
        profile.rows[0].low,
        profile.rows[profile.rows.length - 1].high,
        base,
        width,
        -1,
      );
    });
  }
}

// Over the candles: the nodes this distribution names, as prices — which is the
// only form in which they are any use, since the question they answer is what
// today's price is walking back into.
class NodeRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const d = this.c.data();
    if (!this.c.nodesOn() || !d?.nodes) return;
    const { hvn, lvn } = d.nodes;
    if (!hvn.length && !lvn.length) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const w = scope.mediaSize.width;
      // From the bell to the right-hand edge — the stretch the profile was
      // measured over. If the bell has been scrolled off the left of the
      // viewport the line simply starts at the edge; it is still that stretch,
      // just partly off-screen.
      const x0 = this.c.chart.timeScale().timeToCoordinate(d.from as Time);
      const from = x0 == null ? 0 : Math.max(0, x0);
      if (w - from < 4) return;

      ctx.font = "500 10px Inter, sans-serif";
      ctx.textBaseline = "middle";
      const taken: number[] = [];
      const room = (y: number) => taken.every((t) => Math.abs(t - y) >= LABEL_H);

      const line = (price: number, color: string, text: string | null, alpha: number) => {
        const y = this.c.series.priceToCoordinate(price);
        if (y == null) return;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.setLineDash([1, 2]);
        ctx.beginPath();
        ctx.moveTo(from, y + 0.5);
        ctx.lineTo(w, y + 0.5);
        ctx.stroke();
        ctx.restore();
        if (!text || !room(y)) return;
        taken.push(y);
        // At the left end of the line, where nothing else labels: the composite
        // puts its value levels hard left and its nodes hard right, and a third
        // set of chips in either column would be unreadable. Once the bell has
        // been scrolled off, the line starts at the pane edge — so the chips are
        // held clear of that same left column rather than landing on it.
        const bx = Math.max(from + 6, LEFT_KEEPOUT);
        const tw = ctx.measureText(text).width;
        if (bx + tw + 6 > w) return;
        ctx.fillStyle = CHIP;
        ctx.fillRect(bx - 3, y - 7, tw + 6, 14);
        ctx.fillStyle = color;
        ctx.fillText(text, bx, y);
      };

      let labels = 0;
      // Tallest humps first, so the named ones are the ones worth naming when
      // the pane runs out of room.
      for (const n of hvn) {
        const named = labels < MAX_NODE_LABELS;
        line(n.price, NODE_HVN, named ? `NY HVN ${n.price.toFixed(2)}` : null, 0.45 + 0.45 * n.height);
        if (named) labels++;
      }
      for (const n of lvn) {
        const named = labels < MAX_NODE_LABELS;
        // The thinner the trough, the more it is worth seeing.
        line(n.price, NODE_LVN, named ? `NY LVN ${n.price.toFixed(2)}` : null, 0.9 - 0.4 * n.depth);
        if (named) labels++;
      }
    });
  }
}

class View {
  private _r: Renderer | NodeRenderer;
  constructor(
    c: Ctx,
    private _z: "bottom" | "top",
  ) {
    this._r = _z === "bottom" ? new Renderer(c) : new NodeRenderer(c);
  }
  update() {}
  renderer() {
    return this._r;
  }
  // The histogram sits behind the candles, like the viewport profile: it is
  // context, not the subject. (Its gutter is off to the side anyway, where
  // little else draws.) The nodes are prices, so they go over the top.
  zOrder() {
    return this._z;
  }
}

export class DevelopingProfilePrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _data: DevelopingData | null = null;
  private _events: TapeEvent[] = [];
  private _visible = true;
  private _nodesOn = true;

  /** The session's profile as of the clock, with the levels and nodes read off
   *  it. Null before the bell — there is no NY session yet, and an empty gutter
   *  would be a claim that there is. */
  setData(data: DevelopingData | null) {
    this._data = data;
    this.requestUpdate?.();
  }

  /** Already filtered by the caller, same as every other event consumer. */
  setEvents(events: TapeEvent[]) {
    this._events = events;
    this.requestUpdate?.();
  }

  /** Two switches, like the composite's: the histogram in its gutter and the
   *  node lines over the candles are separately useful, and the second is the
   *  one that draws on the price action. */
  setVisible(on: boolean, nodesOn: boolean) {
    if (on === this._visible && nodesOn === this._nodesOn) return;
    this._visible = on;
    this._nodesOn = nodesOn;
    this.requestUpdate?.();
  }

  attached(param: any) {
    this.requestUpdate = param.requestUpdate;
    const ctx: Ctx = {
      chart: param.chart,
      series: param.series,
      data: () => this._data,
      events: () => this._events,
      visible: () => this._visible,
      nodesOn: () => this._nodesOn,
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
