import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { CandlestickChart } from "../components/charts/CandlestickChart";
import { TimeframeControl, MINUTE_TFS } from "../components/charts/TimeframeControl";
import { DataTable } from "../components/DataTable";
import { KpiGrid } from "../components/KpiGrid";
import type { Card } from "../components/KpiCard";
import { useDraftDetail, useDraftSessionCharts } from "../hooks/useDrafts";
import type {
  ATRPoint,
  Bar,
  ChartMarker,
  EmaPoint,
  IbOverlay,
  ProfilePoint,
  RsiPoint,
  TradeRect,
  VwapPoint,
} from "../lib/chartTypes";
import type { DraftDetailData, DraftTrade } from "../lib/draftTypes";
import { fmt } from "../lib/format";

// One draft: header + guardrails, the materialized trade list, and the same
// continuous session tape the Interactions page draws — a window of the
// draft's sessions stitched into one candle stream, framed on the selected
// day, with the draft's entry/exit marks and hover rectangles on every loaded
// session. The tape and its timeframes (1m/3m/5m/500t) are the Interactions
// SessionChart pattern verbatim, markers and trade rects riding along where
// that chart carries touches and VA-snaps.

const CHECKLIST_STEPS: [keyof DraftDetailData["checklist"], string][] = [
  ["split_half", "split-half"],
  ["monthly_consistency", "monthly consistency"],
  ["engine_ab", "engine A/B"],
];

