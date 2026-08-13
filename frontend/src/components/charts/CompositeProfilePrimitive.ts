// The multi-session composite profile: the volume-at-price of the sessions
// *before* this one, and the levels it names.
//
// Two halves, drawn in two z-layers for the same reason RangeProfilePrimitive
// splits: the histogram belongs to the stretch of chart it was measured over, so
// it is pinned under the context bars and stays there; the levels it produces —
// POC, VAH/VAL, and the HVN/LVN nodes — are prices, so they run the full width
// of the pane and over the candles, because a composite level is only worth
// drawing where today's price can reach it.
//
// It is frozen by construction rather than by rule. The composite is built from
// the history stretch alone, and the engine gives that stretch nothing (see
// `concatTapes`) — so today cannot feed the level today is being read against.
// That circularity is the one thing the demo (demo/composite_profile_demo.py)
// found impossible to guard against in a static view: let the current session
// into the composite and the POC drifts toward price, the level can never be
// violated, and every touch looks like a hold.
//
// Not a signal. Composite levels are the one volume-profile cell this suite has
// never null-checked, and every neighbouring one it has checked came back null
// (stable-level S/R, prior-day-POC magnet, LVN retrace, structure × node). This
// is a reading aid: it says where the days behind you traded, and nothing about
// what happens next.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { ink } from "../../theme";
import type { ProfileNodes, VolumeProfile } from "../../lib/volumeProfile";
import { drawEventMarginal } from "../../lib/eventMarginal";
import type { TapeEvent } from "../../lib/replayEngine";

export interface CompositeData {
  profile: VolumeProfile;
  /** Null when the node reader is switched off. */
  nodes: ProfileNodes | null;
  /** Bar times bounding the context stretch the composite was measured over. */
  from: number;
  to: number;
  /** How many prior sessions went into it, for the labels. */
  days: number;
}

/** The rose family, at the four weights this primitive draws it in, plus the
 *  chip plate under its labels. Derived per frame rather than fixed at module
 *  load: the light surfaces re-cut the family (theme.ts), and a `const` computed
 *  at import time would hold the dark one for the life of the tab. */
function hues() {
  const { composite: c, chip } = ink();
  return {
    poc: c.poc,
    edge: c.edge,
    hvn: c.hvn,
    lvn: c.lvn,
    rowVa: `rgba(${c.fill}, 0.42)`,
    rowOut: `rgba(${c.fill}, 0.16)`,
    rowPoc: `rgba(${c.fill}, 0.85)`,
    shade: `rgba(${c.fill}, 0.05)`,
    chip: chip.bg,
  };
}
const GAP = 1;
/** Fraction of the context stretch the widest (POC) row spans — the same rule
 *  the fixed-range tool follows, since this is that tool with the range set for
 *  you. */
const ROW_SPAN = 0.42;
/** Vertical room a label needs to itself, in px. */
const LABEL_H = 13;
/** Most node labels drawn before it stops being a reading and starts being a
 *  wall of text. The nodes themselves all draw — only the naming is capped. */
const MAX_NODE_LABELS = 8;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: () => CompositeData | null;
  events: () => TapeEvent[];
  profileOn: () => boolean;
  nodesOn: () => boolean;
}

// Pixel span of the context stretch. Null when either edge can't be resolved —
// the history is scrolled off, and there is nowhere to hang the histogram. The
// levels are drawn either way.
function span(c: Ctx, d: CompositeData): { x1: number; x2: number } | null {
  const ts = c.chart.timeScale();
  const a = ts.timeToCoordinate(d.from as Time);
  const b = ts.timeToCoordinate(d.to as Time);
  if (a == null || b == null) return null;
  const x1 = Math.min(a, b);
  const x2 = Math.max(a, b);
  return x2 - x1 < 4 ? null : { x1, x2 };
}

// Under the candles: the histogram, pinned to the days it was measured over.
class FillRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const d = this.c.data();
    if (!d || !this.c.profileOn()) return;
    const p = d.profile;
    if (p.maxVolume <= 0) return;
    const s = span(this.c, d);
    if (!s) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const { x1, x2 } = s;
      const h = hues();
      // The shade marks the days the composite was measured over, so it covers
      // the whole stretch; the histogram over it is the narrower thing, because
      // several days of context bars is a very wide box to run a POC row across.
      const width = x2 - x1;
      const histW = width * ROW_SPAN;
      ctx.fillStyle = h.shade;
      ctx.fillRect(x1, 0, width, scope.mediaSize.height);

      // Rows grow rightward from the left edge of the context stretch — the
      // fixed-range tool's convention, so a composite reads like any other
      // profile on this chart.
      let pocIdx = 0;
      for (let i = 1; i < p.rows.length; i++) {
        if (p.rows[i].volume > p.rows[pocIdx].volume) pocIdx = i;
      }
      for (let i = 0; i < p.rows.length; i++) {
        const row = p.rows[i];
        if (row.volume <= 0) continue;
        const yHigh = this.c.series.priceToCoordinate(row.high);
        const yLow = this.c.series.priceToCoordinate(row.low);
        if (yHigh == null || yLow == null) continue;
        ctx.fillStyle = i === pocIdx ? h.rowPoc : p.valueArea.has(i) ? h.rowVa : h.rowOut;
        ctx.fillRect(x1, yHigh, (row.volume / p.maxVolume) * histW, Math.max(1, yLow - yHigh - GAP));
      }

      // Today's events as a distribution over the days that built the level —
      // measured off this histogram's own baseline and width, so the outline and
      // the bars are on the same scale. This is the comparison the demo page
      // exists to make, and the one where a disagreement is the interesting
      // case: agreement is what any subset of the tape would show.
      drawEventMarginal(
        ctx,
        (price) => this.c.series.priceToCoordinate(price),
        this.c.events(),
        p.rows[0].low,
        p.rows[p.rows.length - 1].high,
        x1,
        histW,
        1,
      );
    });
  }
}

