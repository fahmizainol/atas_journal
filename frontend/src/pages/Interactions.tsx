import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../components/DataTable";
import { BadgeInput } from "../components/BadgeInput";
import { InitialBalancePanel } from "../components/InitialBalancePanel";
import { WeeklyVwapPanel } from "../components/WeeklyVwapPanel";
import { CandlestickChart } from "../components/charts/CandlestickChart";
import { TimeframeControl, MINUTE_TFS } from "../components/charts/TimeframeControl";
import {
  useInteractions,
  useInteractionStats,
  useInteractionCoverage,
  useInteractionRunChart,
  useInteractionRuns,
  useIbSessionWidths,
} from "../hooks/useInteractions";
import { useAllDayNotes, useSaveDayNote } from "../hooks/useCalendar";
import { useFilters } from "../hooks/useFilters";
import { useFiltersData } from "../hooks/useMeta";
import { useRegimeRange, useVolRegimeRange } from "../hooks/useRegime";
import {
  CLASS_LABEL,
  type RegimeClass,
  type VolRegimeDay,
  type VolRegimeLabel,
} from "../lib/regimeTypes";
import { ibPalette, regimePalette } from "../theme";
import type {
  AggRow,
  BandContextRow,
  BandOccupancyRow,
  IbSessionWidth,
  IbWidthBucket,
  InteractionParams,
  SavedRun,
  Touch,
  VaSnap,
  VaSnapAggRow,
  VaSnapContRow,
} from "../lib/interactionTypes";
import type {
  ATRPoint,
  Bar,
  EmaPoint,
  IbOverlay,
  ProfilePoint,
  RsiPoint,
  VwapPoint,
} from "../lib/chartTypes";
import { fmt, fmtInt, fmtPct } from "../lib/format";

// The interactions day-chart sends bar/level/event timestamps as raw UTC epoch
// seconds, but lightweight-charts renders a numeric time *as UTC* — so the axis
// would read UTC, hours off from the Strategies-tab charts (which shift to New
// York wall-clock server-side via api.charts_data._epoch_local). Match them by
// adding the ET offset to every timestamp on the way into the chart. The offset
// is constant across a single session (DST only flips ~2am on a weekend), so one
// probe at midday ET for `day` is exact for the whole overnight+RTH span. Doing
// it here — the one place bars (live endpoint) and events (cached snapshot) meet
// — keeps them aligned without touching the API or the on-disk run cache.
function etWallOffsetSec(day: string): number {
  const probeMs = Date.parse(`${day}T17:00:00Z`); // ~noon ET, safely inside the day
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const p: Record<string, string> = {};
  for (const part of dtf.formatToParts(new Date(probeMs))) p[part.type] = part.value;
  // Intl can emit hour "24" at midnight; normalise so Date.UTC doesn't roll over.
  const hour = p.hour === "24" ? "00" : p.hour;
  const wallMs = Date.UTC(+p.year, +p.month - 1, +p.day, +hour, +p.minute, +p.second);
  return Math.round((wallMs - probeMs) / 1000);
}

// Concatenate one session's overlay rows onto the running tape, each timestamp
// reprojected onto ET wall-clock by that session's own offset.
function pushShifted<T extends { time: number }>(dest: T[], rows: T[] | undefined, off: number) {
  if (!rows) return;
  for (const r of rows) dest.push({ ...r, time: r.time + off });
}

// Break sentinels: a point whose values are non-finite terminates a per-session
// anchor at the session boundary. The band-fill primitive splits its ribbon at
// NaN, and the line renderer paints the segment leaving the last real point
// transparent (lightweight-charts lines have no native gaps — whitespace items
// are dropped from a line series and the neighbours join straight across, see
// CandlestickChart.gappedLineData). Used only for the RTH-anchored NY overlays
// (VWAP + developing profile), which run 9:30→16:00 while the drawn tape
// continues; the Globex/weekly overlays span the whole stream and re-anchor
// cleanly at 18:00.
const vwapBreak = (time: number): VwapPoint => ({
  time,
  middle: NaN,
  upper1: NaN,
  lower1: NaN,
  upper2: NaN,
  lower2: NaN,
});
const profileBreak = (time: number): ProfilePoint => ({
  time,
  poc: NaN,
  vah: NaN,
  val: NaN,
});

// How many sessions the continuous chart loads at once, centred on the selected
// day — up to 7 before and 7 after. Keeps the opening burst small; a run longer
// than this loads a window that recentres (fetching newly-entered days, which are
// cached) as you open days nearer its ends.
const MAX_SESSIONS = 15;

// The window of covered sessions to load around `selectedDay`: up to MAX_SESSIONS
// of them, clamped so the window never runs off either end of the list. Returns
// `dayList` itself (same reference) when the whole run fits, so opening different
// days inside a small run doesn't re-key the queries.
function sessionWindow(dayList: string[], selectedDay: string): string[] {
  if (dayList.length <= MAX_SESSIONS) return dayList;
  const idx = Math.max(0, dayList.indexOf(selectedDay));
  const half = Math.floor(MAX_SESSIONS / 2);
  const hi = Math.min(dayList.length, Math.max(0, idx - half) + MAX_SESSIONS);
  return dayList.slice(Math.max(0, hi - MAX_SESSIONS), hi);
}