function hms(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type ChartTf = "1m" | "3m" | "5m" | "15m" | "500t";

// The day-chart sends raw UTC epoch seconds, but lightweight-charts renders a
// numeric time *as UTC* — shift every timestamp onto ET wall-clock so the axis
// matches the rest of the app. One midday probe per session is exact for its
// whole overnight+RTH span (DST flips ~2am on a weekend). Same helper as the
// Interactions session chart.
function etWallOffsetSec(day: string): number {
  const probeMs = Date.parse(`${day}T17:00:00Z`);
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
  const hour = p.hour === "24" ? "00" : p.hour;
  const wallMs = Date.UTC(+p.year, +p.month - 1, +p.day, +hour, +p.minute, +p.second);
  return Math.round((wallMs - probeMs) / 1000);
}

function pushShifted<T extends { time: number }>(dest: T[], rows: T[] | undefined, off: number) {
  if (!rows) return;
  for (const r of rows) dest.push({ ...r, time: r.time + off });
}

// Break sentinels for the RTH-anchored NY overlays: a non-finite point ends
// the line/ribbon at the session close instead of dragging it across the
// overnight to the next session's bell.
const vwapBreak = (time: number): VwapPoint => ({
  time,
  middle: NaN,
  upper1: NaN,
  lower1: NaN,
  upper2: NaN,
  lower2: NaN,
});
const profileBreak = (time: number): ProfilePoint => ({ time, poc: NaN, vah: NaN, val: NaN });

// How many of the draft's sessions the tape loads at once, centred on the
// selected day. Same knob as the Interactions session chart.
const MAX_SESSIONS = 15;

function sessionWindow(dayList: string[], selectedDay: string): string[] {
  if (dayList.length <= MAX_SESSIONS) return dayList;
  const idx = Math.max(0, dayList.indexOf(selectedDay));
  const half = Math.floor(MAX_SESSIONS / 2);
  const hi = Math.min(dayList.length, Math.max(0, idx - half) + MAX_SESSIONS);
  return dayList.slice(Math.max(0, hi - MAX_SESSIONS), hi);
}

function DraftSessionChart({
  slug,
  selectedDay,
  dayList,
  ticksPerBar,
  barMinutes,
}: {
  slug: string;
  selectedDay: string;
  dayList: string[];
  ticksPerBar?: number;
  barMinutes?: number;
}) {
  const windowDays = useMemo(() => sessionWindow(dayList, selectedDay), [dayList, selectedDay]);
  const results = useDraftSessionCharts(slug, windowDays, ticksPerBar, barMinutes);

  // useQueries returns a fresh array each render; key the heavy merge off a
  // signature and read the live values through refs, so it recomputes only
  // when the loaded data actually changes.
  const resultsRef = useRef(results);
  resultsRef.current = results;
  const windowRef = useRef(windowDays);
  windowRef.current = windowDays;

  const settled = results.every((r) => !r.isLoading);
  const sig = results.map((r, i) => `${windowDays[i]}:${r.status}:${r.dataUpdatedAt}`).join("|");

  // The stitched tape + per-day spans. Independent of `selectedDay`: within a
  // loaded window, refocusing must move the frame, not rebuild the bars.
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
    const markers: ChartMarker[] = [];
    const tradeRects: TradeRect[] = [];
    // One Initial Balance per session, shifted into its slot on the tape.
    const ibs: IbOverlay[] = [];
    const nyVwapSegs: VwapPoint[][] = [];
    const nyProfSegs: ProfilePoint[][] = [];
    const spans = new Map<string, { from: number; to: number }>();
    let tickSize: number | undefined;
    let pointValue: number | undefined;
    for (let i = 0; i < wd.length; i++) {
      const cd = rs[i]?.data;
      if (!cd || !cd.available || !cd.bars || cd.bars.length === 0) continue;
      const day = wd[i];
      const off = etWallOffsetSec(day);
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
      pushShifted(markers, cd.markers, off);
      if (cd.ib)
        ibs.push({
          ...cd.ib,
          start: cd.ib.start + off,
          formed: cd.ib.formed + off,
          end: cd.ib.end + off,
        });
      for (const t of cd.trades ?? []) {
        tradeRects.push({ ...t, entry_time: t.entry_time + off, exit_time: t.exit_time + off });
      }
      // Keep the RTH-anchored NY overlays as per-session segments; they are
      // stitched with break sentinels after the loop, once the whole tape
      // (and so every candidate break bar) exists.
      nyVwapSegs.push((cd.vwap_ny ?? []).map((r) => ({ ...r, time: r.time + off })));
      nyProfSegs.push((cd.profile_ny ?? []).map((r) => ({ ...r, time: r.time + off })));
      if (tickSize == null) tickSize = cd.tick_size;
      if (pointValue == null) pointValue = cd.point_value;
    }
    if (bars.length === 0) return null;

    // Join the RTH-anchored NY segments with a break sentinel between
    // sessions, landing it on the first drawn bar inside the gap so it adds no
    // empty column (fallback: one second past the close).
    const firstBarBetween = (a: number, b: number): number | null => {
      for (const bar of bars) {
        if (bar.time >= b) break;
        if (bar.time > a) return bar.time;
      }
      return null;
    };
    const joinNy = <T extends { time: number }>(segs: T[][], brk: (t: number) => T): T[] => {
      const out: T[] = [];
      for (const seg of segs) {
        if (seg.length === 0) continue;
        if (out.length > 0) {
          const prevEnd = out[out.length - 1].time;
          out.push(brk(firstBarBetween(prevEnd, seg[0].time) ?? prevEnd + 1));
        }
        for (const p of seg) out.push(p);
      }
      return out;
    };

    return {
      bars,
      vwapGlobex,
      vwapNy: joinNy(nyVwapSegs, vwapBreak),
      vwapWeekly,
      profileGlobex,
      profileNy: joinNy(nyProfSegs, profileBreak),
      ema9,
      ema20,
      ema50,
      ema200,
      rsi,
      atr,
      ibs,
      markers,
      tradeRects,
      spans,
      tickSize,
      pointValue,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled, sig]);

  const focusRange = useMemo(() => merged?.spans.get(selectedDay) ?? null, [merged, selectedDay]);

  if (!settled) return <div className="notice">Loading sessions…</div>;
  if (!merged) return <div className="notice">No cached ticks for these sessions.</div>;
  return (
    <CandlestickChart
      bars={merged.bars}
      vwapGlobex={merged.vwapGlobex}
      vwapNy={merged.vwapNy}
      vwapWeekly={merged.vwapWeekly}
      profileGlobex={merged.profileGlobex}
      profileNy={merged.profileNy}
      ema9={merged.ema9}
      ema20={merged.ema20}
      ema50={merged.ema50}
      ema200={merged.ema200}
      rsi={merged.rsi}
      atrPoints={merged.atr}
      ib={merged.ibs}
      markers={merged.markers}
      tradeRects={merged.tradeRects}
      initialTimeRange={focusRange}
      tickSize={merged.tickSize}
      pointValue={merged.pointValue}
      height={560}
    />
  );
}

