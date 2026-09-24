// USD economic releases (journal.econ_calendar) as full-height vertical lines,
// with a tag at the top of the pane naming what printed.
//
// The line sits on the bar that *contains* the release, not the nearest one: an
// 08:30 print on a 5m chart belongs to the 08:30 bar, and on tick bars (whose
// times are irregular) "nearest" can land on the bar before the print. Releases
// past the last bar — the rest of the day in a replay — are extrapolated at the
// chart's own bar spacing, so a scheduled 10:00 is visible from 09:40.
//
// The actual is withheld until the tape has reached the release: a replay that
// reads "CPI 0.4% vs 0.2%" at 08:00 has told you the next half hour.

import type { IChartApi, Logical, Time } from "lightweight-charts";
import { ink } from "../../theme";

export interface EconEvent {
  id: number;
  /** Wall-clock epoch seconds in the chart's zone — the bars' own axis. */
  time: number;
  utc: number;
  name: string;
  impact: string;
  timed: boolean;
  actual: string;
  forecast: string;
  previous: string;
  revision: string;
}

interface Group {
  time: number;
  impact: "high" | "medium" | "low";
  events: EconEvent[];
}

const RANK = { high: 3, medium: 2, low: 1 } as const;

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

function tagText(g: Group, reached: boolean): string {
  const first = g.events[0];
  let s = first.name;
  if (reached && first.actual) s += ` ${first.actual}`;
  if (first.forecast) s += reached && first.actual ? ` (f ${first.forecast})` : ` f ${first.forecast}`;
  if (g.events.length > 1) s += `  +${g.events.length - 1}`;
  return s;
}

class EconRenderer {
  constructor(private src: EconEventPrimitive) {}

  draw(target: any) {
    const { chart, groups, times, visible } = this.src;
    if (!visible || !chart || times.length === 0 || groups.length === 0) return;
    const c = ink().econEvents;
    const ts = chart.timeScale();
    const lastT = times[times.length - 1].time;
    // Median spacing of the tail, for extrapolating past the last bar.
    const tail = times.slice(-21).map((b) => b.time);
    const gaps = tail.slice(1).map((t, i) => t - tail[i]).sort((a, b) => a - b);
    const step = gaps.length ? Math.max(1, gaps[gaps.length >> 1]) : 60;

    target.useMediaCoordinateSpace((scope: any) => {
      const ctx: CanvasRenderingContext2D = scope.context;
      const h: number = scope.mediaSize.height;
      const w: number = scope.mediaSize.width;
      ctx.font = "10px Inter, sans-serif";
      ctx.textBaseline = "top";
      let lastTagRight = -Infinity;
      let row = 0;
      for (const g of groups) {
        let x: number | null;
        if (g.time <= lastT) {
          const i = lastLE(times, g.time);
          if (i < 0) continue;
          x = ts.timeToCoordinate(times[i].time as Time);
        } else {
          const i = times.length - 1 + (g.time - lastT) / step;
          x = ts.logicalToCoordinate(i as Logical);
        }
        if (x == null || x < -1 || x > w + 1) continue;
        const col = c[g.impact];
        ctx.strokeStyle = col;
        ctx.lineWidth = g.impact === "high" ? 1.5 : 1;
        ctx.setLineDash(g.time > lastT ? [2, 4] : [5, 4]);
        ctx.beginPath();
        ctx.moveTo(Math.round(x) + 0.5, 0);
        ctx.lineTo(Math.round(x) + 0.5, h);
        ctx.stroke();
        ctx.setLineDash([]);

        // Tags stagger down two rows when they would overlap the previous one.
        const text = tagText(g, g.time <= lastT);
        const tw = ctx.measureText(text).width + 8;
        row = x + 3 < lastTagRight ? (row + 1) % 3 : 0;
        const y = 4 + row * 16;
        ctx.fillStyle = col;
        ctx.fillRect(x + 3, y, tw, 14);
        ctx.fillStyle = c.label;
        ctx.fillText(text, x + 7, y + 2);
        lastTagRight = Math.max(lastTagRight, x + 3 + tw);
      }
    });
  }
}

class EconPaneView {
  private r: EconRenderer;
  constructor(src: EconEventPrimitive) {
    this.r = new EconRenderer(src);
  }
  update() {}
  renderer() {
    return this.r;
  }
  zOrder() {
    return "bottom" as const;
  }
}

export class EconEventPrimitive {
  chart: IChartApi | null = null;
  groups: Group[] = [];
  times: readonly { time: number }[] = [];
  visible = true;
  private views: EconPaneView[] = [new EconPaneView(this)];
  private requestUpdate?: () => void;

  attached(param: any) {
    this.chart = param.chart;
    this.requestUpdate = param.requestUpdate;
    this.requestUpdate?.();
  }

  detached() {
    this.chart = null;
  }

  updateAllViews() {}

  paneViews() {
    return this.views;
  }

  /** Untimed rows ("All Day", "Tentative") are dropped — a midnight line would
   *  claim a print time the calendar never gave. Same-minute prints (CPI m/m,
   *  y/y, core) collapse into one line tagged with the loudest. */
  setEvents(events: EconEvent[]) {
    const by = new Map<number, Group>();
    for (const e of events) {
      if (!e.timed) continue;
      const imp = (e.impact in RANK ? e.impact : "low") as Group["impact"];
      const g = by.get(e.time);
      if (!g) by.set(e.time, { time: e.time, impact: imp, events: [e] });
      else {
        g.events.push(e);
        if (RANK[imp] > RANK[g.impact]) g.impact = imp;
      }
    }
    this.groups = [...by.values()].sort((a, b) => a.time - b.time);
    const rank = (i: string) => RANK[i as keyof typeof RANK] ?? 0;
    for (const g of this.groups) g.events.sort((a, b) => rank(b.impact) - rank(a.impact));
    this.requestUpdate?.();
  }

  /** The bars, ascending — set whenever they move. Cheap: a reference. */
  setBars(times: readonly { time: number }[]) {
    this.times = times;
    this.requestUpdate?.();
  }

  setVisible(v: boolean) {
    this.visible = v;
    this.requestUpdate?.();
  }
}
