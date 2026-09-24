// Dealer-gamma levels (api/routers/gex.py) as horizontal lines per session: the
// zero-gamma flip as a band between the NDX and QQQ books' flips, the call walls
// (C1.., resistance) and the put walls (P1.., support).
//
// Each session's levels run only across that session's own bars (Globex open →
// 17:00 ET), because each session has its own book: a wall carried across the
// whole chart would draw Monday's positioning over Friday's candles. A session
// the tape is still inside runs to the pane's right edge.
//
// A wall both books carry is solid; one only one book carries is dashed.
// Thickness is how much gamma rests there against that book's heaviest wall of
// the same kind — a bigger hedge to work through, not a claim that it holds.

import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import { ink } from "../../theme";

export interface GexWall {
  px: number;
  kind: "call" | "put";
  rank: number;
  weight: number;
  books: string[];
}

export interface GexBookRead {
  available: boolean;
  reason?: string;
  flip_px?: number | null;
  at_ref_b?: number;
  stamp?: string;
  stale_days?: number;
  exact_anchor?: boolean;
}

/** One stretch of a session drawn from one set of books: the open, or an
 *  intraday update from its publish stamp until the next. */
export interface GexStep {
  /** Wall-clock epoch seconds in the chart's zone — the bars' own axis. */
  from: number;
  to: number;
  flip_lo: number | null;
  flip_hi: number | null;
  walls: GexWall[];
  books: Record<string, GexBookRead>;
}

export interface GexSession {
  date: string;
  from: number;
  to: number;
  steps: GexStep[];
}

function lastLE(times: readonly { time: number }[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid].time <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

const bookTag = (books: string[]) =>
  books.map((b) => (b === "NDX" ? "N" : b === "QQQ" ? "Q" : b)).join("+");

class GexRenderer {
  constructor(private src: GexLevelsPrimitive) {}

  draw(target: any) {
    const { chart, series, sessions, times, visible, walls, flip } = this.src;
    if (
      !visible ||
      !chart ||
      !series ||
      times.length === 0 ||
      sessions.length === 0
    )
      return;
    const c = ink().gex;
    const ts = chart.timeScale();
    const firstT = times[0].time;
    const lastT = times[times.length - 1].time;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const w: number = scope.mediaSize.width;
      ctx.font = "10px Inter, sans-serif";
      ctx.textBaseline = "bottom";

      for (const s of sessions.flatMap((x) => x.steps)) {
        // Not reached yet, or over before the chart begins. A step published
        // after the last bar is simply not drawn — a replay never sees an
        // update ahead of its tape.
        if (s.from > lastT || s.to < firstT) continue;
        const i0 = s.from <= firstT ? 0 : lastLE(times, s.from - 1) + 1;
        const x0Raw =
          s.from <= firstT ? -1 : ts.timeToCoordinate(times[i0].time as Time);
        const x1Raw =
          s.to >= lastT
            ? w + 1
            : ts.timeToCoordinate(times[lastLE(times, s.to)].time as Time);
        if (x0Raw == null || x1Raw == null) continue;
        const x0 = Math.max(-1, x0Raw);
        const x1 = Math.min(w + 1, x1Raw);
        if (x1 - x0 < 2) continue;
        // Tags sit at the right end of the segment, or at the pane edge.
        const tagX = Math.min(x1, w) - 4;
        const tagYs: number[] = [];
        // A short step (an update soon replaced by the next) keeps its lines but
        // not its tags, which would pile onto the next step's.
        const tagsOn = x1 - x0 >= 60;
        const tag = (y: number, text: string) => {
          if (!tagsOn || tagYs.some((t) => Math.abs(t - y) < 11)) return;
          tagYs.push(y);
          ctx.fillStyle = c.label;
          ctx.textAlign = "right";
          ctx.fillText(text, tagX, y - 2);
        };

        if (flip && s.flip_lo != null && s.flip_hi != null) {
          const yA = series.priceToCoordinate(s.flip_hi);
          const yB = series.priceToCoordinate(s.flip_lo);
          if (yA != null && yB != null) {
            if (Math.abs(yB - yA) >= 1) {
              ctx.fillStyle = c.flipFill;
              ctx.fillRect(x0, yA, x1 - x0, yB - yA);
            }
            ctx.strokeStyle = c.flip;
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 3]);
            for (const y of Math.abs(yB - yA) >= 1 ? [yA, yB] : [yA]) {
              ctx.beginPath();
              ctx.moveTo(x0, Math.round(y) + 0.5);
              ctx.lineTo(x1, Math.round(y) + 0.5);
              ctx.stroke();
            }
            ctx.setLineDash([]);
            tag(Math.min(yA, yB), "γ flip");
          }
        }

        for (const wl of s.walls) {
          if (wl.rank >= walls) continue;
          const y = series.priceToCoordinate(wl.px);
          if (y == null) continue;
          const both = wl.books.length > 1;
          const call = wl.kind === "call";
          ctx.strokeStyle = call ? (both ? c.callBoth : c.call) : both ? c.putBoth : c.put;
          ctx.lineWidth = 1 + 2 * Math.max(0, Math.min(1, wl.weight));
          ctx.setLineDash(both ? [] : [6, 4]);
          ctx.beginPath();
          ctx.moveTo(x0, Math.round(y) + 0.5);
          ctx.lineTo(x1, Math.round(y) + 0.5);
          ctx.stroke();
          ctx.setLineDash([]);
          tag(y, `${call ? "C" : "P"}${wl.rank + 1} ${bookTag(wl.books)}`);
        }
      }
    });
  }
}

class GexPaneView {
  private r: GexRenderer;
  constructor(src: GexLevelsPrimitive) {
    this.r = new GexRenderer(src);
  }
  update() {}
  renderer() {
    return this.r;
  }
  zOrder() {
    return "bottom" as const;
  }
}

export class GexLevelsPrimitive {
  chart: IChartApi | null = null;
  series: ISeriesApi<"Candlestick"> | null = null;
  sessions: GexSession[] = [];
  times: readonly { time: number }[] = [];
  visible = true;
  /** Call walls and put walls drawn per book (rank < this). */
  walls = 2;
  flip = true;
  private views: GexPaneView[] = [new GexPaneView(this)];
  private requestUpdate?: () => void;

  attached(param: any) {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.requestUpdate?.();
  }

  detached() {
    this.chart = null;
    this.series = null;
  }

  updateAllViews() {}

  paneViews() {
    return this.views;
  }

  /** The step in force at bar time ``t`` — what the legend reads out, so a
   *  replay's readout is the books as of its tape, not the session's last. */
  stepAt(t: number): GexStep | null {
    let hit: GexStep | null = null;
    for (const s of this.sessions)
      for (const st of s.steps) if (st.from <= t && t < st.to) hit = st;
    return hit;
  }

  setSessions(sessions: GexSession[]) {
    this.sessions = sessions;
    this.requestUpdate?.();
  }

  /** The bars, ascending — set whenever they move. Cheap: a reference. */
  setBars(times: readonly { time: number }[]) {
    this.times = times;
    this.requestUpdate?.();
  }

  setParams(walls: number, flip: boolean) {
    this.walls = walls;
    this.flip = flip;
    this.requestUpdate?.();
  }

  setVisible(v: boolean) {
    this.visible = v;
    this.requestUpdate?.();
  }
}