function summaryCards(d: DraftDetailData): Card[] {
  const s = d.summary;
  const reasons = s.by_reason ?? {};
  const breakdown =
    Object.entries(reasons)
      .sort((a, b) => b[1] - a[1])
      .map(([k, n]) => `${n} ${k}`)
      .join(" / ") || "—";
  // A draft whose source never raced to a target has no win rate and no R —
  // reporting 0.0R as a headline would read as a flat result rather than as an
  // absent measurement, so those drafts show distance instead.
  const raced = s.targets + s.stops > 0;
  return [
    {
      label: "Trades",
      value: String(s.n_trades),
      sub: `${s.n_sessions} sessions · ${s.first_day ?? ""} → ${s.last_day ?? ""}`,
    },
    {
      label: raced ? "To target" : "Exits",
      value: raced && s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : "—",
      sub: breakdown,
    },
    raced
      ? {
          label: "Total R",
          value: `${fmt(s.total_r, false)}R`,
          tone: (s.total_r ?? 0) >= 0 ? "pos" : "neg",
          sub: `avg ${fmt(s.avg_r, false)}R per trade`,
        }
      : {
          label: "Total points",
          value: fmt(s.total_points, false),
          tone: s.total_points >= 0 ? "pos" : "neg",
          sub: "no stop in the source — R is not measurable",
        },
    {
      label: "Guardrails",
      value: `${s.overlapping_trades} overlap`,
      sub: `${s.n_skipped} of ${s.n_events} events skipped`,
    },
  ];
}

// Columns depend on what the source carried: a passthrough draft may label its
// trades (a setup name, a strategy arm) and may have no R to show.
function columnsFor(trades: DraftTrade[]): ColumnDef<DraftTrade, any>[] {
  const labelled = trades.some((t) => t.strategy);
  const stated = trades.some((t) => t.stated_result);
  const hasR = trades.some((t) => t.r_multiple !== 0);
  return [
    { accessorKey: "trade_no", header: "#" },
    { accessorKey: "day", header: "Session" },
    { id: "entry", header: "Entry (ET)", accessorFn: (t) => hms(t.entry_ts_utc) },
    { accessorKey: "direction", header: "Dir" },
    ...(labelled
      ? [{ accessorKey: "strategy", header: "Setup" } as ColumnDef<DraftTrade, any>]
      : []),
    {
      accessorKey: "exit_reason",
      header: "Exit",
      cell: (c) => {
        const v = c.getValue<string>();
        return (
          <span className={v === "target" ? "pos" : v === "stop" ? "neg" : "muted"}>{v}</span>
        );
      },
    },
    ...(hasR
      ? [
          {
            accessorKey: "r_multiple",
            header: "R",
            cell: (c) => {
              const v = c.getValue<number>();
              return <span className={v >= 0 ? "pos" : "neg"}>{v.toFixed(2)}</span>;
            },
          } as ColumnDef<DraftTrade, any>,
        ]
      : []),
    { accessorKey: "points", header: "Points", cell: (c) => c.getValue<number>().toFixed(2) },
    {
      accessorKey: "duration_s",
      header: "Held",
      cell: (c) => `${Math.round(c.getValue<number>() / 60)}m`,
    },
    // What the source itself said the trade did — the check against our
    // reconstruction, kept next to it rather than buried in the spec notes.
    ...(stated
      ? [
          {
            accessorKey: "stated_result",
            header: "Stated",
            cell: (c) => <span className="muted">{c.getValue<string>() ?? ""}</span>,
          } as ColumnDef<DraftTrade, any>,
        ]
      : []),
    { id: "rth", header: "RTH", accessorFn: (t) => (t.is_rth ? "yes" : "night") },
    { id: "ovl", header: "Overlap", accessorFn: (t) => (t.overlapped ? "⚠" : "") },
  ];
}