// Over the candles: the levels. Full width, because that is the only place a
// frozen level is any use — the question a composite answers is what today's
// price is walking into.
class OverlayRenderer {
  constructor(private c: Ctx) {}

  draw(target: any) {
    const d = this.c.data();
    if (!d) return;
    const profileOn = this.c.profileOn();
    const nodesOn = this.c.nodesOn();
    if (!profileOn && !nodesOn) return;
    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const w = scope.mediaSize.width;
      const h = hues();
      ctx.font = "500 10px Inter, sans-serif";
      ctx.textBaseline = "middle";

      // Every label keeps its own strip of the pane: the three value levels
      // claim theirs first (they are the reading), then as many nodes as fit.
      const taken: number[] = [];
      const room = (y: number) => taken.every((t) => Math.abs(t - y) >= LABEL_H);

      const line = (
        price: number,
        color: string,
        dash: number[] | null,
        text: string | null,
        alpha = 1,
        right = false,
      ) => {
        const y = this.c.series.priceToCoordinate(price);
        if (y == null) return;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        if (dash) ctx.setLineDash(dash);
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(w, y + 0.5);
        ctx.stroke();
        ctx.restore();
        if (!text || !room(y)) return;
        taken.push(y);
        const tw = ctx.measureText(text).width;
        // Value levels label on the left, nodes on the right: they are read
        // together and would otherwise stack on the same few pixels.
        const bx = right ? w - tw - 12 : 10;
        ctx.fillStyle = h.chip;
        ctx.fillRect(bx - 3, y - 7, tw + 6, 14);
        ctx.fillStyle = color;
        ctx.fillText(text, bx, y);
      };

      if (profileOn) {
        const p = d.profile;
        const dn = `${d.days}d`;
        line(p.poc, h.poc, null, `C-POC ${p.poc.toFixed(2)} · ${dn}`);
        line(p.vah, h.edge, [4, 3], `C-VAH ${p.vah.toFixed(2)}`);
        line(p.val, h.edge, [4, 3], `C-VAL ${p.val.toFixed(2)}`);
      }
      if (nodesOn && d.nodes) {
        let labels = 0;
        // Tallest humps first, so the ones that get named are the ones worth
        // naming when the pane runs out of room.
        for (const n of d.nodes.hvn) {
          const named = labels < MAX_NODE_LABELS;
          line(
            n.price,
            h.hvn,
            [1, 2],
            named ? `HVN ${n.price.toFixed(2)}` : null,
            0.45 + 0.45 * n.height,
            true,
          );
          if (named) labels++;
        }
        for (const n of d.nodes.lvn) {
          const named = labels < MAX_NODE_LABELS;
          line(
            n.price,
            h.lvn,
            [1, 2],
            named ? `LVN ${n.price.toFixed(2)}` : null,
            // The thinner the trough, the more it is worth seeing.
            0.9 - 0.4 * n.depth,
            true,
          );
          if (named) labels++;
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

export class CompositeProfilePrimitive {
  private views: View[] = [];
  private requestUpdate?: () => void;
  private _data: CompositeData | null = null;
  private _events: TapeEvent[] = [];
  private _profileOn = true;
  private _nodesOn = true;

  /** The composite, or null when there is nothing to draw (no context days, or
   *  the rule is off). Rebuilt only when the days in front of the session change
   *  — it is frozen, so a playback step can never move it. */
  setData(data: CompositeData | null) {
    this._data = data;
    this.requestUpdate?.();
  }

  /** Tape events for the marginal over the histogram. Already filtered by the
   *  caller (strength floor, per-kind toggles) — this only draws. */
  setEvents(events: TapeEvent[]) {
    this._events = events;
    this.requestUpdate?.();
  }

  setVisible(profileOn: boolean, nodesOn: boolean) {
    if (profileOn === this._profileOn && nodesOn === this._nodesOn) return;
    this._profileOn = profileOn;
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
      profileOn: () => this._profileOn,
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