// The session drill-down, as one continuous tape. Instead of a single day it
// loads a window of the run's sessions (each an independent, cached day-chart)
// and stitches the available ones into one candle stream — overnight+RTH gaps
// collapse natively, so dragging left/right walks into the adjacent session. It
// opens framed on `selectedDay`; the tables below stay pinned to that day.
//
// The per-session anchored overlays (NY/Globex/weekly VWAP, developing profiles,
// EMAs) reset at every session boundary by design — each re-anchors at its own
// bell/18:00, so on the tape they read as a sawtooth. The Initial Balance overlay
// is dropped here: the chart draws one IB, and a many-session tape has one per
// day. Touch / VA-snap marks are drawn for every loaded session.
function SessionChart({
  symbol,
  selectedDay,
  dayList,
  binSize,
  sources,
  ticksPerBar,
  barMinutes,
  touches,
  vaSnaps,
}: {
  symbol: string;
  selectedDay: string;
  dayList: string[];
  binSize?: number;
  sources?: string[];
  ticksPerBar?: number;
  barMinutes?: number;
  touches: Touch[];
  vaSnaps: VaSnap[];
}) {
  const windowDays = useMemo(() => sessionWindow(dayList, selectedDay), [dayList, selectedDay]);
  const results = useInteractionRunChart(
    symbol,
    windowDays,
    binSize,
    sources,
    ticksPerBar,
    barMinutes,
  );

  // useQueries returns a fresh array each render, so the heavy merge is keyed off
  // a signature (which days, their cache versions, whether all resolved) and reads
  // the live results/window through refs — recomputing only when the data changes,
  // never on an unrelated re-render.
  const resultsRef = useRef(results);
  resultsRef.current = results;
  const windowRef = useRef(windowDays);
  windowRef.current = windowDays;

  const settled = results.every((r) => !r.isLoading);
  const sig = results.map((r, i) => `${windowDays[i]}:${r.status}:${r.dataUpdatedAt}`).join("|");

  // The stitched tape + per-day time spans. Independent of `selectedDay` on
  // purpose: within one loaded window, switching the focused day must not change
  // `bars` (that would rebuild the chart and lose the zoom) — only the frame moves.
  const merged = useMemo(() => {
    if (!settled) return null;
    const wd = windowRef.current;
    const rs = resultsRef.current;
    const bars: Bar[] = [];
    const vwapGlobex: VwapPoint[] = [];
    const vwapWeekly: VwapPoint[] = [];
    const profileGlobex: ProfilePoint[] = [];
    const ema9: EmaPoint[] = [];
    const ema20: EmaPoint[] = [];
    const ema50: EmaPoint[] = [];
    const ema200: EmaPoint[] = [];
    const rsi: RsiPoint[] = [];
    const atr: ATRPoint[] = [];
    // One Initial Balance per session, each shifted into its slot on the tape so
    // its bell→close segments stay inside that session (see CandlestickChart).
    const ibs: IbOverlay[] = [];
    // The NY VWAP + developing profile are RTH-anchored, so each session is one
    // contiguous 9:30→16:00 run. They are collected as per-session segments and
    // joined into one series *after* the whole tape is built (see `joinNy`
    // below), so the break sentinel between two sessions can land on the next
    // session's first overnight bar when this one has no post-hour bar of its
    // own — the case that used to let the line drag across to the next open.
    const nyVwapSegs: VwapPoint[][] = [];
    const nyProfSegs: ProfilePoint[][] = [];
    const spans = new Map<string, { from: number; to: number }>();
    const offsets = new Map<string, number>();
    let tickSize: number | undefined;
    let pointValue: number | undefined;
    for (let i = 0; i < wd.length; i++) {
      const cd = rs[i]?.data;
      if (!cd || !cd.available || !cd.bars || cd.bars.length === 0) continue;
      const day = wd[i];
      const off = etWallOffsetSec(day);
      offsets.set(day, off);
      const db = cd.bars.map((b) => ({ ...b, time: b.time + off }));
      spans.set(day, { from: db[0].time, to: db[db.length - 1].time });
      for (const b of db) bars.push(b);
      pushShifted(vwapGlobex, cd.vwap_globex, off);
      pushShifted(vwapWeekly, cd.vwap_weekly, off);
      pushShifted(profileGlobex, cd.profile_globex, off);
      pushShifted(ema9, cd.ema9, off);
      pushShifted(ema20, cd.ema20, off);
      pushShifted(ema50, cd.ema50, off);
      pushShifted(ema200, cd.ema200, off);
      pushShifted(rsi, cd.rsi, off);
      pushShifted(atr, cd.atr_points, off);
      if (cd.ib)
        ibs.push({
          ...cd.ib,
          start: cd.ib.start + off,
          formed: cd.ib.formed + off,
          end: cd.ib.end + off,
        });
      // Keep the RTH-anchored NY overlays as per-session segments; they are
      // stitched with break sentinels after the loop, once the whole tape (and
      // so every candidate break bar) exists.
      nyVwapSegs.push((cd.vwap_ny ?? []).map((r) => ({ ...r, time: r.time + off })));
      nyProfSegs.push((cd.profile_ny ?? []).map((r) => ({ ...r, time: r.time + off })));
      if (tickSize == null) tickSize = cd.tick_size;
      if (pointValue == null) pointValue = cd.point_value;
    }
    if (bars.length === 0) return null;

    // Join the per-session NY segments into one series, inserting a break
    // sentinel (NaN → whitespace gap in the renderer) between adjacent sessions
    // so the RTH-anchored line stops at the close instead of dragging across the
    // overnight to the next session's 9:30. The break lands on the first drawn
    // bar strictly inside the gap — this session's 16:00 post-hour bar when it's
    // on disk, otherwise the *next* session's first overnight bar — so it adds no
    // empty column. Only when the gap holds no bar at all (no post and no next-day
    // overnight) does it fall back to one second past the close, a negligible
    // off-grid gap. `bars` is globally sorted (sessions are chronological and
    // don't overlap), so the scan can stop once it passes the gap.
    const firstBarBetween = (a: number, b: number): number | null => {
      for (const bar of bars) {
        if (bar.time >= b) break;
        if (bar.time > a) return bar.time;
      }
      return null;
    };
    const joinNy = <T extends { time: number }>(
      segs: T[][],
      brk: (t: number) => T,
    ): T[] => {
      const out: T[] = [];
      for (const seg of segs) {
        if (seg.length === 0) continue;
        if (out.length > 0) {
          const prevEnd = out[out.length - 1].time;
          const bt = firstBarBetween(prevEnd, seg[0].time);
          out.push(brk(bt ?? prevEnd + 1));
        }
        for (const p of seg) out.push(p);
      }
      return out;
    };
    const vwapNy = joinNy(nyVwapSegs, vwapBreak);
    const profileNy = joinNy(nyProfSegs, profileBreak);

    return {
      bars,
      vwapGlobex,
      vwapNy,
      vwapWeekly,
      profileGlobex,
      profileNy,
      ema9,
      ema20,
      ema50,
      ema200,
      rsi,
      atr,
      ibs,
      touches: touches
        .filter((t) => offsets.has(t.day))
        .map((t) => ({ ...t, ts: t.ts + offsets.get(t.day)! })),
      vaSnaps: vaSnaps
        .filter((s) => offsets.has(s.day))
        .map((s) => ({ ...s, ts: s.ts + offsets.get(s.day)! })),
      spans,
      tickSize,
      pointValue,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled, sig, touches, vaSnaps]);

  // A refetch — switching timeframe, or focusing a day whose window pulls in a
  // session that isn't in the query cache yet — must not swap the chart out for
  // a notice: unmounting CandlestickChart throws away everything that lives
  // inside it (the anchored VWAP, any fixed-range profiles, the ruler, the
  // zoom). So hold the last resolved tape on screen while the next one loads;
  // the chart stays mounted and only its data changes underneath it.
  const lastTape = useRef<typeof merged>(null);
  if (settled) lastTape.current = merged;
  const tape = merged ?? lastTape.current;

  // The span to open on: the selected day's own bars. Split from the tape so a
  // new selection re-frames in place (see CandlestickChart.initialTimeRange)
  // rather than rebuilding it. Null while a just-picked day is still showing
  // over the previous tape — the frame follows as soon as its bars land.
  const focusRange = useMemo(
    () => tape?.spans.get(selectedDay) ?? null,
    [tape, selectedDay],
  );

  if (!tape)
    return (
      <div className="notice">
        {settled ? "No cached ticks for these sessions." : "Loading sessions…"}
      </div>
    );
  return (
    <div style={{ position: "relative" }}>
      {/* Not .chart-tool any more: that class is a 28px icon square in the left
          rail now, and this is a sentence. A legend row is the same chip and
          still the right shape for it — parked on the right, the edge the rail
          left free. */}
      {!settled && (
        <div
          className="chart-legend-row"
          style={{ position: "absolute", top: 8, right: 12, zIndex: 3, cursor: "default" }}
        >
          Loading sessions…
        </div>
      )}
      <CandlestickChart
        bars={tape.bars}
        vwapGlobex={tape.vwapGlobex}
        vwapNy={tape.vwapNy}
        vwapWeekly={tape.vwapWeekly}
        profileGlobex={tape.profileGlobex}
        profileNy={tape.profileNy}
        ema9={tape.ema9}
        ema20={tape.ema20}
        ema50={tape.ema50}
        ema200={tape.ema200}
        rsi={tape.rsi}
        atrPoints={tape.atr}
        ib={tape.ibs}
        touches={tape.touches}
        vaSnaps={tape.vaSnaps}
        initialTimeRange={focusRange}
        tickSize={tape.tickSize}
        pointValue={tape.pointValue}
        height={560}
      />
    </div>
  );
}

// The Interactions Lab — a research bench over the cached tick sessions,
// deliberately separate from the Edges tab (which is trade-derived / live). Here
// nothing depends on trades: you pick a range, run the study, and read how price
// meets the developing NY + Globex profile levels. The output is evidence, not a
// verdict — promoting a cut to a filter/signal stays a manual, downstream step.

const ALL_SOURCES = [
  { key: "ny", label: "NY profile" },
  { key: "globex", label: "Globex profile" },
  { key: "vwap_bands", label: "VWAP bands" },
  { key: "session_refs", label: "Session refs" },
];

// The interactions API sends rates as fractions (0.714), but fmtPct expects an
// already-scaled percentage (71.4) like the rest of the API sends — scale here.
const pct = (v: number | null) => (v == null ? "—" : fmtPct(v * 100));
const num = (v: number | null) => (v == null ? "—" : fmt(v, false));

// The per-session day-type from the (shared, per-day) regime cache — the same
// class the Strategies tab colours its calendar by. Shown here so a session's
// touch/band behaviour can be read against the regime it happened in. `dot` is
// the compact swatch for the Sessions chips; the full pill labels the drill-down.
function RegimeBadge({ klass, dot }: { klass: RegimeClass; dot?: boolean }) {
  const bg = regimePalette.klass[klass];
  const label = CLASS_LABEL[klass];
  if (dot) {
    return (
      <span
        title={`Regime: ${label}`}
        style={{ width: 8, height: 8, borderRadius: 2, background: bg, flexShrink: 0 }}
      />
    );
  }
  return (
    <span
      style={{
        background: bg,
        borderRadius: 4,
        padding: "1px 8px",
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

// The vol-clock label for a session: which tercile of the trailing-60-session
// daily-ATR percentile the day opened in, with the ATR itself alongside — the
// tercile alone can't tell you whether "hot" meant 400 points or 900. Both are
// causal (ATR through the prior session), so this is what was knowable at the
// bell, not a description of what the day went on to do. See vol-clock.md.
function VolRegimeCell({ vol }: { vol: VolRegimeDay | undefined }) {
  if (!vol || !vol.label || vol.atr == null) return <span className="muted">—</span>;
  const label = vol.label as VolRegimeLabel;
  return (
    <span
      style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}
      title={
        `Daily ATR(14) through the prior session: ${vol.atr.toFixed(0)} pts` +
        (vol.pctl == null ? "" : ` · ${(vol.pctl * 100).toFixed(0)}th pctl of the last 60 sessions`) +
        (vol.tr_pts == null ? "" : ` · realised range ${vol.tr_pts.toFixed(0)} pts`)
      }
    >
      <span
        style={{
          background: regimePalette.vol[label],
          borderRadius: 4,
          padding: "1px 8px",
          fontSize: 12,
          fontWeight: 600,
        }}
      >
        {label}
      </span>
      <span className="muted">{vol.atr.toFixed(0)}</span>
    </span>
  );
}

const WIDTH_LABEL: Record<IbWidthBucket, string> = {
  narrow: "narrow",
  mid: "mid",
  wide: "wide",
};

// How wide the session's first hour was, in ADR units. Like the vol-clock cell
// beside it this is causal — the IB completes at 10:30 and the ADR denominator
// runs through the *prior* 14 sessions — but the two axes are orthogonal
// (corr −0.05), which is why both columns are here rather than one.
//
// Reading it (vol-clock §10c): wide leans trend day (60% trend-class vs 47%
// narrow), narrow leans balance/churn (29% vs 12% balance). That is recognition,
// not prediction — post-IB expansion is width-flat (~0.4×ADR of new range comes
// regardless), so this is day character at a glance and nothing more. The built
// `ib_width` gate stays off; no edge claim is attached to this column.
function IbWidthCell({ ib, edges }: {
  ib: IbSessionWidth | undefined;
  edges: [number, number] | undefined;
}) {
  if (!ib) return <span className="muted">—</span>;
  const bucket = ib.width;
  return (
    <span
      style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}
      title={
        `Initial Balance (09:30–10:30 ET) range: ${ib.ib_range.toFixed(0)} pts` +
        (ib.ib_vs_adr == null || ib.adr14 == null
          ? " · no ADR(14) yet — the run's first ~14 sessions have no denominator"
          : ` · ${ib.ib_vs_adr.toFixed(2)}× ADR(14) of ${ib.adr14.toFixed(0)} pts` +
            (edges ? ` · terciles cut at ${edges[0]}/${edges[1]}× ADR` : "")) +
        " · day character, not a forecast (vol-clock §10c)"
      }
    >
      {bucket ? (
        <span
          style={{
            background: ibPalette.width[bucket],
            borderRadius: 4,
            padding: "1px 8px",
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          {WIDTH_LABEL[bucket]}
        </span>
      ) : (
        <span className="muted" style={{ fontSize: 12 }}>—</span>
      )}
      <span className="muted">{ib.ib_range.toFixed(0)}</span>
    </span>
  );
}

// Inline per-day tag editor for the Sessions table. Reuses the existing
// day-notes store (the same tags the calendar's Day journal writes), keyed by
// ISO date; each edit PATCHes the note+tags and the bulk map refetches, so the
// badges are always the server's copy — no local mirror. `note` is threaded
// through so saving tags never clobbers a day's written note. stopPropagation so
// clicking inside the editor doesn't also toggle the row's session selection.
function DayTagCell({ day, note, tags, vocab }: {
  day: string;
  note: string;
  tags: string[];
  vocab: string[];
}) {
  const save = useSaveDayNote(day);
  return (
    <div className="session-tag-cell" onClick={(e) => e.stopPropagation()}>
      <BadgeInput
        value={tags}
        onChange={(next) => save.mutate({ note, tags: next })}
        suggestions={vocab}
        placeholder="tag…"
      />
    </div>
  );
}

// One row of the Sessions table: a covered day, its touch/snap counts, its
// (shared) regime class, and its day-note tags joined in from the bulk map.
interface SessionRow {
  day: string;
  n_touches: number;
  n_snaps: number;
  klass: RegimeClass | undefined;
  /** The vol-clock row for the day, when the ATR had warmed up by then. */
  vol: VolRegimeDay | undefined;
  /** IB width for the day, when the pinned snapshot covers it. */
  ib: IbSessionWidth | undefined;
  note: string;
  tags: string[];
}

const aggColumns: ColumnDef<AggRow, any>[] = [
  { accessorKey: "label", header: "Bucket", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "reject_rate", header: "Reject %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "avg_mfe", header: "Avg MFE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_mae", header: "Avg MAE", cell: (c) => num(c.getValue() as number | null) },
];

const vasnapColumns: ColumnDef<VaSnapAggRow, any>[] = [
  { accessorKey: "label", header: "Snap", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "n_trivial", header: "Triv", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "revert_rate_30", header: "≤30m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "revert_rate_60", header: "≤60m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "revert_rate", header: "By close %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "avg_move", header: "Avg move", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_adverse", header: "Avg adverse", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_dist", header: "Avg dist", cell: (c) => num(c.getValue() as number | null) },
];

const bandContextColumns: ColumnDef<BandContextRow, any>[] = [
  { accessorKey: "label", header: "Cut", cell: (c) => String(c.getValue()) },
  {
    accessorKey: "n",
    header: "N",
    cell: (c) => {
      const v = c.getValue() as number | null;
      return v == null ? <span className="muted">—</span> : fmtInt(v);
    },
  },
  { accessorKey: "reject_rate", header: "Reject % (30m)", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "med_mfe", header: "Med MFE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "med_mae", header: "Med MAE", cell: (c) => num(c.getValue() as number | null) },
  {
    accessorKey: "ratio",
    header: "MFE/MAE",
    cell: (c) => {
      const v = c.getValue() as number | null;
      if (v == null) return <span className="muted">—</span>;
      const cls = v >= 1.3 ? "pos" : v <= 0.8 ? "neg" : "muted";
      return <span className={cls}>{num(v)}</span>;
    },
  },
];

const bandOccupancyColumns: ColumnDef<BandOccupancyRow, any>[] = [
  {
    accessorKey: "label",
    header: "Band",
    cell: (c) => {
      const v = String(c.getValue());
      return v === "Total" ? <strong>{v}</strong> : v;
    },
  },
  { accessorKey: "avg_min", header: "Avg min/session", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "pct", header: "% of session", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "minutes", header: "Total min", cell: (c) => fmtInt(c.getValue() as number) },
];

// Append a summary row so the table foots to the whole session: minutes and
// avg/session sum across the bands, and pct is 100% by construction (the bands
// partition every classified minute). Empty in → empty out (no phantom total).
function withBandTotals(rows: BandOccupancyRow[]): BandOccupancyRow[] {
  if (rows.length === 0) return rows;
  const minutes = rows.reduce((s, r) => s + r.minutes, 0);
  const avg_min = rows.reduce((s, r) => s + (r.avg_min ?? 0), 0);
  return [...rows, { label: "Total", minutes, pct: 1, avg_min: Math.round(avg_min * 10) / 10 }];
}

// The regime-class mix across the run's sessions — how many trend-up / balance /
// … days the window held. `klass: null` marks the grand-total row.
interface RegimeMixRow {
  klass: RegimeClass | null;
  days: number;
  pct: number | null;
}

const REGIME_ORDER: RegimeClass[] = [
  "trend_up", "trend_down", "balance", "parked", "mixed", "unknown",
];

const regimeMixColumns: ColumnDef<RegimeMixRow, any>[] = [
  {
    accessorKey: "klass",
    header: "Regime",
    cell: (c) => {
      const k = c.getValue() as RegimeClass | null;
      if (k == null) return <strong>Total</strong>;
      return (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <RegimeBadge klass={k} dot />
          {CLASS_LABEL[k]}
        </span>
      );
    },
  },
  { accessorKey: "days", header: "Days", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "pct", header: "% of sessions", cell: (c) => pct(c.getValue() as number | null) },
];

// Regime mix cross-tabbed by weekday: rows Mon..Fri (+ any weekend session that
// slipped in), one column per regime class present in the run, plus a total.
// `weekday: null` marks the grand-total row.
interface WeekdayRow {
  weekday: string | null;
  counts: Partial<Record<RegimeClass, number>>;
  total: number;
}

const WEEKDAY_LABEL: Record<number, string> = {
  1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 0: "Sun",
};
// Mon-first ordering; weekend last if it ever appears.
const WEEKDAY_ORDER = [1, 2, 3, 4, 5, 6, 0];

// Parse "YYYY-MM-DD" as UTC midnight so the weekday can't drift with the local
// zone, then read the UTC day-of-week.
function weekdayIdx(day: string): number {
  return new Date(`${day}T00:00:00Z`).getUTCDay();
}

function weekdayColumns(classes: RegimeClass[]): ColumnDef<WeekdayRow, any>[] {
  const cols: ColumnDef<WeekdayRow, any>[] = [
    {
      id: "weekday",
      header: "Day",
      accessorFn: (r) => r.weekday,
      cell: (c) => {
        const v = c.getValue() as string | null;
        return v == null ? <strong>Total</strong> : v;
      },
    },
  ];
  for (const k of classes) {
    cols.push({
      id: k,
      header: CLASS_LABEL[k],
      accessorFn: (r) => r.counts[k] ?? 0,
      cell: (c) => {
        const n = c.getValue() as number;
        return n === 0 ? <span className="muted">—</span> : fmtInt(n);
      },
    });
  }
  cols.push({
    id: "total",
    header: "Total",
    accessorFn: (r) => r.total,
    cell: (c) => <strong>{fmtInt(c.getValue() as number)}</strong>,
  });
  return cols;
}

const vasnapContColumns: ColumnDef<VaSnapContRow, any>[] = [
  { accessorKey: "label", header: "Snap", cell: (c) => String(c.getValue()) },
  { accessorKey: "n", header: "N", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "hold_rate_30", header: "Hold ≤30m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "hold_rate_60", header: "Hold ≤60m %", cell: (c) => pct(c.getValue() as number | null) },
  { accessorKey: "avg_run_30", header: "Run 30m", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_run_60", header: "Run 60m", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "avg_stop_dist", header: "Stop dist", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "rr_60", header: "R:R 60m", cell: (c) => num(c.getValue() as number | null) },
];

function AggTable({ title, data, columns, caption, keepOrder }: {
  title: string;
  data: any[];
  columns: ColumnDef<any, any>[];
  caption?: string;
  // Preserve the server's row order (band position, cut → benchmark) instead
  // of sorting by N — the band-context tables read top-to-bottom as a story.
  keepOrder?: boolean;
}) {
  return (
    <div className="panel">
      <div className="section-cap">{title}</div>
      {data.length > 0 ? (
        <DataTable
          data={data}
          columns={columns}
          initialSort={keepOrder ? [] : [{ id: "n", desc: true }]}
        />
      ) : (
        <div className="muted">No data.</div>
      )}
      {caption && <div className="section-cap" style={{ marginTop: 6 }}>{caption}</div>}
    </div>
  );
}

const outcomeSpan = (v: string) => {
  const cls = v === "reject" ? "pos" : v === "accept" ? "neg" : "muted";
  return <span className={cls}>{v}</span>;
};

const touchColumns: ColumnDef<Touch, any>[] = [
  { accessorKey: "hhmm", header: "Time", cell: (c) => String(c.getValue()) },
  { accessorKey: "label", header: "Level", cell: (c) => String(c.getValue()) },
  { accessorKey: "zone_px", header: "Price", cell: (c) => fmt(c.getValue() as number, false) },
  { accessorKey: "approach", header: "Appr", cell: (c) => String(c.getValue()) },
  { accessorKey: "nth_touch", header: "Nth", cell: (c) => fmtInt(c.getValue() as number) },
  {
    accessorKey: "closed_by",
    header: "Gap by",
    cell: (c) => {
      const v = String(c.getValue());
      return v === "level" ? <span className="neg">{v}</span> : v === "price" ? v : <span className="muted">{v}</span>;
    },
  },
  { accessorKey: "level_age_min", header: "Age", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "outcome", header: "Outcome", cell: (c) => outcomeSpan(String(c.getValue())) },
  {
    id: "o30",
    header: "30m",
    accessorFn: (r) => r.outcomes?.["30"]?.outcome ?? "—",
    cell: (c) => outcomeSpan(String(c.getValue())),
  },
  {
    id: "o60",
    header: "60m",
    accessorFn: (r) => r.outcomes?.["60"]?.outcome ?? "—",
    cell: (c) => outcomeSpan(String(c.getValue())),
  },
  { accessorKey: "mfe", header: "MFE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "mae", header: "MAE", cell: (c) => num(c.getValue() as number | null) },
  { accessorKey: "n_sources", header: "Src", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "signed_delta", header: "Δ", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "level_slope", header: "Slope", cell: (c) => String(c.getValue()) },
];

const snapColumns: ColumnDef<VaSnap, any>[] = [
  { accessorKey: "hhmm", header: "Time", cell: (c) => String(c.getValue()) },
  {
    id: "level",
    header: "Level",
    accessorFn: (r) => `${r.source === "ny" ? "NY" : "Globex"} ${r.level_type}`,
    cell: (c) => String(c.getValue()),
  },
  { accessorKey: "snap_dir", header: "Direction", cell: (c) => String(c.getValue()) },
  {
    accessorKey: "snap_class",
    header: "Class",
    cell: (c) => {
      const v = String(c.getValue());
      return v === "node_flip" ? <span className="neg">{v}</span> : <span className="muted">{v}</span>;
    },
  },
  { accessorKey: "level_jump_pts", header: "Jump", cell: (c) => fmt(c.getValue() as number, false) },
  {
    accessorKey: "co_snaps",
    header: "Co",
    cell: (c) => {
      const v = (c.getValue() as number) ?? 0;
      return v === 0 ? <span className="muted">—</span> : fmtInt(v);
    },
  },
  { accessorKey: "excursion_bars_before", header: "Excursion (bars)", cell: (c) => fmtInt(c.getValue() as number) },
  { accessorKey: "band_at_snap", header: "Band", cell: (c) => String(c.getValue() ?? "—") },
  {
    accessorKey: "reverted",
    header: "Reverted",
    cell: (c) => (c.getValue() ? <span className="pos">yes</span> : <span className="muted">no</span>),
  },
  {
    accessorKey: "revert_min",
    header: "→VWAP min",
    cell: (c) => {
      const v = c.getValue() as number | null | undefined;
      return v == null ? <span className="muted">—</span> : fmtInt(v);
    },
  },
  { accessorKey: "vwap_dist_pts", header: "Dist", cell: (c) => num((c.getValue() as number) ?? null) },
  { accessorKey: "revert_move", header: "Move", cell: (c) => num((c.getValue() as number) ?? null) },
  { accessorKey: "adverse_move", header: "Adverse", cell: (c) => num((c.getValue() as number) ?? null) },
];

export function Interactions() {
  const [symbol, setSymbol] = useState("NQ");
  const [start, setStart] = useState("2025-08-01");
  const [end, setEnd] = useState("2025-08-31");
  const [binSize, setBinSize] = useState("");
  const [sources, setSources] = useState<string[]>(["ny", "globex", "session_refs"]);
  const [windowMin, setWindowMin] = useState("10");
  const [committed, setCommitted] = useState<InteractionParams | null>(null);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  // Which half of the run's output is on screen. Sessions is the working view
  // (drill into a day, tag it), so it's the default; the aggregate stats — and
  // the standalone IB / Weekly-VWAP studies — live behind the Stats tab so the
  // Sessions grid is one click away rather than a scroll past every table.
  const [tab, setTab] = useState<"stats" | "sessions">("sessions");
  // Aggregate stats are computed on demand: the run auto-loads the lean events
  // payload (chart + Sessions grid) and the ~14 aggregate tables are fetched only
  // when the user asks, so a refresh doesn't wait on stats it may not look at.
  // Reset to false on every new run so a fresh range never auto-pulls them.
  const [showStats, setShowStats] = useState(false);
  // Chart timeframe for the session drill-down: "1m"/"5m" = time bars of that many
  // minutes, "500t" = 500-tick bars (the strategies' native grid). The events are
  // unchanged; only the candle grid they overlay swaps.
  const [chartTf, setChartTf] = useState<"1m" | "3m" | "5m" | "15m" | "500t">("1m");

  const coverage = useInteractionCoverage(symbol, start, end);
  const run = useInteractions(committed);
  const stats = useInteractionStats(committed, showStats);
  const savedRuns = useInteractionRuns();
  const queryClient = useQueryClient();

  // The shared per-day regime, over the committed run's window — same cache the
  // Strategies tab reads, so no recompute. Range payload carries the class per
  // day (no ribbon). day → class lookup for the Sessions chips and drill-down.
  const regimeRange = useRegimeRange(
    committed?.symbol ?? null,
    committed?.start ?? null,
    committed?.end ?? null,
  );
  const regimeByDay = useMemo(() => {
    const m = new Map<string, RegimeClass>();
    for (const d of regimeRange.data?.days ?? []) m.set(d.date, d.class);
    return m;
  }, [regimeRange.data]);

  // The vol clock over the same window — an axis orthogonal to the day-type: the
  // class says what shape the day traded, this says how fast its clock ran.
  const volRange = useVolRegimeRange(
    committed?.symbol ?? null,
    committed?.start ?? null,
    committed?.end ?? null,
  );
  const volByDay = useMemo(() => {
    const m = new Map<string, VolRegimeDay>();
    for (const d of volRange.data?.days ?? []) m.set(d.date, d);
    return m;
  }, [volRange.data]);

  // IB width over the same window — a third day-character axis, orthogonal to
  // both of the above and knowable at 10:30. Served from the widest saved IB
  // snapshot, so the terciles mean the same thing in every window; days the
  // snapshot doesn't reach are simply absent.
  const ibWidths = useIbSessionWidths(
    committed?.symbol ?? null,
    committed?.start ?? null,
    committed?.end ?? null,
  );

  // Per-day notes/tags (the same store the calendar's Day journal writes) and the
  // global tag vocabulary for autocomplete — both drive the Sessions table's
  // editable Tags column.
  const dayNotes = useAllDayNotes();
  const { scope } = useFilters();
  const { data: filtersData } = useFiltersData(scope);
  const tagVocab = filtersData?.tags ?? [];

  // A fresh compute writes a new snapshot server-side — refresh the list so it
  // shows up without a reload.
  useEffect(() => {
    if (run.isSuccess) {
      queryClient.invalidateQueries({ queryKey: ["interactions", "saved-runs"] });
    }
  }, [run.isSuccess, run.dataUpdatedAt, queryClient]);

  const toggleSource = (key: string) =>
    setSources((prev) => (prev.includes(key) ? prev.filter((s) => s !== key) : [...prev, key]));

  const onRun = () => {
    setSelectedDay(null);
    setShowStats(false);
    setCommitted({
      symbol,
      start,
      end,
      bin_size: binSize ? Number(binSize) : undefined,
      sources,
      outcome_window_min: windowMin ? Number(windowMin) : undefined,
    });
  };

  // Reopen a snapshot: sync the config bar to its (fully resolved) params and
  // commit them verbatim — same config hash, so the server answers from disk.
  const loadRun = (r: SavedRun) => {
    const c = r.config;
    setSymbol(c.symbol);
    setStart(c.start);
    setEnd(c.end);
    setBinSize(String(c.bin_size));
    setWindowMin(String(c.outcome_window_min));
    setSources(c.sources);
    setSelectedDay(null);
    setShowStats(false);
    setCommitted({
      symbol: c.symbol,
      start: c.start,
      end: c.end,
      bin_size: c.bin_size,
      va_pct: c.va_pct,
      sources: c.sources,
      outcome_window_min: c.outcome_window_min,
      zone_cluster_pts: c.zone_cluster_pts,
    });
  };

  // On first open, auto-load the saved run with the widest date window so the
  // page lands on the fullest study instead of an empty "pick a range" state.
  // Fires once: only while nothing is committed yet, so it never fights a run
  // the user explicitly kicked off or a chip they clicked.
  const didAutoLoad = useRef(false);
  useEffect(() => {
    if (didAutoLoad.current || committed) return;
    const runs = savedRuns.data;
    if (!runs || runs.length === 0) return;
    const span = (r: SavedRun) =>
      Date.parse(r.config.end) - Date.parse(r.config.start);
    const widest = runs.reduce((a, b) => (span(b) > span(a) ? b : a));
    didAutoLoad.current = true;
    loadRun(widest);
  }, [savedRuns.data, committed]);

  const data = run.data;
  const agg = stats.data?.aggregates;
  const dayList = useMemo(
    () => (data ? Object.keys(data.day_index).sort() : []),
    [data],
  );
  // Touches come from the on-demand stats fetch (the raw list is the payload's
  // heaviest block), so this is empty until "Compute stats" is clicked.
  const dayTouches = useMemo(
    () => (agg && selectedDay ? (stats.data?.touches ?? []).filter((t) => t.day === selectedDay) : []),
    [agg, stats.data, selectedDay],
  );
  const daySnaps = useMemo(
    () => (data && selectedDay ? data.events.va_snaps.filter((s) => s.day === selectedDay) : []),
    [data, selectedDay],
  );
  // Tally the regime class over the sessions this run actually covered (dayList),
  // so the mix matches the Sessions chips rather than the raw calendar range.
  const regimeMix = useMemo<RegimeMixRow[]>(() => {
    const counts = new Map<RegimeClass, number>();
    for (const d of dayList) {
      const k = regimeByDay.get(d);
      if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    const total = [...counts.values()].reduce((a, b) => a + b, 0);
    if (total === 0) return [];
    const rows: RegimeMixRow[] = REGIME_ORDER.filter((k) => counts.has(k)).map((k) => ({
      klass: k,
      days: counts.get(k)!,
      pct: counts.get(k)! / total,
    }));
    rows.push({ klass: null, days: total, pct: 1 });
    return rows;
  }, [dayList, regimeByDay]);
  // The same tally cross-cut by weekday. `classes` are the regime classes that
  // actually appear (drives the columns); `rows` are Mon..Fri with per-class
  // counts and a grand-total row.
  const weekdayMix = useMemo<{ rows: WeekdayRow[]; classes: RegimeClass[] }>(() => {
    // grid[weekdayIdx][class] = count
    const grid = new Map<number, Map<RegimeClass, number>>();
    const seenClasses = new Set<RegimeClass>();
    for (const d of dayList) {
      const k = regimeByDay.get(d);
      if (!k) continue;
      const wd = weekdayIdx(d);
      if (!grid.has(wd)) grid.set(wd, new Map());
      const row = grid.get(wd)!;
      row.set(k, (row.get(k) ?? 0) + 1);
      seenClasses.add(k);
    }
    if (seenClasses.size === 0) return { rows: [], classes: [] };
    const classes = REGIME_ORDER.filter((k) => seenClasses.has(k));
    const totals = new Map<RegimeClass, number>();
    let grand = 0;
    const rows: WeekdayRow[] = [];
    for (const wd of WEEKDAY_ORDER) {
      const row = grid.get(wd);
      if (!row) continue;
      const counts: Partial<Record<RegimeClass, number>> = {};
      let total = 0;
      for (const k of classes) {
        const n = row.get(k) ?? 0;
        if (n) counts[k] = n;
        total += n;
        totals.set(k, (totals.get(k) ?? 0) + n);
      }
      grand += total;
      rows.push({ weekday: WEEKDAY_LABEL[wd], counts, total });
    }
    rows.push({ weekday: null, counts: Object.fromEntries(totals) as WeekdayRow["counts"], total: grand });
    return { rows, classes };
  }, [dayList, regimeByDay]);
  const weekdayCols = useMemo(() => weekdayColumns(weekdayMix.classes), [weekdayMix.classes]);

  // The Sessions table: one row per covered day, counts + regime + day tags. The
  // tags are joined in from the bulk day-notes map so the whole grid renders from
  // a single request rather than one per row.
  const sessionRows = useMemo<SessionRow[]>(() => {
    if (!data) return [];
    return dayList.map((d) => ({
      day: d,
      n_touches: data.day_index[d].n_touches,
      n_snaps: data.day_index[d].n_snaps,
      klass: regimeByDay.get(d),
      vol: volByDay.get(d),
      ib: ibWidths.data?.days[d],
      note: dayNotes.data?.[d]?.note ?? "",
      tags: dayNotes.data?.[d]?.tags ?? [],
    }));
  }, [data, dayList, regimeByDay, volByDay, ibWidths.data, dayNotes.data]);

  const sessionColumns = useMemo<ColumnDef<SessionRow, any>[]>(
    () => [
      {
        id: "day",
        header: "Session",
        accessorFn: (r) => r.day,
        cell: (c) => {
          const r = c.row.original;
          return (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              {r.klass && <RegimeBadge klass={r.klass} dot />}
              {r.day}
            </span>
          );
        },
      },
      {
        id: "regime",
        header: "Regime",
        accessorFn: (r) => (r.klass ? CLASS_LABEL[r.klass] : "—"),
        cell: (c) => {
          const k = c.row.original.klass;
          return k ? CLASS_LABEL[k] : <span className="muted">—</span>;
        },
      },
      {
        id: "vol",
        header: "Vol regime",
        // Sorted on the percentile, not the raw ATR: the percentile is what the
        // tercile is cut from, so sorting by it never lands a "hot" row above a
        // "quiet" one the way an ATR sort across a changing baseline can.
        accessorFn: (r) => r.vol?.pctl ?? -1,
        cell: (c) => <VolRegimeCell vol={c.row.original.vol} />,
      },
      {
        id: "ib_width",
        header: "IB width",
        // Sorted on the ADR ratio rather than the points, for the same reason the
        // vol column sorts on the percentile: points are not comparable across a
        // window whose baseline range doubles, and the ratio is what the chip is
        // cut from. Uncovered days sort to the bottom.
        accessorFn: (r) => r.ib?.ib_vs_adr ?? -1,
        cell: (c) => (
          <IbWidthCell ib={c.row.original.ib} edges={ibWidths.data?.tercile_edges} />
        ),
      },
      { accessorKey: "n_touches", header: "Touches", cell: (c) => fmtInt(c.getValue() as number) },
      { accessorKey: "n_snaps", header: "Snaps", cell: (c) => fmtInt(c.getValue() as number) },
      {
        id: "tags",
        header: "Tags",
        // Editable inline (unlike the by-trade table's read-only cell) — tagging a
        // day is the whole point, and there's no separate detail slot for it.
        cell: (c) => {
          const r = c.row.original;
          return <DayTagCell day={r.day} note={r.note} tags={r.tags} vocab={tagVocab} />;
        },
      },
    ],
    [tagVocab, ibWidths.data?.tercile_edges],
  );

  return (
    <div>
      <div className="section-title">Interactions Lab</div>

      {/* config bar */}
      <div className="panel" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
        <label>Symbol<br /><input value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ width: 70 }} /></label>
        <label>Start<br /><input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>End<br /><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>Bin (pts)<br /><input value={binSize} placeholder="tick" onChange={(e) => setBinSize(e.target.value)} style={{ width: 60 }} /></label>
        <label>Outcome window (min)<br /><input value={windowMin} onChange={(e) => setWindowMin(e.target.value)} style={{ width: 60 }} /></label>
        <div>
          Sources<br />
          {ALL_SOURCES.map((s) => (
            <label key={s.key} style={{ marginRight: 10, fontWeight: 400 }}>
              <input type="checkbox" checked={sources.includes(s.key)} onChange={() => toggleSource(s.key)} /> {s.label}
            </label>
          ))}
        </div>
        <button type="button" onClick={onRun} disabled={run.isFetching}>
          {run.isFetching ? "Running…" : "Run tracking"}
        </button>
      </div>

      {/* saved snapshots — reopening one is a disk read, not a recompute */}
      {savedRuns.data && savedRuns.data.length > 0 && (
        <div className="panel">
          <div className="section-cap">Saved runs</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {savedRuns.data.map((r) => (
              <button
                key={r.run_id}
                type="button"
                className="chip"
                title={`bin ${r.config.bin_size} · VA ${r.config.va_pct} · window ${r.config.outcome_window_min}m · cluster ${r.config.zone_cluster_pts} pts · ${r.coverage.ran_days}/${r.coverage.requested_days} sessions`}
                onClick={() => loadRun(r)}
                disabled={run.isFetching}
              >
                {r.config.symbol} · {r.config.start} → {r.config.end} ·{" "}
                {r.config.sources.join("+")} · {r.n_touches}t/{r.n_snaps}s
              </button>
            ))}
          </div>
        </div>
      )}

      {run.isError && <div className="notice">Run failed: {String((run.error as Error)?.message)}</div>}
      {!committed && <div className="notice">Pick a range and hit Run tracking.</div>}
      {run.isFetching && <div className="notice">Computing interactions…</div>}

      {/* Stats ⇄ Sessions. Sessions is the working view (drill into a day, tag
          it); the aggregate tables and the standalone IB / Weekly-VWAP studies
          live under Stats so reaching Sessions is a click, not a scroll. */}
      <div className="tabs" style={{ marginTop: 16 }}>
        <button
          type="button"
          className={tab === "stats" ? "active" : undefined}
          onClick={() => setTab("stats")}
        >
          Stats
        </button>
        <button
          type="button"
          className={tab === "sessions" ? "active" : undefined}
          onClick={() => setTab("sessions")}
        >
          Sessions
        </button>
      </div>

      {tab === "stats" && (
        <>
          {/* cache coverage strip */}
          {coverage.data && (
            <div className="panel">
              <div className="section-cap">
                Cache coverage — {coverage.data.days.filter((d) => d.rth).length}/{coverage.data.days.length} sessions have ticks
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
                {coverage.data.days.map((d) => (
                  <span
                    key={d.date}
                    title={`${d.date}${d.rth ? " · rth" : ""}${d.on ? " · overnight" : ""}`}
                    style={{
                      width: 14,
                      height: 14,
                      borderRadius: 2,
                      background: d.rth ? (d.on ? "var(--pos, #3fb950)" : "#6ea8fe") : "#30363d",
                      opacity: d.rth ? 1 : 0.4,
                      outline: d.date === selectedDay ? "2px solid #f0f6fc" : "none",
                    }}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Initial Balance & ORB — independent of the touch study; shares the
              symbol/date inputs above but commits its own (much cheaper) run. */}
          <InitialBalancePanel symbol={symbol} start={start} end={end} />

          {/* Weekly VWAP envelope — same contract as the IB panel: shares the
              symbol/date inputs above, commits its own (cheap) run. */}
          <WeeklyVwapPanel symbol={symbol} start={start} end={end} />

          {data && !run.isFetching && (
            <>
              <div className="section-cap" style={{ margin: "10px 0" }}>
                {data.coverage.ran_days}/{data.coverage.requested_days} sessions ·{" "}
                {Object.values(data.day_index).reduce((s, d) => s + d.n_touches, 0)} touches ·{" "}
                {data.events.va_snaps.length} VA-snaps
                {data.coverage.skipped.length > 0 && ` · skipped ${data.coverage.skipped.length} (no ticks)`}
              </div>

              {/* Regime mix is derived client-side from the (shared) per-day
                  regime cache, not the run's aggregates — so it needs no stats
                  fetch and shows straight away. */}
              <div className="grid-2">
                <AggTable
                  title="Regime mix"
                  data={regimeMix}
                  columns={regimeMixColumns}
                  keepOrder
                  caption="Day-type breakdown across the run's sessions — the same per-day regime class the Strategies tab colours its calendar by."
                />
                <AggTable
                  title="Regime by weekday"
                  data={weekdayMix.rows}
                  columns={weekdayCols}
                  keepOrder
                  caption="The same day-types cross-cut by weekday (Mon–Fri) — does the window lean trendy early in the week, balanced on Fridays, etc."
                />
              </div>

              {/* Touch / VA-snap aggregates — computed on demand so the run
                  loads the chart first. Button until asked; then the ~14 tables. */}
              {!agg && (
                <div className="panel" style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <button type="button" onClick={() => setShowStats(true)} disabled={stats.isFetching}>
                    {stats.isFetching ? "Computing…" : "Compute aggregate stats"}
                  </button>
                  <span className="muted">
                    {stats.isError
                      ? `Stats failed: ${String((stats.error as Error)?.message)}`
                      : "Touch & VA-snap aggregates load on request — the run loads the chart first."}
                  </span>
                </div>
              )}

              {agg && (
                <>
              {/* aggregates */}
          <AggTable
            title="Outcome by horizon"
            data={agg.by_horizon ?? []}
            columns={aggColumns}
            caption="The same touches rescored at fixed 10/30/60-minute windows — how much of the outcome is just the clock."
          />
          <div className="grid-2">
            <AggTable
              title="Upper-band pullback (the cut)"
              data={agg.upper_band_pullback ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="Pullback-from-above onto a developing POC/VAH while price holds NY VWAP +1σ..+2σ, scored at 30m with medians. Judge every row against the null baseline: beat its reject rate AND show MFE/MAE asymmetry, or it's noise."
            />
            <AggTable
              title="By band context"
              data={agg.by_band_context ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="All touches grouped by where price sat in the NY VWAP bands, per approach side — the same level means different things in different bands."
            />
          </div>
          <div className="grid-2">
            <AggTable
              title="Who closed the gap"
              data={agg.who_closed_gap ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="Over the last 5 bars before each touch: did price move to the level, or the level to price? A falling band chased by price scores as a fresh 1st touch while price tested nothing — if level-led rows sit at the null, they dilute every touch table."
            />
            <AggTable
              title="Acceptance decay"
              data={agg.acceptance_decay ?? []}
              columns={bandContextColumns}
              keepOrder
              caption="The same touches by how many times the zone was already tested today. Read Med MFE down the rows — a level touched again and again with shrinking excursion has become fair price, even while it still 'rejects' by the 3-pt threshold."
            />
          </div>
          <div className="grid-2">
            <AggTable
              title="Time in band · NY VWAP"
              data={withBandTotals(agg.band_occupancy ?? [])}
              columns={bandOccupancyColumns}
              keepOrder
              caption="How long price spent in each NY VWAP band, tallied per RTH minute. Rows read top-to-bottom like the chart (>+2σ down to <-2σ). Avg min/session is the comparable read — the total scales with the date range."
            />
            <AggTable
              title="Time in band · Globex (ON) VWAP"
              data={withBandTotals(agg.band_occupancy_gx ?? [])}
              columns={bandOccupancyColumns}
              keepOrder
              caption="The same per-minute tally against the overnight-anchored Globex VWAP bands. Empty unless 'globex' is among the sources."
            />
          </div>
          <div className="grid-2">
            <div>
              <AggTable title="By level source" data={agg.by_source} columns={aggColumns} />
              <AggTable title="By nth-touch" data={agg.by_nth_touch} columns={aggColumns} />
            </div>
            <div>
              <AggTable
                title="Confluence lift"
                data={agg.confluence_lift}
                columns={aggColumns}
                caption="Reject rate of a lone level vs. one stacked with another source."
              />
              <AggTable
                title="VA-snap → reversion (fade)"
                data={agg.vasnap_reversion}
                columns={vasnapColumns}
                caption="After a level snapped across price, did price revert to NY VWAP. Rates exclude trivial snaps (already at/through VWAP at the snap bar)."
              />
              <AggTable
                title="VA-snap → continuation (flip)"
                data={agg.vasnap_continuation ?? []}
                columns={vasnapContColumns}
                caption="The same snaps traded with the snap direction, stop = a close through NY VWAP. Hold % = never stopped within the window; run = excursion before the stop."
              />
            </div>
          </div>
          <div className="grid-2">
            <AggTable
              title="VA-snap by class"
              data={agg.vasnap_by_class ?? []}
              columns={vasnapColumns}
              caption="The reversion trade cut by snap class: boundary creep (jump < 20 pts) vs node-flip (the value area re-seating on a different volume node). A 4-pt creep and a 195-pt flip are different events."
            />
            <AggTable
              title="VA-snap confluence"
              data={agg.vasnap_confluence ?? []}
              columns={vasnapColumns}
              caption="Lone snaps vs same-minute multi-level snaps. Two boundaries re-seating in the same minute is one value-migration event, not two independent signals."
            />
          </div>
                </>
              )}
            </>
          )}
        </>
      )}

      {tab === "sessions" && data && !run.isFetching && (
        <>
          {/* per-day drill-down — a row per session; click to open it below.
              Add tags per day inline (stored with the calendar's day notes). */}
          <div className="panel">
            <div className="section-cap">
              Sessions — click a row to drill in; tag the day inline
              {/* Provenance for the IB-width column: it is a slice of one saved
                  snapshot, so a dash means "outside that window", not "no data". */}
              {ibWidths.data?.source && (
                <span className="muted">
                  {" · IB width from "}
                  {ibWidths.data.source.start} → {ibWidths.data.source.end}
                  {` (${ibWidths.data.source.ib_minutes}m IB, terciles pinned at `}
                  {ibWidths.data.tercile_edges[0]}/{ibWidths.data.tercile_edges[1]}× ADR)
                </span>
              )}
            </div>
            <div className="table-scroll compact-table" style={{ maxHeight: 420, overflow: "auto" }}>
              <DataTable
                data={sessionRows}
                columns={sessionColumns}
                rowKey={(r) => r.day}
                selectedKey={selectedDay}
                onRowClick={(r) => setSelectedDay(r.day === selectedDay ? null : r.day)}
                initialSort={[{ id: "day", desc: false }]}
              />
            </div>
          </div>

          {selectedDay && (
            <>
              <div className="panel">
                <div
                  className="section-cap"
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}
                >
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    {regimeByDay.get(selectedDay) && (
                      <RegimeBadge klass={regimeByDay.get(selectedDay)!} />
                    )}
                    <span>
                      {selectedDay} — session · drag left/right to scan adjacent sessions · triangles
                      = VA-snaps{agg
                        ? " · green/red dots = touch reject/accept (ringed = 2+ sources stacked)"
                        : " · compute stats to mark touches"}
                    </span>
                  </span>
                  <span style={{ display: "flex", flexShrink: 0 }}>
                    <TimeframeControl
                      value={chartTf}
                      onChange={(tf) => setChartTf(tf as typeof chartTf)}
                      options={[{ key: "500t", label: "500t" }, ...MINUTE_TFS]}
                    />
                  </span>
                </div>
                {/* Tag the open session right here, so tagging doesn't mean
                    scrolling back up to its row in the Sessions table. Same
                    day-notes store as that column — edits sync both ways. */}
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <span className="muted" style={{ fontSize: 12, flexShrink: 0 }}>Tags</span>
                  <div style={{ flex: 1, maxWidth: 520 }}>
                    <DayTagCell
                      day={selectedDay}
                      note={dayNotes.data?.[selectedDay]?.note ?? ""}
                      tags={dayNotes.data?.[selectedDay]?.tags ?? []}
                      vocab={tagVocab}
                    />
                  </div>
                </div>
                <SessionChart
                  symbol={data.symbol}
                  selectedDay={selectedDay}
                  dayList={dayList}
                  binSize={committed?.bin_size}
                  sources={committed?.sources}
                  ticksPerBar={chartTf === "500t" ? 500 : undefined}
                  barMinutes={
                    chartTf === "3m" ? 3 : chartTf === "5m" ? 5 : chartTf === "15m" ? 15 : undefined
                  }
                  touches={stats.data?.touches ?? []}
                  vaSnaps={data.events.va_snaps}
                />
              </div>
              <div className="panel">
                <div className="section-cap">{selectedDay} — VA-snaps</div>
                {daySnaps.length > 0 ? (
                  <DataTable data={daySnaps} columns={snapColumns} initialSort={[{ id: "hhmm", desc: false }]} />
                ) : (
                  <div className="muted">No VA-snaps this session.</div>
                )}
              </div>
              <div className="panel">
                <div className="section-cap">{selectedDay} — touches</div>
                {agg ? (
                  <DataTable data={dayTouches} columns={touchColumns} initialSort={[{ id: "hhmm", desc: false }]} />
                ) : (
                  <div className="muted">
                    Touches load with the aggregate stats — hit “Compute aggregate stats” on the Stats
                    tab to show them here and mark them on the chart.
                  </div>
                )}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