export function DraftDetail() {
  const { slug } = useParams<{ slug: string }>();
  const { data: draft, isLoading } = useDraftDetail(slug);

  const [day, setDay] = useState<string | null>(null);
  const [selectedTrade, setSelectedTrade] = useState<number | null>(null);
  const [chartTf, setChartTf] = useState<ChartTf>("1m");
  // The picker steps between trade days; the tape itself loads every covered
  // session in the span so adjacent days render and the weekly line is
  // continuous.
  const tradeDays = useMemo(
    () => [...new Set((draft?.trades ?? []).map((t) => t.day))],
    [draft?.trades],
  );
  const allDays = draft?.days?.length ? draft.days : tradeDays;
  const cols = useMemo(() => columnsFor(draft?.trades ?? []), [draft?.trades]);
  useEffect(() => {
    if (day == null && tradeDays.length) setDay(tradeDays[0]);
  }, [day, tradeDays]);

  if (isLoading) {
    return (
      <div className="notice">
        Materializing draft — first open walks the event table over the tick cache…
      </div>
    );
  }
  if (!draft) return <div className="panel muted">No draft named “{slug}”.</div>;

  const done = CHECKLIST_STEPS.filter(([k]) => draft.checklist[k]);
  const dayTrades = draft.trades.filter((t) => t.day === day);

  return (
    <div>
      <div className="research-doc-nav">
        <Link to="/drafts" className="muted">
          ← All drafts
        </Link>
      </div>

      <div className="panel" style={{ marginBottom: 12 }}>
        <h2 style={{ marginTop: 0 }}>{draft.name}</h2>
        <p style={{ fontSize: 13 }}>{draft.hypothesis}</p>
        <p className="muted" style={{ fontSize: 12 }}>
          {draft.symbol} · {draft.direction} ·{" "}
          {draft.race_sigma || draft.horizon_min ? (
            <>
              exits at level ± {draft.race_sigma}σ, time-out {draft.horizon_min}m · entry on the
              bar after the event ·{" "}
            </>
          ) : (
            // Passthrough: no race was run, so quoting σ/horizon/next-bar here
            // would describe machinery this draft never touched.
            <>rows passed through from their source, unraced ·{" "}</>
          )}
          {draft.source_doc && (
            <>
              from the <Link to={`/research/${draft.source_doc}`}>{draft.source_doc}</Link> study
            </>
          )}
        </p>
        {draft.notes && (
          <p className="muted" style={{ fontSize: 12 }}>
            {draft.notes}
          </p>
        )}
        <div className="notice" style={{ marginTop: 8 }}>
          <b>Not a backtest.</b> One contract, no fills, no slippage, no commission, overlapping
          entries counted rather than resolved. Promotion ladder:{" "}
          {CHECKLIST_STEPS.map(([k, label], i) => (
            <span key={k}>
              {i > 0 && " → "}
              <span className={draft.checklist[k] ? "pos" : "muted"}>
                {draft.checklist[k] ? "✓ " : ""}
                {label}
              </span>
            </span>
          ))}
          {done.length === CHECKLIST_STEPS.length ? " — ready to build." : ""}
        </div>
      </div>

      <div style={{ marginBottom: 12 }}>
        <KpiGrid cards={summaryCards(draft)} template="repeat(4, 1fr)" className="lab-kpi" />
      </div>

      <div className="panel draft-chart-sticky" style={{ marginBottom: 12 }}>
        <div
          style={{
            display: "flex",
            gap: 12,
            alignItems: "center",
            flexWrap: "wrap",
            marginBottom: 8,
          }}
        >
          <span className="muted" style={{ fontSize: 12 }}>
            Session
          </span>
          <select value={day ?? ""} onChange={(e) => setDay(e.target.value)}>
            {tradeDays.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <span className="muted" style={{ fontSize: 12, flex: 1 }}>
            {dayTrades.length} trade{dayTrades.length === 1 ? "" : "s"} this session · drag
            left/right to scan adjacent sessions, or click a row below.
          </span>
          <span style={{ display: "flex", flexShrink: 0 }}>
            <TimeframeControl
              value={chartTf}
              onChange={(tf) => setChartTf(tf as ChartTf)}
              options={[{ key: "500t", label: "500t" }, ...MINUTE_TFS]}
            />
          </span>
        </div>
        {day && slug && (
          <>
            <DraftSessionChart
              slug={slug}
              selectedDay={day}
              dayList={allDays}
              ticksPerBar={chartTf === "500t" ? 500 : undefined}
              barMinutes={
                chartTf === "3m" ? 3 : chartTf === "5m" ? 5 : chartTf === "15m" ? 15 : undefined
              }
            />
            <div className="section-cap" style={{ marginTop: 6 }}>
              A window of the draft's sessions stitched into one tape, framed on the selected day —
              blue arrow = entry (the bar after the event), orange arrow = exit, shaded rectangle =
              the holding period; hover it for the numbers. The orange bands are the weekly VWAP
              the events were measured against; per-session anchors (NY/Globex VWAP, profiles)
              reset at each session boundary by design.
            </div>
          </>
        )}
      </div>

      <div className="panel">
        <DataTable
          data={draft.trades}
          columns={cols}
          rowKey={(t) => t.trade_no}
          selectedKey={selectedTrade}
          onRowClick={(t) => {
            setDay(t.day);
            setSelectedTrade(t.trade_no);
          }}
          initialSort={[{ id: "trade_no", desc: false }]}
        />
      </div>
    </div>
  );
}
